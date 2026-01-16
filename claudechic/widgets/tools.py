"""Tool display widgets - ToolUseWidget and TaskWidget."""

import json
import logging
import re

import pyperclip

from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Markdown, Static, Collapsible, Button

from claude_agent_sdk import ToolUseBlock, ToolResultBlock

from claudechic.formatting import (
    format_tool_header,
    format_tool_details,
    get_lang_from_path,
)
from claudechic.widgets.diff import DiffWidget
from claudechic.widgets.chat import ChatMessage
from claudechic.profiling import profile

log = logging.getLogger(__name__)

# Pattern to strip SDK-injected system reminders from tool results
SYSTEM_REMINDER_PATTERN = re.compile(r"\n*<system-reminder>.*?</system-reminder>\n*", re.DOTALL)


class ToolUseWidget(Static):
    """A collapsible widget showing a tool use."""

    can_focus = False
    SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, block: ToolUseBlock, collapsed: bool = False, completed: bool = False) -> None:
        super().__init__()
        self.block = block
        self.result: ToolResultBlock | bool | None = True if completed else None
        self._initial_collapsed = collapsed
        self._header = format_tool_header(self.block.name, self.block.input)
        self._spinner_frame = 0
        self._spinner_timer = None

    def compose(self) -> ComposeResult:
        yield Button("⧉", classes="copy-btn")
        title = self._header if self.result else f"{self.SPINNER_FRAMES[0]} {self._header}"
        with Collapsible(title=title, collapsed=self._initial_collapsed):
            if self.block.name == "Edit":
                yield DiffWidget(
                    self.block.input.get("old_string", ""),
                    self.block.input.get("new_string", ""),
                    path=self.block.input.get("file_path", ""),
                    replace_all=self.block.input.get("replace_all", False),
                    id="diff-content",
                )
            else:
                details = format_tool_details(self.block.name, self.block.input)
                yield Markdown(details.rstrip(), id="md-content")

    def on_mount(self) -> None:
        if self.result is None:  # Only start spinner for in-progress tools
            self._spinner_timer = self.set_interval(1 / 10, self._tick_spinner)

    @profile
    def _tick_spinner(self) -> None:
        if self.result is not None or self._spinner_timer is None:
            return
        self._spinner_frame = (self._spinner_frame + 1) % len(self.SPINNER_FRAMES)
        try:
            from textual.widgets._collapsible import CollapsibleTitle
            collapsible = self.query_one(Collapsible)
            new_title = f"{self.SPINNER_FRAMES[self._spinner_frame]} {self._header}"
            collapsible.title = new_title
            title_widget = collapsible.query_one(CollapsibleTitle)
            title_widget.label = new_title
        except Exception:
            pass  # Widget may not be fully mounted

    def stop_spinner(self) -> None:
        """Stop the spinner and show static header."""
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self.result is not None:
            return
        self.result = True  # Mark as complete
        try:
            from textual.widgets._collapsible import CollapsibleTitle
            collapsible = self.query_one(Collapsible)
            collapsible.title = self._header
            title_widget = collapsible.query_one(CollapsibleTitle)
            title_widget.label = self._header
            title_widget.refresh()  # Force repaint
        except Exception:
            pass  # Widget may not be fully mounted

    def collapse(self) -> None:
        """Collapse this widget."""
        try:
            self.query_one(Collapsible).collapsed = True
        except Exception:
            pass  # Widget may not be mounted

    def get_copyable_content(self) -> str:
        """Get content suitable for copying."""
        inp = self.block.input
        parts = []
        if self.block.name == "Edit":
            parts.append(f"File: {inp.get('file_path', '?')}")
            if inp.get("old_string"):
                parts.append(f"Old:\n```\n{inp['old_string']}\n```")
            if inp.get("new_string"):
                parts.append(f"New:\n```\n{inp['new_string']}\n```")
        elif self.block.name == "Bash":
            parts.append(f"Command:\n```\n{inp.get('command', '?')}\n```")
        elif self.block.name == "Write":
            parts.append(f"File: {inp.get('file_path', '?')}")
            if inp.get("content"):
                parts.append(f"Content:\n```\n{inp['content']}\n```")
        elif self.block.name == "Read":
            parts.append(f"File: {inp.get('file_path', '?')}")
        else:
            parts.append(json.dumps(inp, indent=2))
        if self.result and self.result is not True and self.result.content:
            content = (
                self.result.content
                if isinstance(self.result.content, str)
                else str(self.result.content)
            )
            content = SYSTEM_REMINDER_PATTERN.sub("", content)
            parts.append(f"Result:\n```\n{content}\n```")
        return "\n\n".join(parts)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if "copy-btn" in event.button.classes:
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
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self.result = result  # Set after stopping timer to prevent race
        log.debug(f"Tool result for {self.block.name}: {len(str(result.content or ''))} chars")
        try:
            from textual.widgets._collapsible import CollapsibleTitle
            collapsible = self.query_one(Collapsible)
            collapsible.title = self._header  # Remove spinner
            title_widget = collapsible.query_one(CollapsibleTitle)
            title_widget.label = self._header
            title_widget.refresh()  # Force repaint
            if result.is_error:
                collapsible.add_class("error")
            # Edit uses Static for diff, others use Markdown
            if self.block.name == "Edit":
                return
            md = collapsible.query_one("#md-content", Markdown)
            details = format_tool_details(self.block.name, self.block.input)
            if result.content:
                content = (
                    result.content
                    if isinstance(result.content, str)
                    else str(result.content)
                )
                # Strip SDK-injected system reminders from display
                content = SYSTEM_REMINDER_PATTERN.sub("", content)
                preview = content[:2000] + ("..." if len(content) > 2000 else "")
                if result.is_error:
                    details += f"\n\n**Error:**\n```\n{preview}\n```"
                elif self.block.name == "Read":
                    lang = get_lang_from_path(self.block.input.get("file_path", ""))
                    # Replace arrow with space in line number gutter
                    preview = re.sub(r"^(\s*\d+)→", r"\1  ", preview, flags=re.MULTILINE)
                    details += f"\n\n```{lang}\n{preview}\n```"
                elif self.block.name in ("Bash", "Grep", "Glob"):
                    details += f"\n\n```text\n{preview}\n```"
                else:
                    details += f"\n\n{preview}"
            md.update(details.rstrip())
        except Exception:
            pass  # Widget may not be fully mounted


class TaskWidget(Static):
    """A collapsible widget showing a Task with nested subagent content."""

    can_focus = False
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
        desc = self.block.input.get("description", "Task")
        agent_type = self.block.input.get("subagent_type", "")
        title = f"Task: {desc}" + (f" ({agent_type})" if agent_type else "")
        with Collapsible(title=title, collapsed=self._initial_collapsed):
            yield Static("", id="task-content")

    def collapse(self) -> None:
        """Collapse this widget."""
        try:
            self.query_one(Collapsible).collapsed = True
        except Exception:
            pass  # Widget may not be mounted

    def stop_spinner(self) -> None:
        """No-op for TaskWidget (no spinner to stop)."""
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
            pass  # Widget may not be mounted

    def add_tool_use(self, block: ToolUseBlock) -> None:
        """Add a tool use from subagent."""
        try:
            content = self.query_one("#task-content", Static)
            while len(self._recent_tools) >= self.RECENT_EXPANDED:
                old = self._recent_tools.pop(0)
                old.collapse()
            widget = ToolUseWidget(block, collapsed=False)
            self._pending_tools[block.id] = widget
            self._recent_tools.append(widget)
            content.mount(widget)
            self._current_message = None
        except Exception:
            pass  # Widget may not be mounted

    def add_tool_result(self, block: ToolResultBlock) -> None:
        """Add a tool result from subagent."""
        widget = self._pending_tools.get(block.tool_use_id)
        if widget:
            widget.set_result(block)
            del self._pending_tools[block.tool_use_id]

    def set_result(self, result: ToolResultBlock) -> None:
        """Set the Task's own result."""
        self.result = result
        # Stop spinners for any nested tools that didn't get results
        for widget in self._pending_tools.values():
            widget.stop_spinner()
        self._pending_tools.clear()
        try:
            collapsible = self.query_one(Collapsible)
            if result.is_error:
                collapsible.add_class("error")
        except Exception:
            pass  # Widget may not be mounted


class AgentToolWidget(Static):
    """Widget for displaying chic agent MCP tool calls (spawn_agent, ask_agent, etc.)."""

    SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    DEFAULT_CSS = """
    AgentToolWidget {
        border-left: solid $panel;
        padding: 0 1;
        margin: 1 0;
    }
    AgentToolWidget .agent-header {
        text-style: bold;
    }
    AgentToolWidget .agent-prompt {
        color: $text-muted;
        margin-left: 2;
    }
    AgentToolWidget .go-btn {
        margin-left: 2;
        min-width: 14;
    }
    AgentToolWidget .result-text {
        margin-top: 1;
        color: $text-muted;
    }
    AgentToolWidget .spinner {
        color: $text-muted;
        margin-left: 2;
    }
    """

    class GoToAgent(Message):
        """Message posted when user clicks 'Go to agent' button."""

        def __init__(self, agent_name: str) -> None:
            super().__init__()
            self.agent_name = agent_name

    def __init__(self, block: ToolUseBlock) -> None:
        super().__init__()
        self.block = block
        self.result: ToolResultBlock | None = None
        self._agent_name = block.input.get("name", "?")
        self._spinner_frame = 0
        self._spinner_timer = None

    def compose(self) -> ComposeResult:
        tool_short = self.block.name.replace("mcp__chic__", "")

        if tool_short == "spawn_agent":
            yield Static(f"Spawning agent: {self._agent_name}", classes="agent-header")
            if prompt := self.block.input.get("prompt"):
                preview = prompt[:80] + "..." if len(prompt) > 80 else prompt
                yield Static(f'"{preview}"', classes="agent-prompt")
            yield Button(f"Go to {self._agent_name}", classes="go-btn")

        elif tool_short == "spawn_worktree":
            yield Static(f"Creating worktree: {self._agent_name}", classes="agent-header")
            if prompt := self.block.input.get("prompt"):
                preview = prompt[:80] + "..." if len(prompt) > 80 else prompt
                yield Static(f'"{preview}"', classes="agent-prompt")
            yield Button(f"Go to {self._agent_name}", classes="go-btn")

        elif tool_short == "ask_agent":
            yield Static(f"Asking agent: {self._agent_name}", classes="agent-header")
            if prompt := self.block.input.get("prompt"):
                preview = prompt[:80] + "..." if len(prompt) > 80 else prompt
                yield Static(f'"{preview}"', classes="agent-prompt")
            yield Static(f"{self.SPINNER_FRAMES[0]} waiting...", classes="spinner", id="ask-spinner")
            yield Button(f"Go to {self._agent_name}", classes="go-btn")

        elif tool_short == "list_agents":
            yield Static("Listing agents", classes="agent-header")

        else:
            # Fallback for unknown chic tools
            yield Static(f"{tool_short}: {self._agent_name}", classes="agent-header")

    def on_mount(self) -> None:
        tool_short = self.block.name.replace("mcp__chic__", "")
        if tool_short == "ask_agent" and self.result is None:
            self._spinner_timer = self.set_interval(1 / 10, self._tick_spinner)

    @profile
    def _tick_spinner(self) -> None:
        if self.result is not None:
            if self._spinner_timer:
                self._spinner_timer.stop()
            return
        self._spinner_frame = (self._spinner_frame + 1) % len(self.SPINNER_FRAMES)
        try:
            spinner = self.query_one("#ask-spinner", Static)
            spinner.update(f"{self.SPINNER_FRAMES[self._spinner_frame]} waiting...")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if "go-btn" in event.button.classes:
            event.stop()
            self.post_message(self.GoToAgent(self._agent_name))

    def collapse(self) -> None:
        """No-op for compatibility with ToolUseWidget interface."""
        pass

    def set_result(self, result: ToolResultBlock) -> None:
        """Update with tool result."""
        self.result = result
        # Stop spinner
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        # Remove spinner widget for ask_agent
        try:
            spinner = self.query_one("#ask-spinner", Static)
            spinner.remove()
        except Exception:
            pass
        # For list_agents, show the result text
        tool_short = self.block.name.replace("mcp__chic__", "")
        if tool_short == "list_agents" and result.content:
            content = result.content if isinstance(result.content, str) else str(result.content)
            content = SYSTEM_REMINDER_PATTERN.sub("", content)
            try:
                self.mount(Static(content, classes="result-text"))
            except Exception:
                pass
