"""Custom Textual messages for SDK event communication."""

from textual.message import Message

from claude_agent_sdk import ToolUseBlock, ToolResultBlock, ResultMessage


class StreamChunk(Message):
    """Message sent when a chunk of text is received from Claude."""

    def __init__(
        self, text: str, new_message: bool = False, parent_tool_use_id: str | None = None
    ) -> None:
        self.text = text
        self.new_message = new_message  # Start a new ChatMessage widget
        self.parent_tool_use_id = parent_tool_use_id  # If set, belongs to a Task
        super().__init__()


class ResponseComplete(Message):
    """Message sent when Claude's response is complete."""

    def __init__(self, result: ResultMessage | None = None) -> None:
        self.result = result
        super().__init__()


class ToolUseMessage(Message):
    """Message sent when a tool use starts."""

    def __init__(
        self, block: ToolUseBlock, parent_tool_use_id: str | None = None
    ) -> None:
        self.block = block
        self.parent_tool_use_id = parent_tool_use_id
        super().__init__()


class ToolResultMessage(Message):
    """Message sent when a tool result arrives."""

    def __init__(
        self, block: ToolResultBlock, parent_tool_use_id: str | None = None
    ) -> None:
        self.block = block
        self.parent_tool_use_id = parent_tool_use_id
        super().__init__()


class ContextUpdate(Message):
    """Message sent when context usage is known."""

    def __init__(self, tokens: int) -> None:
        self.tokens = tokens
        super().__init__()
