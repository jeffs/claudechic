"""Session management - loading and listing Claude Code sessions."""

import json
import re
from pathlib import Path


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


def get_recent_sessions(
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
        # Skip non-UUID sessions (agent-* are internal)
        if not is_valid_uuid(f.stem):
            continue
        if f.stat().st_size == 0:
            continue
        try:
            preview = ""
            msg_count = 0
            matches_search = not search  # If no search, all match
            with open(f) as fh:
                for line in fh:
                    d = json.loads(line)
                    if d.get("type") == "user" and not d.get("isMeta"):
                        content = d.get("message", {}).get("content", "")
                        if isinstance(content, str) and not content.startswith("<"):
                            msg_count += 1
                            if not preview:
                                preview = content[:50].replace("\n", " ")
                            if search and search_lower in content.lower():
                                matches_search = True
            if preview and msg_count > 0 and matches_search:
                sessions.append((f.stem, preview, f.stat().st_mtime, msg_count))
        except (json.JSONDecodeError, IOError):
            continue

    sessions.sort(key=lambda x: x[2], reverse=True)
    return sessions[:limit]


def load_session_messages(session_id: str, limit: int = 10, cwd: Path | None = None) -> list[dict]:
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
        with open(session_file) as f:
            for line in f:
                d = json.loads(line)
                if d.get("type") == "user":
                    content = d.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        # Skip slash commands and their output
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
                                messages.append(
                                    {
                                        "type": "tool_use",
                                        "name": block.get("name", "?"),
                                        "input": block.get("input", {}),
                                        "id": block.get("id", ""),
                                    }
                                )
    except (json.JSONDecodeError, IOError):
        pass

    return messages[-limit:]
