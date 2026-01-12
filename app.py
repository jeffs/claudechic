"""Claude Code Textual UI - A terminal interface for Claude Code."""

import asyncio
import difflib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

import anyio
import psutil
import pyperclip

# Set up file logging
logging.basicConfig(
    filename="cc-textual.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger(__name__)

from textual.app import App, ComposeResult, RenderResult
from textual.widgets import Markdown, TextArea, Header, Footer, Static, ListView, ListItem, Label, Collapsible, Button
from textual.widgets._header import HeaderIcon, HeaderTitle
from textual.containers import VerticalScroll, Horizontal
from textual.message import Message
from textual.binding import Binding
from textual.reactive import reactive
from textual import work
from textual.events import MouseUp
from textual.widget import Widget
from rich.text import Text

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    SystemMessage,
    UserMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
)
from claude_agent_sdk.types import (
    ToolPermissionContext,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    HookMatcher,
)


@dataclass
class PermissionRequest:
    """Represents a pending permission request - for testing and UI."""
    tool_name: str
    tool_input: dict[str, Any]
    _event: threading.Event = field(default_factory=threading.Event)
    _result: str = "deny"

    @property
    def title(self) -> str:
        return f"Allow {format_tool_header(self.tool_name, self.tool_input)}?"

    def respond(self, result: str) -> None:
        """Respond to this permission request programmatically."""
        self._result = result
        self._event.set()

    async def wait(self) -> str:
        """Wait for response (from UI or programmatic)."""
        while not self._event.is_set():
            await anyio.sleep(0.05)
        return self._result


def is_valid_uuid(s: str) -> bool:
    """Check if string is a valid UUID (not agent-* internal sessions)."""
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', s, re.I))


def get_project_sessions_dir() -> Path | None:
    """Get the sessions directory for the current project."""
    cwd = Path.cwd().absolute()
    # Claude stores sessions in ~/.claude/projects/-path-to-project (with dashes instead of slashes)
    project_key = str(cwd).replace("/", "-")
    sessions_dir = Path.home() / ".claude/projects" / project_key
    return sessions_dir if sessions_dir.exists() else None


def get_recent_sessions(limit: int = 20, search: str = "") -> list[tuple[str, str, float, int]]:
    """Get recent sessions from current project only. Returns [(session_id, preview, mtime, msg_count)].

    If search is provided, filters to sessions containing that text in any user message.
    """
    sessions = []
    sessions_dir = get_project_sessions_dir()
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


def load_session_messages(session_id: str, limit: int = 10) -> list[dict]:
    """Load recent messages from a session file. Returns list of message dicts.

    Each dict has 'type' key: 'user', 'assistant', or 'tool_use'.
    - user: {'type': 'user', 'content': str}
    - assistant: {'type': 'assistant', 'content': str}
    - tool_use: {'type': 'tool_use', 'name': str, 'input': dict}
    """
    sessions_dir = get_project_sessions_dir()
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
                        # Skip slash commands and their output (XML-wrapped format)
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

    # Return last N messages
    return messages[-limit:]


MAX_CONTEXT_TOKENS = 200_000  # Claude's context window


def parse_context_tokens(content: str) -> int | None:
    """Parse token count from /context output. Returns tokens used or None."""
    # Look for "**Tokens:** 17.7k / 200.0k" pattern
    match = re.search(r'\*\*Tokens:\*\*\s*([\d.]+)(k)?\s*/\s*[\d.]+k', content)
    if match:
        used = float(match.group(1))
        if match.group(2):  # has 'k' suffix
            used *= 1000
        return int(used)
    return None


class CPUBar(Widget):
    """Display CPU usage in the header."""

    cpu_pct = reactive(0.0)

    def on_mount(self) -> None:
        self._process = psutil.Process()
        self._process.cpu_percent()  # Prime the measurement
        self.set_interval(2.0, self._update_cpu)

    def _update_cpu(self) -> None:
        try:
            self.cpu_pct = self._process.cpu_percent()
        except Exception:
            pass

    def render(self) -> RenderResult:
        pct = min(self.cpu_pct / 100.0, 1.0)
        if pct < 0.3:
            color = "dim"
        elif pct < 0.7:
            color = "yellow"
        else:
            color = "red"
        return Text.assemble(("CPU ", "dim"), (f"{self.cpu_pct:3.0f}%", color))


class ContextBar(Widget):
    """Display context usage as a progress bar in the header."""

    tokens = reactive(0)
    max_tokens = reactive(MAX_CONTEXT_TOKENS)

    def render(self) -> RenderResult:
        pct = min(self.tokens / self.max_tokens, 1.0) if self.max_tokens else 0
        bar_width = 10
        filled = int(pct * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        # Dim when low, yellow when moderate, red when high
        if pct < 0.5:
            color = "dim"
        elif pct < 0.8:
            color = "yellow"
        else:
            color = "red"
        return Text.assemble((bar, color), (f" {pct*100:.0f}%", color))


class HeaderIndicators(Widget):
    """Right-side header indicators container."""

    def compose(self) -> ComposeResult:
        yield CPUBar(id="cpu-bar")
        yield ContextBar(id="context-bar")


class ContextHeader(Header):
    """Header with context bar and CPU indicator."""

    def compose(self) -> ComposeResult:
        yield HeaderIcon().data_bind(Header.icon)
        yield HeaderTitle()
        yield HeaderIndicators()


class SessionItem(ListItem):
    """A session in the sidebar."""
    def __init__(self, session_id: str, preview: str, msg_count: int = 0) -> None:
        super().__init__()
        self.session_id = session_id
        self.preview = preview
        self.msg_count = msg_count

    def compose(self) -> ComposeResult:
        yield Label(f"{self.preview[:50]}\n({self.msg_count} msgs)")


class ChatInput(TextArea):
    """Text input that submits on Enter, newline on Shift+Enter, history with Up/Down."""

    BINDINGS = [
        Binding("enter", "submit", "Send", priority=True, show=False),
        Binding("ctrl+j", "newline", "Newline", priority=True, show=False),
        Binding("up", "history_prev", "Previous", priority=True, show=False),
        Binding("down", "history_next", "Next", priority=True, show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        # Preserve whitespace behavior for pasting
        kwargs.setdefault("tab_behavior", "indent")
        kwargs.setdefault("soft_wrap", True)
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index: int = -1  # -1 means not browsing history
        self._current_input: str = ""  # Saved input when browsing history

    class Submitted(Message):
        """Posted when user presses Enter."""
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def action_submit(self) -> None:
        text = self.text.strip()
        if text:
            # Add to history (avoid duplicates of last entry)
            if not self._history or self._history[-1] != text:
                self._history.append(text)
        self._history_index = -1
        self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        self.insert("\n")

    def action_history_prev(self) -> None:
        """Go to previous command in history (only when cursor at top)."""
        # Only trigger if cursor is on the first line
        if self.cursor_location[0] != 0:
            self.move_cursor_relative(rows=-1)
            return
        if not self._history:
            return
        if self._history_index == -1:
            # Starting to browse - save current input
            self._current_input = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self.text = self._history[self._history_index]
        self.move_cursor(self.document.end)

    def action_history_next(self) -> None:
        """Go to next command in history (only when cursor at bottom)."""
        # Only trigger if cursor is on the last line
        last_line = self.document.line_count - 1
        if self.cursor_location[0] != last_line:
            self.move_cursor_relative(rows=1)
            return
        if self._history_index == -1:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.text = self._history[self._history_index]
        else:
            # Back to current input
            self._history_index = -1
            self.text = self._current_input
        self.move_cursor(self.document.end)


class StreamChunk(Message):
    """Message sent when a chunk of text is received."""
    def __init__(self, text: str, new_message: bool = False, parent_tool_use_id: str | None = None) -> None:
        self.text = text
        self.new_message = new_message  # Start a new ChatMessage widget
        self.parent_tool_use_id = parent_tool_use_id  # If set, belongs to a Task
        super().__init__()


class ResponseComplete(Message):
    """Message sent when response is complete."""
    def __init__(self, result: ResultMessage | None = None) -> None:
        self.result = result
        super().__init__()


class ToolUseMessage(Message):
    """Message sent when a tool use starts."""
    def __init__(self, block: ToolUseBlock, parent_tool_use_id: str | None = None) -> None:
        self.block = block
        self.parent_tool_use_id = parent_tool_use_id
        super().__init__()


class ToolResultMessage(Message):
    """Message sent when a tool result arrives."""
    def __init__(self, block: ToolResultBlock, parent_tool_use_id: str | None = None) -> None:
        self.block = block
        self.parent_tool_use_id = parent_tool_use_id
        super().__init__()


class ContextUpdate(Message):
    """Message sent when context usage is known."""
    def __init__(self, tokens: int) -> None:
        self.tokens = tokens
        super().__init__()


def format_tool_header(name: str, input: dict) -> str:
    """Format a one-line header for a tool use."""
    if name == "Edit":
        return f"Edit: {input.get('file_path', '?')}"
    elif name == "Write":
        return f"Write: {input.get('file_path', '?')}"
    elif name == "Read":
        return f"Read: {input.get('file_path', '?')}"
    elif name == "Bash":
        cmd = input.get('command', '?')
        desc = input.get('description', '')
        if desc:
            return f"Bash: {desc}"
        return f"Bash: {cmd[:50]}{'...' if len(cmd) > 50 else ''}"
    elif name == "Glob":
        return f"Glob: {input.get('pattern', '?')}"
    elif name == "Grep":
        return f"Grep: {input.get('pattern', '?')}"
    else:
        return f"{name}"


def get_lang_from_path(path: str) -> str:
    """Guess language from file extension for syntax highlighting."""
    ext = Path(path).suffix.lower()
    return {
        '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
        '.jsx': 'jsx', '.tsx': 'tsx', '.rs': 'rust', '.go': 'go',
        '.rb': 'ruby', '.java': 'java', '.c': 'c', '.cpp': 'cpp',
        '.h': 'c', '.hpp': 'cpp', '.css': 'css', '.html': 'html',
        '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
        '.md': 'markdown', '.sh': 'bash', '.bash': 'bash',
    }.get(ext, '')


def _tokenize(s: str) -> list[str]:
    """Split string into words and punctuation for word-level diff."""
    return re.findall(r'\w+|[^\w\s]|\s+', s)


def _render_word_diff(old_line: str, new_line: str, result: Text) -> None:
    """Render a single line pair with word-level highlighting."""
    old_tokens = _tokenize(old_line)
    new_tokens = _tokenize(new_line)
    sm = difflib.SequenceMatcher(None, old_tokens, new_tokens)

    # Build old line with subtle red background
    result.append("- ", style="red on #2d0000")
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        chunk = ''.join(old_tokens[i1:i2])
        if tag == 'equal':
            result.append(chunk, style="on #2d0000")
        elif tag in ('delete', 'replace'):
            result.append(chunk, style="bold red on #401010")
    result.append("\n")

    # Build new line with subtle green background
    result.append("+ ", style="green on #002d00")
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        chunk = ''.join(new_tokens[j1:j2])
        if tag == 'equal':
            result.append(chunk, style="on #002d00")
        elif tag in ('insert', 'replace'):
            result.append(chunk, style="bold green on #104010")
    result.append("\n")


def format_diff_text(old: str, new: str, max_len: int = 300) -> Text:
    """Format a diff with subtle red/green backgrounds."""
    result = Text()
    old_preview = old[:max_len] + ('...' if len(old) > max_len else '')
    new_preview = new[:max_len] + ('...' if len(new) > max_len else '')
    old_lines = old_preview.split('\n') if old else []
    new_lines = new_preview.split('\n') if new else []

    # Use difflib to match lines
    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for line in old_lines[i1:i2]:
                result.append(f"  {line}\n", style="dim")
        elif tag == 'delete':
            for line in old_lines[i1:i2]:
                result.append(f"- {line}\n", style="red on #2d0000")
        elif tag == 'insert':
            for line in new_lines[j1:j2]:
                result.append(f"+ {line}\n", style="green on #002d00")
        elif tag == 'replace':
            # For replaced lines, highlight word-level changes
            for old_line, new_line in zip(old_lines[i1:i2], new_lines[j1:j2]):
                _render_word_diff(old_line, new_line, result)
            # Handle unequal line counts
            for line in old_lines[i1+len(new_lines[j1:j2]):i2]:
                result.append(f"- {line}\n", style="red on #2d0000")
            for line in new_lines[j1+len(old_lines[i1:i2]):j2]:
                result.append(f"+ {line}\n", style="green on #002d00")
    return result


def format_tool_details(name: str, input: dict) -> str:
    """Format expanded details for a tool use (non-Edit tools)."""
    if name == "Write":
        path = input.get('file_path', '?')
        content = input.get('content', '')
        lang = get_lang_from_path(path)
        preview = content[:400] + ('...' if len(content) > 400 else '')
        return f"```{lang}\n{preview}\n```"
    elif name == "Read":
        path = input.get('file_path', '?')
        offset = input.get('offset')
        limit = input.get('limit')
        details = f"**File:** `{path}`"
        if offset or limit:
            details += f"\nLines: {offset or 0} - {(offset or 0) + (limit or 'end')}"
        return details
    elif name == "Bash":
        cmd = input.get('command', '?')
        return f"```bash\n{cmd}\n```"
    elif name == "Glob":
        pattern = input.get('pattern', '?')
        path = input.get('path', '.')
        return f"**Pattern:** `{pattern}`\n**Path:** `{path}`"
    elif name == "Grep":
        pattern = input.get('pattern', '?')
        path = input.get('path', '.')
        return f"**Pattern:** `{pattern}`\n**Path:** `{path}`"
    else:
        return f"```\n{json.dumps(input, indent=2)}\n```"


class ToolUseWidget(Static):
    """A collapsible widget showing a tool use."""

    def __init__(self, block: ToolUseBlock, collapsed: bool = False) -> None:
        super().__init__()
        self.block = block
        self.result: ToolResultBlock | None = None
        self._initial_collapsed = collapsed

    def compose(self) -> ComposeResult:
        yield Button("⎘", id="tool-copy-btn", classes="tool-copy-btn")
        header = format_tool_header(self.block.name, self.block.input)
        with Collapsible(title=header, collapsed=self._initial_collapsed):
            if self.block.name == "Edit":
                # Use colored diff display
                diff = format_diff_text(
                    self.block.input.get('old_string', ''),
                    self.block.input.get('new_string', '')
                )
                yield Static(diff, id="diff-content")
            else:
                details = format_tool_details(self.block.name, self.block.input)
                yield Markdown(details, id="md-content")

    def collapse(self) -> None:
        """Collapse this widget."""
        try:
            self.query_one(Collapsible).collapsed = True
        except Exception:
            pass

    def get_copyable_content(self) -> str:
        """Get content suitable for copying - preserves exact content."""
        inp = self.block.input
        parts = []
        if self.block.name == "Edit":
            parts.append(f"File: {inp.get('file_path', '?')}")
            if inp.get('old_string'):
                parts.append(f"Old:\n```\n{inp['old_string']}\n```")
            if inp.get('new_string'):
                parts.append(f"New:\n```\n{inp['new_string']}\n```")
        elif self.block.name == "Bash":
            parts.append(f"Command:\n```\n{inp.get('command', '?')}\n```")
        elif self.block.name == "Write":
            parts.append(f"File: {inp.get('file_path', '?')}")
            if inp.get('content'):
                parts.append(f"Content:\n```\n{inp['content']}\n```")
        elif self.block.name == "Read":
            parts.append(f"File: {inp.get('file_path', '?')}")
        else:
            parts.append(json.dumps(inp, indent=2))
        if self.result and self.result.content:
            content = self.result.content if isinstance(self.result.content, str) else str(self.result.content)
            parts.append(f"Result:\n```\n{content}\n```")
        return "\n\n".join(parts)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tool-copy-btn":
            event.stop()
            try:
                pyperclip.copy(self.get_copyable_content())
                self.app.notify("Copied tool output")
            except Exception as e:
                self.app.notify(f"Copy failed: {e}", severity="error")

    def on_mouse_move(self) -> None:
        """Track mouse presence for hover effect."""
        if not self.has_class("hovered"):
            self.add_class("hovered")

    def on_leave(self) -> None:
        self.remove_class("hovered")

    def set_result(self, result: ToolResultBlock) -> None:
        """Update with tool result."""
        self.result = result
        log.info(f"Tool result for {self.block.name}: {type(result.content)} - {str(result.content)[:200]}")
        try:
            collapsible = self.query_one(Collapsible)
            if result.is_error:
                collapsible.add_class("error")
            # Edit uses Static for diff, others use Markdown
            if self.block.name == "Edit":
                # For edits, result is usually just success/error - no content to add
                return
            md = collapsible.query_one("#md-content", Markdown)
            details = format_tool_details(self.block.name, self.block.input)
            if result.content:
                content = result.content if isinstance(result.content, str) else str(result.content)
                preview = content[:500] + ('...' if len(content) > 500 else '')
                if result.is_error:
                    details += f"\n\n**Error:**\n```\n{preview}\n```"
                elif self.block.name == "Read":
                    lang = get_lang_from_path(self.block.input.get('file_path', ''))
                    details += f"\n\n```{lang}\n{preview}\n```"
                elif self.block.name in ("Bash", "Grep", "Glob"):
                    details += f"\n\n```\n{preview}\n```"
                else:
                    details += f"\n\n{preview}"
            md.update(details)
        except Exception:
            pass  # Widget may not be mounted yet


class TaskWidget(Static):
    """A collapsible widget showing a Task with nested subagent content."""

    RECENT_EXPANDED = 2  # Keep last N tool uses expanded within task

    def __init__(self, block: ToolUseBlock, collapsed: bool = False) -> None:
        super().__init__()
        self.block = block
        self.result: ToolResultBlock | None = None
        self._initial_collapsed = collapsed
        self._current_message: ChatMessage | None = None
        self._recent_tools: list[ToolUseWidget] = []
        self._pending_tools: dict[str, ToolUseWidget] = {}

    def compose(self) -> ComposeResult:
        desc = self.block.input.get('description', 'Task')
        agent_type = self.block.input.get('subagent_type', '')
        title = f"Task: {desc}" + (f" ({agent_type})" if agent_type else "")
        with Collapsible(title=title, collapsed=self._initial_collapsed):
            yield Static("", id="task-content")

    def collapse(self) -> None:
        """Collapse this widget."""
        try:
            self.query_one(Collapsible).collapsed = True
        except Exception:
            pass

    def add_text(self, text: str, new_message: bool = False) -> None:
        """Add text content from subagent."""
        try:
            content = self.query_one("#task-content", Static)
            if new_message or self._current_message is None:
                self._current_message = ChatMessage("")
                self._current_message.add_class("assistant-message")
                if new_message:
                    self._current_message.add_class("after-tool")
                content.mount(self._current_message)
            self._current_message.append_content(text)
        except Exception:
            pass

    def add_tool_use(self, block: ToolUseBlock) -> None:
        """Add a tool use from subagent."""
        try:
            content = self.query_one("#task-content", Static)
            # Collapse older tools
            while len(self._recent_tools) >= self.RECENT_EXPANDED:
                old = self._recent_tools.pop(0)
                old.collapse()
            widget = ToolUseWidget(block, collapsed=False)
            self._pending_tools[block.id] = widget
            self._recent_tools.append(widget)
            content.mount(widget)
            self._current_message = None  # Next text starts fresh
        except Exception:
            pass

    def add_tool_result(self, block: ToolResultBlock) -> None:
        """Add a tool result from subagent."""
        widget = self._pending_tools.get(block.tool_use_id)
        if widget:
            widget.set_result(block)
            del self._pending_tools[block.tool_use_id]

    def set_result(self, result: ToolResultBlock) -> None:
        """Set the Task's own result."""
        self.result = result
        try:
            collapsible = self.query_one(Collapsible)
            if result.is_error:
                collapsible.add_class("error")
        except Exception:
            pass  # Widget may not be mounted yet


class ThinkingIndicator(Static):
    """Animated spinner shown when Claude is working."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    frame = reactive(0)

    def __init__(self) -> None:
        super().__init__("⠋ Thinking...")

    def on_mount(self) -> None:
        self._timer = self.set_interval(1/10, self._tick)

    def on_unmount(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self.frame = (self.frame + 1) % len(self.FRAMES)

    def watch_frame(self, frame: int) -> None:
        self.update(f"{self.FRAMES[frame]} Thinking...")
        self.refresh()


class ChatMessage(Static):
    """A single chat message."""

    def __init__(self, content: str = "") -> None:
        super().__init__()
        self._content = content.rstrip()

    def compose(self) -> ComposeResult:
        yield Button("⎘", id="copy-btn", classes="copy-btn")
        yield Markdown(self._content, id="content")

    def append_content(self, text: str) -> None:
        self._content += text
        try:
            md = self.query_one("#content", Markdown)
            md.update(self._content.rstrip())
        except Exception:
            pass  # Widget not mounted yet, content will show on mount

    def get_raw_content(self) -> str:
        """Get raw content for copying."""
        return self._content

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-btn":
            try:
                pyperclip.copy(self.get_raw_content())
                self.app.notify("Copied to clipboard")
            except Exception as e:
                self.app.notify(f"Copy failed: {e}", severity="error")


class SelectionPrompt(Static):
    """Reusable selection prompt with arrow/number navigation."""

    can_focus = True

    def __init__(self, title: str, options: list[tuple[str, str]]) -> None:
        """Create selection prompt.

        Args:
            title: Prompt title/question
            options: List of (value, label) tuples
        """
        super().__init__()
        self.title = title
        self.options = options
        self.selected_idx = 0
        self._result_event = threading.Event()
        self._result_value: str = options[0][0] if options else ""

    def compose(self) -> ComposeResult:
        yield Static(self.title, classes="prompt-title")
        for i, (value, label) in enumerate(self.options):
            classes = "prompt-option selected" if i == 0 else "prompt-option"
            yield Static(f"{i + 1}. {label}", classes=classes, id=f"opt-{i}")

    def on_mount(self) -> None:
        """Auto-focus on mount to capture keys immediately."""
        self.focus()

    def _update_selection(self) -> None:
        """Update visual selection state."""
        for i in range(len(self.options)):
            opt = self.query_one(f"#opt-{i}", Static)
            if i == self.selected_idx:
                opt.add_class("selected")
            else:
                opt.remove_class("selected")

    def on_key(self, event) -> None:
        if event.key == "up":
            self.selected_idx = (self.selected_idx - 1) % len(self.options)
            self._update_selection()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self.selected_idx = (self.selected_idx + 1) % len(self.options)
            self._update_selection()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self._resolve(self.options[self.selected_idx][0])
            event.prevent_default()
            event.stop()
        elif event.key == "escape":
            self._resolve("")  # Empty = cancelled
            event.prevent_default()
            event.stop()
        elif event.key.isdigit():
            idx = int(event.key) - 1
            if 0 <= idx < len(self.options):
                self._resolve(self.options[idx][0])
                event.prevent_default()
                event.stop()

    def _resolve(self, result: str) -> None:
        if not self._result_event.is_set():
            self._result_value = result
            self._result_event.set()
        self.remove()

    async def wait(self) -> str:
        """Wait for selection. Returns value or empty string if cancelled."""
        while not self._result_event.is_set():
            await anyio.sleep(0.05)
        return self._result_value


class QuestionPrompt(Static):
    """Multi-question prompt for AskUserQuestion tool."""

    can_focus = True

    def __init__(self, questions: list[dict]) -> None:
        super().__init__()
        self.questions = questions
        self.current_q = 0
        self.selected_idx = 0
        self.answers: dict[str, str] = {}
        self._result_event = threading.Event()
        self._in_other_mode = False  # Whether typing in "Other" input

    def compose(self) -> ComposeResult:
        yield from self._render_question()

    def _render_question(self):
        """Yield widgets for current question."""
        q = self.questions[self.current_q]
        yield Static(f"[{self.current_q + 1}/{len(self.questions)}] {q['question']}", classes="prompt-title")
        for i, opt in enumerate(q.get('options', [])):
            classes = "prompt-option selected" if i == self.selected_idx else "prompt-option"
            label = opt.get('label', '?')
            desc = opt.get('description', '')
            text = f"{i + 1}. {label}" + (f" - {desc}" if desc else "")
            yield Static(text, classes=classes, id=f"opt-{i}")
        # "Other" option
        other_idx = len(q.get('options', []))
        classes = "prompt-option selected" if self.selected_idx == other_idx else "prompt-option"
        yield Static(f"{other_idx + 1}. Other:", classes=classes, id=f"opt-{other_idx}")

    def on_mount(self) -> None:
        self.focus()

    def _update_display(self) -> None:
        """Refresh display for current question."""
        self._in_other_mode = False
        self.remove_children()
        for w in self._render_question():
            self.mount(w)

    def _update_selection(self) -> None:
        """Update visual selection."""
        q = self.questions[self.current_q]
        total = len(q.get('options', [])) + 1
        for i in range(total):
            try:
                opt = self.query_one(f"#opt-{i}", Static)
                if i == self.selected_idx:
                    opt.add_class("selected")
                else:
                    opt.remove_class("selected")
            except Exception:
                pass

    def on_key(self, event) -> None:
        # If in other mode, let Input handle keys (except escape/enter)
        if self._in_other_mode:
            if event.key == "escape":
                self._exit_other_mode()
                event.prevent_default()
                event.stop()
            elif event.key == "enter":
                self._submit_other()
                event.prevent_default()
                event.stop()
            return

        q = self.questions[self.current_q]
        options = q.get('options', [])
        total = len(options) + 1

        if event.key == "up":
            self.selected_idx = (self.selected_idx - 1) % total
            self._update_selection()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self.selected_idx = (self.selected_idx + 1) % total
            self._update_selection()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self._select_current()
            event.prevent_default()
            event.stop()
        elif event.key == "escape":
            self._resolve_cancelled()
            event.prevent_default()
            event.stop()
        elif event.key.isdigit():
            idx = int(event.key) - 1
            if 0 <= idx < total:
                self.selected_idx = idx
                self._select_current()
                event.prevent_default()
                event.stop()

    def _select_current(self) -> None:
        """Select current option and advance to next question or finish."""
        q = self.questions[self.current_q]
        options = q.get('options', [])

        if self.selected_idx < len(options):
            # Regular option - record and advance
            answer = options[self.selected_idx].get('label', '?')
            self._record_answer(answer)
        else:
            # "Other" - show text input
            self._enter_other_mode()

    def _enter_other_mode(self) -> None:
        """Show text input for custom answer."""
        self._in_other_mode = True
        from textual.widgets import Input
        input_widget = Input(placeholder="Type your answer...", id="other-input")
        self.mount(input_widget)
        input_widget.focus()

    def _exit_other_mode(self) -> None:
        """Cancel other mode and return to selection."""
        self._in_other_mode = False
        try:
            self.query_one("#other-input").remove()
        except Exception:
            pass
        self.focus()

    def _submit_other(self) -> None:
        """Submit the custom answer from text input."""
        try:
            input_widget = self.query_one("#other-input")
            answer = input_widget.value.strip()
            if answer:
                self._record_answer(answer)
            else:
                self._exit_other_mode()
        except Exception:
            self._exit_other_mode()

    def _record_answer(self, answer: str) -> None:
        """Record answer and advance to next question or finish."""
        q = self.questions[self.current_q]
        self.answers[q['question']] = answer

        if self.current_q < len(self.questions) - 1:
            self.current_q += 1
            self.selected_idx = 0
            self._update_display()
        else:
            self._resolve()

    def _resolve(self) -> None:
        if not self._result_event.is_set():
            self._result_event.set()
        self.remove()

    def _resolve_cancelled(self) -> None:
        self.answers = {}
        self._resolve()

    async def wait(self) -> dict[str, str]:
        """Wait for all answers. Returns answers dict or empty if cancelled."""
        while not self._result_event.is_set():
            await anyio.sleep(0.05)
        return self.answers


async def _dummy_hook(input_data, tool_use_id, context):
    """Dummy hook required for can_use_tool to work in Python SDK."""
    return {"continue_": True}


class ChatApp(App):
    """Main chat application."""

    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("ctrl+y", "copy_selection", "Copy", priority=True, show=False),
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        Binding("ctrl+l", "clear", "Clear", show=False),
        Binding("shift+tab", "cycle_permission_mode", "Auto-edit", priority=True),
        Binding("escape", "cancel_picker", "Cancel", show=False),
    ]

    # Auto-approve Edit/Write tools (but still prompt for Bash, etc.)
    AUTO_EDIT_TOOLS = {"Edit", "Write"}

    # Tools to collapse by default (not very informative when expanded)
    COLLAPSE_BY_DEFAULT = {"WebSearch", "WebFetch", "AskUserQuestion"}

    RECENT_TOOLS_EXPANDED = 2  # Keep last N tool uses expanded

    auto_approve_edits = reactive(False)  # When True, auto-approve Edit/Write

    def __init__(self, resume_session_id: str | None = None) -> None:
        super().__init__()
        self.options = ClaudeAgentOptions(
            permission_mode="default",  # Respect settings.json permissions + hooks
            env={"ANTHROPIC_API_KEY": ""},  # Use Max subscription, not API key
            setting_sources=["user", "project", "local"],
            can_use_tool=self._handle_permission,
            # Dummy hook required for can_use_tool to work in Python SDK
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[_dummy_hook])]},
        )
        self.client: ClaudeSDKClient | None = None
        self.current_response: ChatMessage | None = None
        self.session_id: str | None = None
        self.pending_tools: dict[str, ToolUseWidget | TaskWidget] = {}  # tool_use_id -> widget
        self.active_tasks: dict[str, TaskWidget] = {}  # tool_use_id -> TaskWidget for routing
        self.recent_tools: list[ToolUseWidget | TaskWidget] = []  # Track recent for auto-collapse
        self._resume_on_start = resume_session_id  # Session to resume on startup
        self._session_picker_active = False  # Whether session picker is shown
        # Event queues for testing
        self.interactions: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        self.completions: asyncio.Queue[ResponseComplete] = asyncio.Queue()

    async def _handle_permission(
        self, tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResult:
        """Handle permission request from SDK - shows UI prompt."""
        log.info(f"Permission requested for {tool_name}: {str(tool_input)[:100]}")

        # Handle AskUserQuestion - show question UI and return answers
        if tool_name == "AskUserQuestion":
            return await self._handle_ask_user_question(tool_input)

        # Auto-approve Edit/Write if enabled
        if self.auto_approve_edits and tool_name in self.AUTO_EDIT_TOOLS:
            log.info(f"Auto-approved {tool_name}")
            return PermissionResultAllow()
        # Create permission request and publish to queue (for testing)
        request = PermissionRequest(tool_name, tool_input)
        await self.interactions.put(request)
        # Show UI prompt
        options = [("allow", "Yes, this time only"), ("deny", "No")]
        if tool_name in self.AUTO_EDIT_TOOLS:
            options.insert(0, ("allow_all", "Yes, all edits in this session"))
        prompt = SelectionPrompt(request.title, options)
        input_widget = self.query_one("#input", ChatInput)
        input_widget.add_class("hidden")
        self.query_one("#input-wrapper").mount(prompt)
        # Wait for response from either UI or programmatic (test)
        async def ui_response():
            result = await prompt.wait()
            if not request._event.is_set():
                request.respond(result)
        self.run_worker(ui_response(), exclusive=False)
        result = await request.wait()
        # Clean up UI and restore input
        try:
            prompt.remove()
        except Exception:
            pass
        input_widget.remove_class("hidden")
        log.info(f"Permission result: {result}")
        if result == "allow_all":
            self.auto_approve_edits = True
            self.notify("Auto-edit enabled (Shift+Tab to disable)")
            return PermissionResultAllow()
        elif result == "allow":
            return PermissionResultAllow()
        else:
            return PermissionResultDeny(message="User denied permission")

    async def _handle_ask_user_question(self, tool_input: dict[str, Any]) -> PermissionResult:
        """Handle AskUserQuestion tool - show questions and return answers."""
        questions = tool_input.get('questions', [])
        if not questions:
            log.warning("AskUserQuestion with no questions")
            return PermissionResultAllow(updated_input=tool_input)

        log.info(f"AskUserQuestion with {len(questions)} questions")

        # Show question prompt UI
        prompt = QuestionPrompt(questions)
        input_widget = self.query_one("#input", ChatInput)
        input_widget.add_class("hidden")
        self.query_one("#input-wrapper").mount(prompt)

        # Wait for answers
        answers = await prompt.wait()

        # Clean up UI
        try:
            prompt.remove()
        except Exception:
            pass
        input_widget.remove_class("hidden")

        if not answers:
            # User cancelled
            log.info("AskUserQuestion cancelled by user")
            return PermissionResultDeny(message="User cancelled questions")

        log.info(f"AskUserQuestion answers: {answers}")
        # Return with updated input containing answers
        return PermissionResultAllow(updated_input={
            'questions': questions,
            'answers': answers,
        })

    def action_cycle_permission_mode(self) -> None:
        """Toggle auto-approve for Edit/Write tools."""
        self.auto_approve_edits = not self.auto_approve_edits
        if self.auto_approve_edits:
            self.notify("Auto-edit: ON")
        else:
            self.notify("Auto-edit: OFF")

    def compose(self) -> ComposeResult:
        yield ContextHeader()
        with Horizontal(id="main"):
            yield ListView(id="session-picker", classes="hidden")
            yield VerticalScroll(id="chat-view")
        with Horizontal(id="input-wrapper"):
            yield ChatInput(id="input")
        yield Footer()

    async def on_mount(self) -> None:
        self.client = ClaudeSDKClient(self.options)
        await self.client.connect()
        self.query_one("#input", ChatInput).focus()
        if self._resume_on_start:
            self._load_and_display_history(self._resume_on_start)
            self.notify(f"Resuming {self._resume_on_start[:8]}...")
            self.resume_session(self._resume_on_start)
        else:
            self.refresh_context()

    def _load_and_display_history(self, session_id: str) -> None:
        """Load session history and display in chat view."""
        chat_view = self.query_one("#chat-view", VerticalScroll)
        chat_view.remove_children()
        for m in load_session_messages(session_id, limit=50):
            if m["type"] == "user":
                msg = ChatMessage( m['content'][:500])
                msg.add_class("user-message")
                chat_view.mount(msg)
            elif m["type"] == "assistant":
                msg = ChatMessage( m['content'][:1000])
                msg.add_class("assistant-message")
                chat_view.mount(msg)
            elif m["type"] == "tool_use":
                block = ToolUseBlock(id=m.get("id", ""), name=m["name"], input=m["input"])
                widget = ToolUseWidget(block, collapsed=True)
                chat_view.mount(widget)
        self.call_after_refresh(chat_view.scroll_end, animate=False)

    @work(group="context", exclusive=True, exit_on_error=False)
    async def refresh_context(self) -> None:
        """Silently run /context to get current usage."""
        if not self.client:
            return
        await self.client.query("/context")
        async for message in self.client.receive_response():
            if isinstance(message, UserMessage):
                content = getattr(message, 'content', '')
                tokens = parse_context_tokens(content)
                if tokens is not None:
                    self.post_message(ContextUpdate(tokens))

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        if not event.text.strip():
            return

        prompt = event.text
        self.query_one("#input", ChatInput).clear()
        chat_view = self.query_one("#chat-view", VerticalScroll)

        # Handle /clear specially - also clear UI
        if prompt.strip() == "/clear":
            chat_view.remove_children()
            self.notify("Conversation cleared")
            self.run_claude(prompt)
            return

        # Handle /resume - show session picker or resume specific session
        if prompt.strip().startswith("/resume"):
            parts = prompt.strip().split(maxsplit=1)
            if len(parts) > 1:
                # Resume specific session by ID
                self._load_and_display_history(parts[1])
                self.notify(f"Resuming {parts[1][:8]}...")
                self.resume_session(parts[1])
            else:
                # Show session picker
                self._show_session_picker()
            return

        # Add user message
        user_msg = ChatMessage(prompt)
        user_msg.add_class("user-message")
        chat_view.mount(user_msg)
        self.call_after_refresh(chat_view.scroll_end, animate=False)

        # Reset current response - will be created when first text arrives
        self.current_response = None

        # Show thinking indicator
        self._show_thinking()

        # Start the query
        self.run_claude(prompt)

    @work(group="claude", exclusive=True, exit_on_error=False)
    async def run_claude(self, prompt: str) -> None:
        if not self.client:
            return

        await self.client.query(prompt)
        # Track had_tool_use per parent (None = top-level, or parent_tool_use_id)
        had_tool_use: dict[str | None, bool] = {}
        async for message in self.client.receive_response():
            log.info(f"Message type: {type(message).__name__}")
            if isinstance(message, AssistantMessage):
                parent_id = message.parent_tool_use_id
                for block in message.content:
                    if isinstance(block, TextBlock):
                        # After tool use, need fresh message widget
                        new_msg = had_tool_use.get(parent_id, False)
                        self.post_message(StreamChunk(block.text, new_message=new_msg, parent_tool_use_id=parent_id))
                        had_tool_use[parent_id] = False
                    elif isinstance(block, ToolUseBlock):
                        self.post_message(ToolUseMessage(block, parent_tool_use_id=parent_id))
                        had_tool_use[parent_id] = True
                    elif isinstance(block, ToolResultBlock):
                        self.post_message(ToolResultMessage(block, parent_tool_use_id=parent_id))
            elif isinstance(message, UserMessage):
                # Check for /context response
                content = getattr(message, 'content', '')
                if '<local-command-stdout>' in content:
                    tokens = parse_context_tokens(content)
                    if tokens is not None:
                        self.post_message(ContextUpdate(tokens))
            elif isinstance(message, SystemMessage):
                # Handle system messages (compact, etc.)
                subtype = getattr(message, 'subtype', '')
                if subtype == 'compact_boundary':
                    meta = getattr(message, 'compact_metadata', None)
                    if meta:
                        self.call_from_thread(
                            self.notify,
                            f"Compacted: {getattr(meta, 'pre_tokens', '?')} tokens"
                        )
            elif isinstance(message, ResultMessage):
                self.post_message(ResponseComplete(message))

    def _show_thinking(self) -> None:
        """Show thinking indicator in chat view."""
        if self.query(ThinkingIndicator):
            return
        chat_view = self.query_one("#chat-view", VerticalScroll)
        chat_view.mount(ThinkingIndicator())
        self.call_after_refresh(chat_view.scroll_end, animate=False)

    def _hide_thinking(self) -> None:
        """Hide thinking indicator."""
        try:
            for ind in self.query(ThinkingIndicator):
                ind.remove()
        except Exception:
            pass

    def on_stream_chunk(self, event: StreamChunk) -> None:
        self._hide_thinking()
        # Route to TaskWidget if this belongs to a subagent
        if event.parent_tool_use_id and event.parent_tool_use_id in self.active_tasks:
            task = self.active_tasks[event.parent_tool_use_id]
            task.add_text(event.text, new_message=event.new_message)
            return
        # Top-level message
        chat_view = self.query_one("#chat-view", VerticalScroll)
        if event.new_message or not self.current_response:
            self.current_response = ChatMessage("")
            self.current_response.add_class("assistant-message")
            if event.new_message:
                self.current_response.add_class("after-tool")
            chat_view.mount(self.current_response)
        self.current_response.append_content(event.text)
        self.call_after_refresh(chat_view.scroll_end, animate=False)

    def on_tool_use_message(self, event: ToolUseMessage) -> None:
        """Handle a tool use starting."""
        self._hide_thinking()
        # Route to TaskWidget if this belongs to a subagent
        if event.parent_tool_use_id and event.parent_tool_use_id in self.active_tasks:
            task = self.active_tasks[event.parent_tool_use_id]
            task.add_tool_use(event.block)
            return
        # Top-level tool use
        chat_view = self.query_one("#chat-view", VerticalScroll)
        # Collapse older tools beyond the threshold
        while len(self.recent_tools) >= self.RECENT_TOOLS_EXPANDED:
            old = self.recent_tools.pop(0)
            old.collapse()
        # Create TaskWidget for Task tool, otherwise ToolUseWidget
        collapsed = event.block.name in self.COLLAPSE_BY_DEFAULT
        if event.block.name == "Task":
            widget = TaskWidget(event.block, collapsed=collapsed)
            self.active_tasks[event.block.id] = widget
        else:
            widget = ToolUseWidget(event.block, collapsed=collapsed)
        self.pending_tools[event.block.id] = widget
        self.recent_tools.append(widget)
        chat_view.mount(widget)
        self.call_after_refresh(chat_view.scroll_end, animate=False)
        # Show spinner while tool executes
        self._show_thinking()

    def on_tool_result_message(self, event: ToolResultMessage) -> None:
        """Handle a tool result arriving."""
        # Route to TaskWidget if this belongs to a subagent
        if event.parent_tool_use_id and event.parent_tool_use_id in self.active_tasks:
            task = self.active_tasks[event.parent_tool_use_id]
            task.add_tool_result(event.block)
            return
        # Top-level tool result
        widget = self.pending_tools.get(event.block.tool_use_id)
        if widget:
            widget.set_result(event.block)
            del self.pending_tools[event.block.tool_use_id]
            # Clean up active_tasks if this was a Task
            if event.block.tool_use_id in self.active_tasks:
                del self.active_tasks[event.block.tool_use_id]
        # Show spinner while Claude thinks about next step
        self._show_thinking()

    def on_context_update(self, event: ContextUpdate) -> None:
        """Update context bar from /context command."""
        self.query_one("#context-bar", ContextBar).tokens = event.tokens

    def on_response_complete(self, event: ResponseComplete) -> None:
        self._hide_thinking()
        if event.result:
            self.session_id = event.result.session_id
            # Refresh context from /context command (authoritative source)
            self.refresh_context()
        self.current_response = None
        self.query_one("#input", ChatInput).focus()
        # Publish to completions queue (for testing)
        self.completions.put_nowait(event)

    @work(group="resume", exclusive=True, exit_on_error=False)
    async def resume_session(self, session_id: str) -> None:
        """Resume a session by creating a new client (abandoning the old one)."""
        log.info(f"resume_session started: {session_id}")
        try:
            # Don't disconnect - just abandon old client to avoid task boundary issues
            self.client = None

            options = ClaudeAgentOptions(
                permission_mode="default",  # Respect settings.json permissions + hooks
                env={"ANTHROPIC_API_KEY": ""},
                setting_sources=["user", "project", "local"],
                resume=session_id,
                can_use_tool=self._handle_permission,
            )
            log.info("Creating new client")
            client = ClaudeSDKClient(options)
            log.info("Connecting new client")
            await client.connect()
            log.info("Connected!")
            self.client = client
            self.session_id = session_id
            self.post_message(ResponseComplete(None))  # Trigger focus back to input
            self.refresh_context()  # Update context bar
            log.info(f"Resume complete for {session_id}")
        except Exception as e:
            log.exception(f"Resume failed: {e}")
            self.post_message(ResponseComplete(None))

    def action_clear(self) -> None:
        chat_view = self.query_one("#chat-view", VerticalScroll)
        chat_view.remove_children()

    def action_copy_selection(self) -> None:
        """Copy selected text to clipboard."""
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.notify("Copied to clipboard")

    def on_mouse_up(self, event: MouseUp) -> None:
        """Auto-copy selection on mouse release."""
        # Small delay to let selection finalize
        self.set_timer(0.05, self._check_and_copy_selection)

    def _check_and_copy_selection(self) -> None:
        """Copy selection if present."""
        selected = self.screen.get_selected_text()
        if selected and len(selected.strip()) > 0:
            self.copy_to_clipboard(selected)

    def action_quit(self) -> None:
        """Quit on double Ctrl+C."""
        now = time.time()
        if hasattr(self, '_last_quit_time') and now - self._last_quit_time < 1.0:
            self.exit()
        else:
            self._last_quit_time = now
            self.notify("Press Ctrl+C again to quit")

    def _show_session_picker(self) -> None:
        """Show the session picker, hiding the chat view."""
        picker = self.query_one("#session-picker", ListView)
        chat_view = self.query_one("#chat-view", VerticalScroll)
        picker.remove_class("hidden")
        chat_view.add_class("hidden")
        self._session_picker_active = True
        self._update_session_picker("")

    def _update_session_picker(self, search: str) -> None:
        """Update session picker with filtered results."""
        picker = self.query_one("#session-picker", ListView)
        picker.clear()
        for session_id, preview, _, msg_count in get_recent_sessions(search=search):
            picker.append(SessionItem(session_id, preview, msg_count))

    def _hide_session_picker(self) -> None:
        """Hide the session picker, show chat view."""
        self._session_picker_active = False
        self.query_one("#session-picker", ListView).add_class("hidden")
        self.query_one("#chat-view", VerticalScroll).remove_class("hidden")
        self.query_one("#input", ChatInput).clear()
        self.query_one("#input", ChatInput).focus()

    def action_cancel_picker(self) -> None:
        """Cancel the session picker with Escape."""
        if self._session_picker_active:
            self._hide_session_picker()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Filter session picker as user types."""
        if self._session_picker_active and event.text_area.id == "input":
            self._update_session_picker(event.text_area.text)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            session_id = event.item.session_id
            log.info(f"Resuming session: {session_id}")
            self._hide_session_picker()
            self._load_and_display_history(session_id)
            self.notify(f"Resuming {session_id[:8]}...")
            self.resume_session(session_id)

    def on_app_focus(self) -> None:
        self.query_one("#input", ChatInput).focus()

    def on_key(self, event) -> None:
        """Redirect typing to input unless a dialog is active."""
        # Don't intercept if SelectionPrompt is active (it handles its own keys)
        if self.query(SelectionPrompt):
            return
        # Don't intercept if input is already focused
        input_widget = self.query_one("#input", ChatInput)
        if self.focused == input_widget:
            return
        # For printable characters, focus input and let it handle the key
        if len(event.character or "") == 1 and event.character.isprintable():
            input_widget.focus()
            input_widget.insert(event.character)
            event.prevent_default()
            event.stop()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Claude Code Textual UI")
    parser.add_argument("--resume", "-r", action="store_true",
                        help="Resume the most recent session")
    parser.add_argument("--session", "-s", type=str,
                        help="Resume a specific session ID")
    args = parser.parse_args()

    resume_id = None
    if args.session:
        resume_id = args.session
    elif args.resume:
        sessions = get_recent_sessions(limit=1)
        if sessions:
            resume_id = sessions[0][0]

    try:
        app = ChatApp(resume_session_id=resume_id)
        app.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        import traceback
        with open("/tmp/cc-textual-crash.log", "w") as f:
            traceback.print_exc(file=f)
        raise
