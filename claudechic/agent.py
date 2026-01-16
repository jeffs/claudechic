"""Agent: autonomous Claude agent with SDK connection and message history."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import (
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    StreamEvent,
    ToolPermissionContext,
)

from claudechic.file_index import FileIndex
from claudechic.permissions import PermissionRequest

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message types for chat history
# ---------------------------------------------------------------------------


@dataclass
class ImageAttachment:
    """An image attached to a message."""

    path: str
    filename: str
    media_type: str
    base64_data: str


@dataclass
class UserContent:
    """A user message in chat history."""

    text: str
    images: list[ImageAttachment] = field(default_factory=list)


@dataclass
class ToolUse:
    """A tool use within an assistant turn."""

    id: str
    name: str
    input: dict[str, Any]
    result: str | None = None
    is_error: bool = False


@dataclass
class AssistantContent:
    """An assistant message in chat history."""

    text: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)


@dataclass
class ChatItem:
    """A single item in chat history."""

    role: Literal["user", "assistant"]
    content: UserContent | AssistantContent


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------


class Agent:
    """Autonomous Claude agent with its own SDK connection and state.

    The Agent owns:
    - SDK client and connection lifecycle
    - Message history (list of ChatItem)
    - Permission request queue
    - Per-agent state (images, todos, file index, etc.)

    Events are emitted via callbacks for UI integration.
    """

    # Tools to auto-approve when auto_approve_edits is True
    AUTO_EDIT_TOOLS = {"Edit", "Write"}

    def __init__(
        self,
        name: str,
        cwd: Path,
        *,
        id: str | None = None,
        worktree: str | None = None,
    ):
        # Identity
        self.id = id or str(uuid.uuid4())[:8]
        self.name = name
        self.cwd = cwd
        self.worktree = worktree

        # SDK
        self.client: ClaudeSDKClient | None = None
        self.session_id: str | None = None
        self._response_task: asyncio.Task | None = None

        # Status
        self.status: Literal["idle", "busy", "needs_input"] = "idle"
        self._thinking: bool = False  # Whether this agent is currently thinking

        # Chat history
        self.messages: list[ChatItem] = []
        self._current_assistant: AssistantContent | None = None
        self._current_text_buffer: str = ""

        # Permission queue
        self.pending_prompts: deque[PermissionRequest] = deque()

        # Tool tracking (within current response)
        self.pending_tools: dict[str, ToolUse] = {}
        self.active_tasks: dict[str, str] = {}  # task_id -> accumulated text
        self.response_had_tools: bool = False
        self._needs_new_message: bool = True  # Start new ChatMessage on next text
        self._thinking_hidden: bool = False  # Track if thinking indicator was hidden this response

        # Per-agent state
        self.pending_images: list[ImageAttachment] = []
        self.file_index: FileIndex | None = None
        self.todos: list[dict] = []
        self.auto_approve_edits: bool = False

        # Worktree finish state (for /worktree finish flow)
        self.finish_state: Any = None

        # UI state - ChatView reference and active prompt
        self.chat_view: Any = None  # ChatView widget (set by ChatApp)
        self.active_prompt: Any = None  # Active SelectionPrompt/QuestionPrompt
        self.pending_input: str = ""  # Saved input text when switching away

        # MCP ask_agent support
        self._completion_event = asyncio.Event()
        self._last_response: str | None = None

        # Callbacks for UI integration (set by ChatApp/AgentManager)
        self.on_status_changed: Callable[[Agent], None] | None = None
        self.on_message_updated: Callable[[Agent], None] | None = None
        self.on_prompt_added: Callable[[Agent, PermissionRequest], None] | None = None
        self.on_error: Callable[[Agent, str, Exception | None], None] | None = None
        self.on_complete: Callable[[Agent, ResultMessage | None], None] | None = None
        self.on_todos_updated: Callable[[Agent], None] | None = None

        # Fine-grained callbacks for streaming UI updates
        # These are called in addition to on_message_updated
        self.on_text_chunk: Callable[[Agent, str, bool, str | None], None] | None = None  # agent, text, new_message, parent_tool_id
        self.on_tool_use: Callable[[Agent, ToolUse], None] | None = None
        self.on_tool_result: Callable[[Agent, ToolUse], None] | None = None
        self.on_system_message: Callable[[Agent, SystemMessage], None] | None = None
        self.on_command_output: Callable[[Agent, str], None] | None = None  # agent, markdown_content

        # Permission UI callback (ChatApp provides this to show prompts)
        self.permission_ui_callback: (
            Callable[[Agent, PermissionRequest], Awaitable[str]] | None
        ) = None

        # Callback when send() is called (for UI to display user message)
        # args: agent, prompt, images
        self.on_prompt_sent: Callable[[Agent, str, list[ImageAttachment]], None] | None = None

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def connect(
        self,
        options: ClaudeAgentOptions,
        resume: str | None = None,
    ) -> None:
        """Connect to SDK.

        Args:
            options: SDK options (should have can_use_tool set to self._handle_permission)
            resume: Optional session ID to resume
        """
        # Inject our permission handler
        options.can_use_tool = self._handle_permission

        self.client = ClaudeSDKClient(options)
        await self.client.connect()

        if resume:
            self.session_id = resume

        # Initialize file index
        self.file_index = FileIndex(root=self.cwd)
        await self.file_index.refresh()

    async def disconnect(self) -> None:
        """Disconnect and cleanup."""
        if self._response_task and not self._response_task.done():
            self._response_task.cancel()
            try:
                await self._response_task
            except asyncio.CancelledError:
                pass

        if self.client:
            try:
                await self.client.interrupt()
            except Exception:
                pass
            self.client = None

    async def load_history(self, limit: int = 50, cwd: Path | None = None) -> None:
        """Load message history from session file into self.messages.

        This populates Agent.messages from the persisted session,
        making Agent.messages the single source of truth for history.
        Call ChatView._render_full() after this to update UI.

        Args:
            limit: Maximum number of messages to load
            cwd: Working directory for session lookup (defaults to self.cwd)
        """
        from claudechic.sessions import load_session_messages

        if not self.session_id:
            return

        self.messages.clear()
        raw_messages = await load_session_messages(
            self.session_id, limit=limit, cwd=cwd or self.cwd
        )

        current_assistant: AssistantContent | None = None

        for m in raw_messages:
            if m["type"] == "user":
                # Flush any pending assistant content
                if current_assistant is not None:
                    self.messages.append(
                        ChatItem(role="assistant", content=current_assistant)
                    )
                    current_assistant = None
                # Add user message
                self.messages.append(
                    ChatItem(role="user", content=UserContent(text=m["content"]))
                )
            elif m["type"] == "assistant":
                # Start or continue assistant content
                if current_assistant is None:
                    current_assistant = AssistantContent(text=m["content"])
                else:
                    # Append to existing (shouldn't happen often with current parser)
                    current_assistant.text += "\n" + m["content"]
            elif m["type"] == "tool_use":
                # Add tool use to current assistant content
                if current_assistant is None:
                    current_assistant = AssistantContent()
                current_assistant.tool_uses.append(
                    ToolUse(
                        id=m.get("id", ""),
                        name=m["name"],
                        input=m.get("input", {}),
                    )
                )

        # Flush final assistant content
        if current_assistant is not None:
            self.messages.append(
                ChatItem(role="assistant", content=current_assistant)
            )

        log.info(f"Loaded {len(self.messages)} messages from session {self.session_id}")

    # -----------------------------------------------------------------------
    # Sending messages
    # -----------------------------------------------------------------------

    def attach_image(self, path: Path) -> ImageAttachment | None:
        """Attach an image to the next message.

        Returns ImageAttachment on success, None on failure.
        """
        try:
            data = base64.b64encode(path.read_bytes()).decode()
            media_type = mimetypes.guess_type(str(path))[0] or "image/png"
            img = ImageAttachment(str(path), path.name, media_type, data)
            self.pending_images.append(img)
            return img
        except Exception:
            return None

    def clear_images(self) -> None:
        """Clear pending images."""
        self.pending_images.clear()

    async def send(self, prompt: str) -> None:
        """Send a message and start processing response.

        The response is processed concurrently - this method returns immediately.
        """
        if not self.client:
            raise RuntimeError("Agent not connected")

        # Add user message to history
        self.messages.append(
            ChatItem(role="user", content=UserContent(text=prompt, images=list(self.pending_images)))
        )

        # Notify UI to display user message (pass full image info before clearing)
        if self.on_prompt_sent:
            self.on_prompt_sent(self, prompt, list(self.pending_images))

        self._set_status("busy")
        self.response_had_tools = False
        self._completion_event.clear()
        self._current_assistant = None
        self._current_text_buffer = ""
        self._needs_new_message = True
        self._thinking_hidden = False  # Reset for new response

        # Start response processing
        self._response_task = asyncio.create_task(
            self._process_response(prompt),
            name=f"agent-{self.id}-response",
        )

    async def interrupt(self) -> None:
        """Interrupt current response."""
        if self._response_task and not self._response_task.done():
            self._response_task.cancel()
            try:
                await self._response_task
            except asyncio.CancelledError:
                pass

        if self.client:
            try:
                await self.client.interrupt()
            except Exception:
                pass

        self._set_status("idle")

    async def wait_for_completion(self, timeout: float = 300) -> str | None:
        """Wait for current response to complete. Returns response text.

        Used by MCP ask_agent tool.
        """
        try:
            await asyncio.wait_for(self._completion_event.wait(), timeout=timeout)
            return self._last_response
        except asyncio.TimeoutError:
            return None

    # -----------------------------------------------------------------------
    # Response processing
    # -----------------------------------------------------------------------

    async def _process_response(self, prompt: str) -> None:
        """Process SDK response stream."""
        try:
            # Send message with images if any
            if self.pending_images:
                message = self._build_message_with_images(prompt)
                if self.client and self.client._transport:
                    await self.client._transport.write(json.dumps(message) + "\n")
                self.pending_images.clear()
            else:
                await self.client.query(prompt)  # type: ignore[union-attr]

            had_tool_use: dict[str | None, bool] = {}

            async for message in self.client.receive_response():  # type: ignore[union-attr]
                await self._handle_sdk_message(message, had_tool_use)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Response processing failed")
            self._emit_error("Response failed", e)
            self._emit_complete(None)
        finally:
            self._flush_current_text()
            self._set_status("idle")
            self._completion_event.set()

    async def _handle_sdk_message(
        self, message: Any, had_tool_use: dict[str | None, bool]
    ) -> None:
        """Handle a single SDK message."""
        if isinstance(message, AssistantMessage):
            parent_id = message.parent_tool_use_id
            for block in message.content:
                # Skip TextBlock - handled via StreamEvent for streaming
                if isinstance(block, ToolUseBlock):
                    self._handle_tool_use(block, parent_id)
                    had_tool_use[parent_id] = True
                elif isinstance(block, ToolResultBlock):
                    self._handle_tool_result(block)

        elif isinstance(message, UserMessage):
            # UserMessage can contain tool results or command output
            content = getattr(message, "content", "")
            if isinstance(content, str):
                # Handle local command output (e.g., /context)
                if "<local-command-stdout>" in content:
                    self._handle_command_output(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        self._handle_tool_result(block)

        elif isinstance(message, StreamEvent):
            self._handle_stream_event(message)

        elif isinstance(message, SystemMessage):
            self._handle_system_message(message)

        elif isinstance(message, ResultMessage):
            self._flush_current_text()
            self.session_id = message.session_id
            self._last_response = message.result or ""
            self._emit_complete(message)

    def _handle_system_message(self, message: SystemMessage) -> None:
        """Handle system message from SDK - emit callback for UI."""
        if self.on_system_message:
            self.on_system_message(self, message)

    def _handle_text_chunk(
        self, text: str, new_message: bool, parent_tool_id: str | None
    ) -> None:
        """Handle incoming text chunk."""
        # If this belongs to a Task, accumulate there
        if parent_tool_id and parent_tool_id in self.active_tasks:
            self.active_tasks[parent_tool_id] += text
            return

        if new_message:
            self._flush_current_text()

        # Ensure we have an assistant content to accumulate into
        if self._current_assistant is None:
            self._current_assistant = AssistantContent()
            self.messages.append(
                ChatItem(role="assistant", content=self._current_assistant)
            )

        self._current_text_buffer += text
        self._current_assistant.text = self._current_text_buffer
        self._emit_message_updated()

        # Emit fine-grained callback for UI streaming
        if self.on_text_chunk:
            self.on_text_chunk(self, text, new_message, parent_tool_id)

    def _handle_stream_event(self, event: StreamEvent) -> None:
        """Handle streaming event from SDK."""
        ev = event.event
        ev_type = ev.get("type")
        parent_id = event.parent_tool_use_id

        if ev_type == "content_block_delta":
            delta = ev.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    # Start new message after tool use or at start of response
                    new_msg = self._needs_new_message
                    self._needs_new_message = False
                    self._handle_text_chunk(text, new_msg, parent_id)

    def _flush_current_text(self) -> None:
        """Flush accumulated text to current assistant message."""
        if self._current_assistant and self._current_text_buffer:
            self._current_assistant.text = self._current_text_buffer
            self._current_text_buffer = ""
            self._emit_message_updated()

    def _handle_command_output(self, content: str) -> None:
        """Handle command output from UserMessage (e.g., /context)."""
        import re
        # Extract content from <local-command-stdout>...</local-command-stdout>
        match = re.search(r"<local-command-stdout>(.*?)</local-command-stdout>", content, re.DOTALL)
        if match and self.on_command_output:
            self.on_command_output(self, match.group(1).strip())

    def _handle_tool_use(self, block: ToolUseBlock, parent_tool_id: str | None) -> None:  # noqa: ARG002
        """Handle tool use start."""
        self._flush_current_text()
        self.response_had_tools = True
        self._needs_new_message = True  # Next text chunk starts a new ChatMessage

        # TodoWrite updates todos
        if block.name == "TodoWrite":
            self.todos = block.input.get("todos", [])
            if self.on_todos_updated:
                self.on_todos_updated(self)
            return

        tool = ToolUse(id=block.id, name=block.name, input=block.input)

        # Track Task tools specially
        if block.name == "Task":
            self.active_tasks[block.id] = ""

        self.pending_tools[block.id] = tool

        # Add to current assistant content
        if self._current_assistant is None:
            self._current_assistant = AssistantContent()
            self.messages.append(
                ChatItem(role="assistant", content=self._current_assistant)
            )
        self._current_assistant.tool_uses.append(tool)
        self._emit_message_updated()

        # Emit fine-grained callback for UI
        if self.on_tool_use:
            self.on_tool_use(self, tool)

    def _handle_tool_result(self, block: ToolResultBlock) -> None:
        """Handle tool result."""
        tool = self.pending_tools.pop(block.tool_use_id, None)
        if tool:
            tool.result = block.content if isinstance(block.content, str) else str(block.content)
            tool.is_error = block.is_error or False
            self._emit_message_updated()

            # Emit fine-grained callback for UI
            if self.on_tool_result:
                self.on_tool_result(self, tool)

        # Clean up active tasks
        self.active_tasks.pop(block.tool_use_id, None)

    # -----------------------------------------------------------------------
    # Permissions
    # -----------------------------------------------------------------------

    async def _handle_permission(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,  # noqa: ARG002
    ) -> PermissionResult:
        """Handle permission request from SDK."""
        log.info(f"Permission requested for {tool_name}: {str(tool_input)[:100]}")

        # AskUserQuestion needs special handling
        if tool_name == "AskUserQuestion":
            return await self._handle_ask_user_question(tool_input)

        # Always allow ExitPlanMode and chic MCP tools
        if tool_name == "ExitPlanMode":
            return PermissionResultAllow()
        if tool_name.startswith("mcp__chic__"):
            return PermissionResultAllow()

        # Auto-approve edits if enabled
        if self.auto_approve_edits and tool_name in self.AUTO_EDIT_TOOLS:
            log.info(f"Auto-approved {tool_name}")
            return PermissionResultAllow()

        # Create permission request and queue it
        request = PermissionRequest(tool_name, tool_input)
        self.pending_prompts.append(request)
        if self.on_prompt_added:
            self.on_prompt_added(self, request)

        self._set_status("needs_input")

        # Wait for UI to respond
        if self.permission_ui_callback:
            result = await self.permission_ui_callback(self, request)
        else:
            # No UI callback - wait for programmatic response
            result = await request.wait()

        # Remove from queue
        if request in self.pending_prompts:
            self.pending_prompts.remove(request)

        self._set_status("busy")

        log.info(f"Permission result: {result}")
        if result == "allow_all":
            self.auto_approve_edits = True
            return PermissionResultAllow()
        elif result == "allow":
            return PermissionResultAllow()
        else:
            return PermissionResultDeny(message="User denied permission")

    async def _handle_ask_user_question(
        self, tool_input: dict[str, Any]
    ) -> PermissionResult:
        """Handle AskUserQuestion tool - needs UI to collect answers."""
        questions = tool_input.get("questions", [])
        if not questions:
            return PermissionResultAllow(updated_input=tool_input)

        # Create a special request for question prompts
        request = PermissionRequest("AskUserQuestion", tool_input)
        self.pending_prompts.append(request)
        if self.on_prompt_added:
            self.on_prompt_added(self, request)

        self._set_status("needs_input")

        # The UI callback should handle question collection
        if self.permission_ui_callback:
            result = await self.permission_ui_callback(self, request)
        else:
            result = await request.wait()

        if request in self.pending_prompts:
            self.pending_prompts.remove(request)

        self._set_status("busy")

        if result == "deny":
            return PermissionResultDeny(message="User cancelled questions")

        # Result should be the answers dict (stored in request._result by UI)
        answers = getattr(request, "_answers", {})
        return PermissionResultAllow(
            updated_input={"questions": questions, "answers": answers}
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _set_status(self, status: Literal["idle", "busy", "needs_input"]) -> None:
        """Update status and emit event."""
        if self.status != status:
            self.status = status
            if self.on_status_changed:
                self.on_status_changed(self)

    def _emit_message_updated(self) -> None:
        """Emit message update event."""
        if self.on_message_updated:
            self.on_message_updated(self)

    def _emit_error(self, message: str, exception: Exception | None) -> None:
        """Emit error event."""
        if self.on_error:
            self.on_error(self, message, exception)

    def _emit_complete(self, result: ResultMessage | None) -> None:
        """Emit completion event."""
        if self.on_complete:
            self.on_complete(self, result)

    def _build_message_with_images(self, prompt: str) -> dict[str, Any]:
        """Build SDK message with text and images."""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in self.pending_images:
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": img.media_type, "data": img.base64_data},
                }
            )
        return {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }

