"""ChatView: renders an Agent's message history to widgets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import VerticalScroll

from claudechic.agent import Agent, ChatItem, UserContent, AssistantContent, ToolUse
from claudechic.widgets.chat import ChatMessage, ChatAttachment
from claudechic.widgets.tools import ToolUseWidget, TaskWidget, AgentToolWidget

if TYPE_CHECKING:
    from claude_agent_sdk import ToolUseBlock


# Tools to collapse by default
COLLAPSE_BY_DEFAULT = {"WebSearch", "WebFetch", "AskUserQuestion", "Read", "Glob", "Grep"}


class ChatView(VerticalScroll):
    """A scrollable view that renders an Agent's message history.

    This widget renders `agent.messages` to Textual widgets. It supports:
    - Full re-render (on agent switch)
    - Incremental updates (for streaming responses)

    The view maintains a mapping from message indices to widgets for efficient updates.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._agent: Agent | None = None
        self._rendered_count = 0  # Number of messages rendered
        self._current_assistant_widget: ChatMessage | None = None
        self._tool_widgets: dict[str, ToolUseWidget | TaskWidget | AgentToolWidget] = {}

    def set_agent(self, agent: Agent | None) -> None:
        """Set the agent to render. Triggers full re-render."""
        self._agent = agent
        self._render_full()

    def _render_full(self) -> None:
        """Fully re-render the chat view from agent.messages."""
        # Clear existing content
        self.remove_children()
        self._rendered_count = 0
        self._current_assistant_widget = None
        self._tool_widgets.clear()

        if not self._agent:
            return

        # Render all messages
        for item in self._agent.messages:
            self._render_item(item)

        self._rendered_count = len(self._agent.messages)
        self.scroll_end(animate=False)

    def _render_item(self, item: ChatItem) -> None:
        """Render a single chat item."""
        if item.role == "user" and isinstance(item.content, UserContent):
            self._render_user(item.content)
        elif item.role == "assistant" and isinstance(item.content, AssistantContent):
            self._render_assistant(item.content)

    def _render_user(self, content: UserContent) -> None:
        """Render a user message."""
        msg = ChatMessage(content.text)
        msg.add_class("user-message")
        self.mount(msg)

        # Render image attachments as clickable tags
        for i, (filename, _) in enumerate(content.images):
            if filename.lower().startswith("screenshot"):
                display_name = f"Screenshot #{i + 1}"
            else:
                display_name = filename
            self.mount(ChatAttachment(filename, display_name))

    def _render_assistant(self, content: AssistantContent) -> None:
        """Render an assistant message."""
        # Render text if present
        if content.text:
            msg = ChatMessage(content.text)
            msg.add_class("assistant-message")
            self.mount(msg)
            self._current_assistant_widget = msg

        # Render tool uses
        for tool in content.tool_uses:
            self._render_tool(tool)

    def _render_tool(self, tool: ToolUse) -> None:
        """Render a tool use."""
        collapsed = tool.name in COLLAPSE_BY_DEFAULT
        completed = tool.result is not None

        # Create appropriate widget based on tool type
        if tool.name == "Task":
            widget = TaskWidget(
                _make_tool_block(tool),
                collapsed=collapsed,
            )
        elif tool.name.startswith("mcp__chic__"):
            widget = AgentToolWidget(_make_tool_block(tool))
        else:
            widget = ToolUseWidget(
                _make_tool_block(tool),
                collapsed=collapsed,
                completed=completed,
            )

        self._tool_widgets[tool.id] = widget
        self.mount(widget)

    def update_incremental(self) -> None:
        """Update the view incrementally from agent state.

        Call this when agent.messages changes (new text, new tools, etc.)
        for efficient updates without full re-render.
        """
        if not self._agent:
            return

        messages = self._agent.messages

        # Handle new messages
        while self._rendered_count < len(messages):
            item = messages[self._rendered_count]
            self._render_item(item)
            self._rendered_count += 1

        # Update current assistant message (for streaming)
        if messages and messages[-1].role == "assistant":
            content = messages[-1].content
            if isinstance(content, AssistantContent):
                self._update_current_assistant(content)

        self.scroll_end(animate=False)

    def _update_current_assistant(self, content: AssistantContent) -> None:
        """Update the current assistant message widget."""
        # Handle new tool uses not yet rendered
        for tool in content.tool_uses:
            if tool.id not in self._tool_widgets:
                self._render_tool(tool)
            else:
                # Update existing tool widget if result arrived
                widget = self._tool_widgets.get(tool.id)
                if widget and tool.result is not None:
                    # Create a fake ToolResultBlock for the widget
                    from claude_agent_sdk import ToolResultBlock
                    result = ToolResultBlock(
                        tool_use_id=tool.id,
                        content=tool.result,
                        is_error=tool.is_error,
                    )
                    widget.set_result(result)

    def show_thinking(self) -> None:
        """Show the thinking indicator (now handled at app level)."""
        pass

    def hide_thinking(self) -> None:
        """Hide the thinking indicator (now handled at app level)."""
        pass


def _make_tool_block(tool: ToolUse) -> "ToolUseBlock":
    """Create a ToolUseBlock from our ToolUse dataclass."""
    from claude_agent_sdk import ToolUseBlock
    return ToolUseBlock(
        id=tool.id,
        name=tool.name,
        input=tool.input,
    )
