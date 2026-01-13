"""Tool formatting and diff rendering utilities."""

import difflib
import json
import re
from pathlib import Path

from rich.text import Text


# Constants
MAX_CONTEXT_TOKENS = 200_000  # Claude's context window


def parse_context_tokens(content: str) -> int | None:
    """Parse token count from /context output.

    Looks for pattern like "**Tokens:** 17.7k / 200.0k"

    Returns:
        Token count as int, or None if not found.
    """
    match = re.search(r"\*\*Tokens:\*\*\s*([\d.]+)(k)?\s*/\s*[\d.]+k", content)
    if match:
        used = float(match.group(1))
        if match.group(2):  # has 'k' suffix
            used *= 1000
        return int(used)
    return None


def format_tool_header(name: str, input: dict) -> str:
    """Format a one-line header for a tool use."""
    if name == "Edit":
        return f"Edit: {input.get('file_path', '?')}"
    elif name == "Write":
        return f"Write: {input.get('file_path', '?')}"
    elif name == "Read":
        return f"Read: {input.get('file_path', '?')}"
    elif name == "Bash":
        cmd = input.get("command", "?")
        desc = input.get("description", "")
        if desc:
            return f"Bash: {desc}"
        return f"Bash: {cmd[:50]}{'...' if len(cmd) > 50 else ''}"
    elif name == "Glob":
        return f"Glob: {input.get('pattern', '?')}"
    elif name == "Grep":
        return f"Grep: {input.get('pattern', '?')}"
    elif name == "WebSearch":
        return f"WebSearch: {input.get('query', '?')}"
    elif name == "WebFetch":
        return f"WebFetch: {input.get('url', '?')[:50]}"
    elif name == "Task":
        desc = input.get("description", "")
        agent = input.get("subagent_type", "")
        if desc:
            return f"Task: {desc}" + (f" ({agent})" if agent else "")
        return f"Task" + (f" ({agent})" if agent else "")
    elif name == "TodoWrite":
        todos = input.get("todos", [])
        return f"TodoWrite: {len(todos)} items"
    elif name == "AskUserQuestion":
        questions = input.get("questions", [])
        if questions and questions[0].get("question"):
            q = questions[0]["question"][:40]
            return f"AskUserQuestion: {q}..."
        return "AskUserQuestion"
    elif name == "Skill":
        return f"Skill: {input.get('skill', '?')}"
    else:
        return f"{name}"


def get_lang_from_path(path: str) -> str:
    """Guess language from file extension for syntax highlighting."""
    ext = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "jsx",
        ".tsx": "tsx",
        ".rs": "rust",
        ".go": "go",
        ".rb": "ruby",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".css": "css",
        ".html": "html",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".sh": "bash",
        ".bash": "bash",
    }.get(ext, "")


def _tokenize(s: str) -> list[str]:
    """Split string into words and punctuation for word-level diff."""
    return re.findall(r"\w+|[^\w\s]|\s+", s)


def _render_word_diff(old_line: str, new_line: str, result: Text) -> None:
    """Render a single line pair with word-level highlighting."""
    old_tokens = _tokenize(old_line)
    new_tokens = _tokenize(new_line)
    sm = difflib.SequenceMatcher(None, old_tokens, new_tokens)

    # Build old line with subtle red background
    result.append("- ", style="red on #2d0000")
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        chunk = "".join(old_tokens[i1:i2])
        if tag == "equal":
            result.append(chunk, style="on #2d0000")
        elif tag in ("delete", "replace"):
            result.append(chunk, style="bold red on #401010")
    result.append("\n")

    # Build new line with subtle green background
    result.append("+ ", style="green on #002d00")
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        chunk = "".join(new_tokens[j1:j2])
        if tag == "equal":
            result.append(chunk, style="on #002d00")
        elif tag in ("insert", "replace"):
            result.append(chunk, style="bold green on #104010")
    result.append("\n")


def format_diff_text(old: str, new: str, max_len: int = 300) -> Text:
    """Format a diff with subtle red/green backgrounds."""
    result = Text()
    old_preview = old[:max_len] + ("..." if len(old) > max_len else "")
    new_preview = new[:max_len] + ("..." if len(new) > max_len else "")
    old_lines = old_preview.split("\n") if old else []
    new_lines = new_preview.split("\n") if new else []

    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for line in old_lines[i1:i2]:
                result.append(f"  {line}\n", style="dim")
        elif tag == "delete":
            for line in old_lines[i1:i2]:
                result.append(f"- {line}\n", style="red on #2d0000")
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                result.append(f"+ {line}\n", style="green on #002d00")
        elif tag == "replace":
            # For replaced lines, highlight word-level changes
            for old_line, new_line in zip(old_lines[i1:i2], new_lines[j1:j2]):
                _render_word_diff(old_line, new_line, result)
            # Handle unequal line counts
            for line in old_lines[i1 + len(new_lines[j1:j2]) : i2]:
                result.append(f"- {line}\n", style="red on #2d0000")
            for line in new_lines[j1 + len(old_lines[i1:i2]) : j2]:
                result.append(f"+ {line}\n", style="green on #002d00")
    return result


def format_tool_details(name: str, input: dict) -> str:
    """Format expanded details for a tool use (non-Edit tools)."""
    if name == "Write":
        path = input.get("file_path", "?")
        content = input.get("content", "")
        lang = get_lang_from_path(path)
        preview = content[:400] + ("..." if len(content) > 400 else "")
        return f"```{lang}\n{preview}\n```"
    elif name == "Read":
        path = input.get("file_path", "?")
        offset = input.get("offset")
        limit = input.get("limit")
        details = f"**File:** `{path}`"
        if offset or limit:
            end = (offset or 0) + limit if limit else "end"
            details += f"\nLines: {offset or 0} - {end}"
        return details
    elif name == "Bash":
        cmd = input.get("command", "?")
        return f"```bash\n{cmd}\n```"
    elif name == "Glob":
        pattern = input.get("pattern", "?")
        path = input.get("path", ".")
        return f"**Pattern:** `{pattern}`\n**Path:** `{path}`"
    elif name == "Grep":
        pattern = input.get("pattern", "?")
        path = input.get("path", ".")
        return f"**Pattern:** `{pattern}`\n**Path:** `{path}`"
    else:
        return f"```\n{json.dumps(input, indent=2)}\n```"
