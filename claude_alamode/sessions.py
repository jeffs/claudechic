"""Session management - loading and listing Claude Code sessions."""

import json
import os
import re
from pathlib import Path

import aiofiles


def is_valid_uuid(s: str) -> bool:
    """Check if string is a valid UUID (not agent-* internal sessions)."""
    return bool(
        re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", s, re.I
        )
    )


def get_project_sessions_dir(cwd: Path | None = None) -> Path | None:
    """Get the sessions directory for a project.

    Claude stores sessions in ~/.claude/projects/-path-to-project
    with dashes instead of slashes.

    Args:
        cwd: Project directory. If None, uses current working directory.
    """
    if cwd is None:
        cwd = Path.cwd().absolute()
    else:
        cwd = cwd.absolute()
    project_key = str(cwd).replace("/", "-")
    sessions_dir = Path.home() / ".claude/projects" / project_key
    return sessions_dir if sessions_dir.exists() else None


async def get_recent_sessions(
    limit: int = 20, search: str = "", cwd: Path | None = None
) -> list[tuple[str, str, float, int]]:
    """Get recent sessions from a project.

    Args:
        limit: Maximum number of sessions to return
        search: Optional text to filter sessions by content
        cwd: Project directory. If None, uses current working directory.

    Returns:
        List of (session_id, preview, mtime, msg_count) tuples,
        sorted by modification time descending.
    """
    sessions = []
    sessions_dir = get_project_sessions_dir(cwd)
    if not sessions_dir:
        return sessions

    search_lower = search.lower()
    for f in sessions_dir.glob("*.jsonl"):
        if not is_valid_uuid(f.stem):
            continue
        if f.stat().st_size == 0:
            continue
        try:
            preview = ""
            msg_count = 0
            matches_search = not search
            async with aiofiles.open(f) as fh:
                async for line in fh:
                    d = json.loads(line)
                    if d.get("type") == "user" and not d.get("isMeta"):
                        content = d.get("message", {}).get("content", "")
                        if isinstance(content, str) and not content.startswith("<"):
                            msg_count += 1
                            if not preview:
                                text = content.replace("\n", " ")
                                preview = text[:200] + "…" if len(text) > 200 else text
                            if search and search_lower in content.lower():
                                matches_search = True
            if preview and msg_count > 0 and matches_search:
                sessions.append((f.stem, preview, f.stat().st_mtime, msg_count))
        except (json.JSONDecodeError, IOError):
            continue

    sessions.sort(key=lambda x: x[2], reverse=True)
    return sessions[:limit]


async def load_session_messages(
    session_id: str, limit: int = 10, cwd: Path | None = None
) -> list[dict]:
    """Load recent messages from a session file.

    Args:
        session_id: UUID of the session
        limit: Maximum number of messages to return
        cwd: Project directory. If None, uses current working directory.

    Returns:
        List of message dicts with 'type' key:
        - user: {'type': 'user', 'content': str}
        - assistant: {'type': 'assistant', 'content': str}
        - tool_use: {'type': 'tool_use', 'name': str, 'input': dict, 'id': str}
    """
    sessions_dir = get_project_sessions_dir(cwd)
    if not sessions_dir:
        return []

    session_file = sessions_dir / f"{session_id}.jsonl"
    if not session_file.exists():
        return []

    messages = []
    try:
        async with aiofiles.open(session_file) as f:
            async for line in f:
                d = json.loads(line)
                if d.get("type") == "user":
                    content = d.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        if content.strip().startswith("/"):
                            continue
                        if "<command-name>/" in content:
                            continue
                        if "<local-command-stdout>" in content:
                            continue
                        if "<local-command-caveat>" in content:
                            continue
                        messages.append({"type": "user", "content": content})
                elif d.get("type") == "assistant":
                    msg = d.get("message", {})
                    content_blocks = msg.get("content", [])
                    for block in content_blocks:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text = block.get("text", "")
                                if text.strip():
                                    messages.append({"type": "assistant", "content": text})
                            elif block.get("type") == "tool_use":
                                messages.append({
                                    "type": "tool_use",
                                    "name": block.get("name", "?"),
                                    "input": block.get("input", {}),
                                    "id": block.get("id", ""),
                                })
    except (json.JSONDecodeError, IOError):
        pass

    return messages[-limit:]


async def get_context_from_session(
    session_id: str, cwd: Path | None = None, agent_id: str | None = None
) -> int | None:
    """Get context token usage from session file.

    Reads the session jsonl and finds the last usage block, then sums:
    input_tokens + cache_creation_input_tokens + cache_read_input_tokens

    Args:
        session_id: UUID of the main session
        cwd: Project directory. If None, uses current working directory.
        agent_id: Optional short agent ID (e.g., "a35f34b") for sub-agent sessions

    Returns:
        Total input context tokens, or None if not found.
    """
    sessions_dir = get_project_sessions_dir(cwd)
    if not sessions_dir:
        return None

    if agent_id:
        session_file = sessions_dir / f"agent-{agent_id}.jsonl"
    else:
        session_file = sessions_dir / f"{session_id}.jsonl"

    if not session_file.exists():
        return None

    # Read from end of file to find last usage entry efficiently
    # This avoids reading entire file which can be megabytes for long sessions
    try:
        file_size = os.path.getsize(session_file)
        if file_size == 0:
            return None

        # Read last chunk (usually enough to find last usage)
        chunk_size = min(32768, file_size)  # 32KB chunk
        async with aiofiles.open(session_file, mode='rb') as f:
            await f.seek(file_size - chunk_size)
            chunk = await f.read()

        # Split into lines, process in reverse
        lines = chunk.split(b'\n')
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if "message" in data and isinstance(data["message"], dict):
                    usage = data["message"].get("usage")
                    if usage:
                        return (
                            usage.get("input_tokens", 0)
                            + usage.get("cache_creation_input_tokens", 0)
                            + usage.get("cache_read_input_tokens", 0)
                        )
            except json.JSONDecodeError:
                continue
    except (IOError, OSError):
        return None

    return None
