"""Claude Code Textual UI - Main application."""

import asyncio
import base64
from contextlib import asynccontextmanager
import json
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

from textual.app import App, ComposeResult

from claude_alamode.theme import ALAMODE_THEME
from textual.binding import Binding
from textual.containers import VerticalScroll, Vertical, Horizontal
from textual.events import MouseUp
from textual.widgets import ListView, TextArea
from textual import work

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
)

from claude_alamode.messages import (
    StreamChunk,
    ResponseComplete,
    ToolUseMessage,
    ToolResultMessage,
)
from claude_alamode.sessions import get_context_from_session, get_recent_sessions, load_session_messages
from claude_alamode.features.worktree import (
    handle_worktree_command,
    list_worktrees,
)
from claude_alamode.features.worktree.commands import on_response_complete_finish
from claude_alamode.permissions import PermissionRequest
from claude_alamode.agent import AgentSession, create_agent_session
from claude_alamode.file_index import FileIndex
from claude_alamode.widgets import (
    ContextBar,
    ChatMessage,
    ChatInput,
    ChatAttachment,
    ThinkingIndicator,
    ImageAttachments,
    ErrorMessage,
    ToolUseWidget,
    TaskWidget,
    TodoWidget,
    TodoPanel,
    SelectionPrompt,
    QuestionPrompt,
    SessionItem,
    TextAreaAutoComplete,
    AgentSidebar,
    AgentItem,
    WorktreeItem,
    AutoHideScroll,
)
from claude_alamode.widgets.footer import StatusFooter
from claude_alamode.errors import log_exception, setup_logging

log = logging.getLogger(__name__)


def _scroll_if_at_bottom(scroll_view: VerticalScroll) -> None:
    """Scroll to end only if user hasn't scrolled up."""
    # Force layout recalculation to ensure scroll metrics are accurate
    scroll_view.refresh(layout=True)
    # Consider "at bottom" if within 50px of the end
    at_bottom = scroll_view.scroll_y >= scroll_view.max_scroll_y - 50
    if at_bottom:
        scroll_view.scroll_end(animate=False)


class ChatApp(App):
    """Main chat application."""

    TITLE = "Claude à la Mode"
    CSS_PATH = Path(__file__).parent / "styles.tcss"

    BINDINGS = [
        Binding("ctrl+y", "copy_selection", "Copy", priority=True, show=False),
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        Binding("ctrl+l", "clear", "Clear", show=False),
        Binding("ctrl+s", "screenshot", "Screenshot", show=False),
        Binding("shift+tab", "cycle_permission_mode", "Auto-edit", priority=True, show=False),
        Binding("escape", "escape", "Cancel", show=False),
        Binding("ctrl+n", "new_agent", "New Agent", priority=True, show=False),
        # Agent switching: ctrl+1 through ctrl+9
        *[Binding(f"ctrl+{i}", f"switch_agent({i})", f"Agent {i}", priority=True, show=False) for i in range(1, 10)],
    ]

    # Auto-approve Edit/Write tools (but still prompt for Bash, etc.)
    AUTO_EDIT_TOOLS = {"Edit", "Write"}

    # Tools to collapse by default
    COLLAPSE_BY_DEFAULT = {"WebSearch", "WebFetch", "AskUserQuestion", "Read", "Glob", "Grep"}

    RECENT_TOOLS_EXPANDED = 2

    # Width threshold for showing sidebar
    SIDEBAR_MIN_WIDTH = 140

    def __init__(self, resume_session_id: str | None = None, initial_prompt: str | None = None) -> None:
        super().__init__()
        # Multi-agent state
        self.agents: dict[str, AgentSession] = {}
        self.active_agent_id: str | None = None
        self._resume_on_start = resume_session_id
        self._initial_prompt = initial_prompt
        self._session_picker_active = False
        # Event queues for testing
        self.interactions: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        self.completions: asyncio.Queue[ResponseComplete] = asyncio.Queue()
        # Pending images to attach to next message
        self.pending_images: list[tuple[str, str, str, str]] = []  # (path, filename, media_type, base64_data)
        # File index for fuzzy file search
        self.file_index: FileIndex | None = None

    # Properties to access active agent's state (backward compatibility)
    @property
    def _agent(self) -> AgentSession | None:
        """Get the active agent session."""
        if self.active_agent_id and self.active_agent_id in self.agents:
            return self.agents[self.active_agent_id]
        return None

    @property
    def client(self) -> ClaudeSDKClient | None:
        return self._agent.client if self._agent else None

    @client.setter
    def client(self, value: ClaudeSDKClient | None) -> None:
        if self._agent:
            self._agent.client = value

    @property
    def session_id(self) -> str | None:
        return self._agent.session_id if self._agent else None

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        if self._agent:
            self._agent.session_id = value

    @property
    def sdk_cwd(self) -> Path:
        return self._agent.cwd if self._agent else Path.cwd()

    @sdk_cwd.setter
    def sdk_cwd(self, value: Path) -> None:
        if self._agent:
            self._agent.cwd = value

    @property
    def current_response(self) -> ChatMessage | None:
        return self._agent.current_response if self._agent else None

    @current_response.setter
    def current_response(self, value: ChatMessage | None) -> None:
        if self._agent:
            self._agent.current_response = value

    @property
    def pending_tools(self) -> dict[str, ToolUseWidget | TaskWidget]:
        return self._agent.pending_tools if self._agent else {}

    @property
    def active_tasks(self) -> dict[str, TaskWidget]:
        return self._agent.active_tasks if self._agent else {}

    @property
    def recent_tools(self) -> list[ToolUseWidget | TaskWidget]:
        return self._agent.recent_tools if self._agent else []

    @property
    def _chat_view(self) -> VerticalScroll | None:
        """Get the active agent's chat view."""
        return self._agent.chat_view if self._agent else None

    def _get_agent(self, agent_id: str | None) -> AgentSession | None:
        """Get agent by ID, or active agent if None."""
        return self.agents.get(agent_id) if agent_id else self._agent

    def _set_agent_status(self, status: Literal["idle", "busy", "needs_input"], agent_id: str | None = None) -> None:
        """Update an agent's status and sidebar display."""
        aid = agent_id or self.active_agent_id
        if not aid or aid not in self.agents:
            return
        self.agents[aid].status = status
        try:
            sidebar = self.query_one("#agent-sidebar", AgentSidebar)
            sidebar.update_status(aid, status)
        except Exception:
            pass  # Sidebar not mounted yet

    def show_error(self, message: str, exception: Exception | None = None) -> None:
        """Display an error message in the chat view and log to file.

        Args:
            message: Brief description of what failed
            exception: Optional exception for logging (full traceback logged to file)
        """
        chat_view = self._chat_view
        if chat_view:
            error_widget = ErrorMessage(message, exception)
            chat_view.mount(error_widget)
            self.call_after_refresh(_scroll_if_at_bottom, chat_view)
        # Also show toast for visibility
        self.notify(message, severity="error")

    async def _replace_client(self, options: ClaudeAgentOptions) -> None:
        """Safely replace current client with a new one."""
        # Cancel any permission prompts waiting for user input
        for prompt in list(self.query(SelectionPrompt)) + list(self.query(QuestionPrompt)):
            prompt.cancel()
        old = self.client
        self.client = None
        if old:
            try:
                await old.interrupt()
            except Exception:
                pass
            # Skip disconnect() - it causes race conditions with SDK cleanup.
            # interrupt() is sufficient to stop the subprocess.
        new_client = ClaudeSDKClient(options)
        await new_client.connect()
        self.client = new_client

    def _attach_image(self, path: Path) -> None:
        """Read and queue image for next message."""
        try:
            data = base64.b64encode(path.read_bytes()).decode()
            media_type = mimetypes.guess_type(str(path))[0] or "image/png"
            self.pending_images.append((str(path), path.name, media_type, data))
            # Update visual indicator
            self.query_one("#image-attachments", ImageAttachments).add_image(path.name)
        except Exception as e:
            self.notify(f"Failed to attach {path.name}: {e}", severity="error")

    def on_image_attachments_removed(self, event: ImageAttachments.Removed) -> None:
        """Handle removal of an image attachment."""
        self.pending_images = [
            (path, name, media, data) for path, name, media, data in self.pending_images
            if name != event.filename
        ]

    def _build_message_with_images(self, prompt: str) -> dict[str, Any]:
        """Build a message dict with text and any pending images."""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for _path, _filename, media_type, data in self.pending_images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data}
            })
        self.pending_images.clear()
        # Clear visual indicator
        try:
            self.query_one("#image-attachments", ImageAttachments).clear()
        except Exception:
            pass  # Widget may not exist yet
        return {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }

    @asynccontextmanager
    async def _show_prompt(self, prompt):
        """Show a prompt widget, hiding input container. Restores on exit."""
        agent = self._agent
        if agent:
            agent.active_prompt = prompt
        input_container = self.query_one("#input-container")
        input_container.add_class("hidden")
        self.query_one("#input-wrapper").mount(prompt)
        try:
            yield prompt
        finally:
            if agent:
                agent.active_prompt = None
            try:
                prompt.remove()
            except Exception:
                pass  # Prompt may already be removed
            input_container.remove_class("hidden")

    async def _handle_permission(
        self, tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResult:
        """Handle permission request from SDK."""
        log.info(f"Permission requested for {tool_name}: {str(tool_input)[:100]}")

        if tool_name == "AskUserQuestion":
            return await self._handle_ask_user_question(tool_input)

        # ExitPlanMode has no side effects - always allow
        if tool_name == "ExitPlanMode":
            return PermissionResultAllow()

        if self._agent and self._agent.auto_approve_edits and tool_name in self.AUTO_EDIT_TOOLS:
            log.info(f"Auto-approved {tool_name}")
            return PermissionResultAllow()

        request = PermissionRequest(tool_name, tool_input)
        await self.interactions.put(request)

        options = [("allow", "Yes, this time only"), ("deny", "No")]
        if tool_name in self.AUTO_EDIT_TOOLS:
            options.insert(0, ("allow_all", "Yes, all edits in this session"))

        self._set_agent_status("needs_input")
        async with self._show_prompt(SelectionPrompt(request.title, options)) as prompt:
            async def ui_response():
                result = await prompt.wait()
                if not request._event.is_set():
                    request.respond(result)

            # Run UI response handler concurrently - allows both UI and programmatic responses
            asyncio.create_task(ui_response())
            result = await request.wait()

        self._set_agent_status("busy")
        log.info(f"Permission result: {result}")
        if result == "allow_all":
            if self._agent:
                self._agent.auto_approve_edits = True
                self._update_footer_auto_edit()
            self.notify("Auto-edit enabled (Shift+Tab to disable)")
            return PermissionResultAllow()
        elif result == "allow":
            return PermissionResultAllow()
        else:
            return PermissionResultDeny(message="User denied permission")

    async def _handle_ask_user_question(
        self, tool_input: dict[str, Any]
    ) -> PermissionResult:
        """Handle AskUserQuestion tool."""
        questions = tool_input.get("questions", [])
        if not questions:
            return PermissionResultAllow(updated_input=tool_input)

        log.info(f"AskUserQuestion with {len(questions)} questions")

        async with self._show_prompt(QuestionPrompt(questions)) as prompt:
            answers = await prompt.wait()

        if not answers:
            return PermissionResultDeny(message="User cancelled questions")

        log.info(f"AskUserQuestion answers: {answers}")
        return PermissionResultAllow(
            updated_input={"questions": questions, "answers": answers}
        )

    def action_cycle_permission_mode(self) -> None:
        """Toggle auto-approve for Edit/Write tools for current agent."""
        if self._agent:
            self._agent.auto_approve_edits = not self._agent.auto_approve_edits
            self._update_footer_auto_edit()
            self.notify(f"Auto-edit: {'ON' if self._agent.auto_approve_edits else 'OFF'}")

    def _update_footer_auto_edit(self) -> None:
        """Update footer to reflect current agent's auto-edit state."""
        try:
            footer = self.query_one(StatusFooter)
            footer.auto_edit = self._agent.auto_approve_edits if self._agent else False
        except Exception:
            pass  # Footer may not be mounted yet

    # Built-in slash commands (local to this app)
    LOCAL_COMMANDS = ["/clear", "/resume", "/worktree", "/worktree finish", "/worktree cleanup", "/agent", "/agent close", "/shell", "/theme"]

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield ListView(id="session-picker", classes="hidden")
            yield AutoHideScroll(id="chat-view")
            with Vertical(id="right-sidebar", classes="hidden"):
                yield AgentSidebar(id="agent-sidebar")
                yield TodoPanel(id="todo-panel")
        with Horizontal(id="input-wrapper"):
            with Vertical(id="input-container"):
                yield ImageAttachments(id="image-attachments", classes="hidden")
                yield ChatInput(id="input")
                yield TextAreaAutoComplete(
                    "#input",
                    slash_commands=self.LOCAL_COMMANDS,  # Updated in on_mount
                )
        yield StatusFooter()

    def _make_options(
        self, cwd: Path | None = None, resume: str | None = None
    ) -> ClaudeAgentOptions:
        """Create SDK options with common settings."""
        return ClaudeAgentOptions(
            permission_mode="default",
            env={"ANTHROPIC_API_KEY": ""},
            setting_sources=["user", "project", "local"],
            cwd=cwd,
            resume=resume,
            can_use_tool=self._handle_permission,
        )

    async def on_mount(self) -> None:
        # Register and activate custom theme
        self.register_theme(ALAMODE_THEME)
        self.theme = "alamode"

        # Create initial agent session
        cwd = Path.cwd()
        agent = create_agent_session(name=cwd.name, cwd=cwd)
        agent.chat_view = self.query_one("#chat-view", AutoHideScroll)
        self.agents[agent.id] = agent
        self.active_agent_id = agent.id

        # Add to sidebar
        sidebar = self.query_one("#agent-sidebar", AgentSidebar)
        sidebar.add_agent(agent.id, agent.name)
        sidebar.set_active(agent.id)

        # Populate ghost worktrees (feature branches only)
        self._populate_worktrees()

        # Initialize file index for fuzzy file search
        self.file_index = FileIndex(root=cwd)
        self._refresh_file_index()

        # Create client with resume if provided (avoids double client creation)
        resume = self._resume_on_start
        agent.client = ClaudeSDKClient(self._make_options(resume=resume))
        await agent.client.connect()
        if resume:
            self._load_and_display_history(resume)
            agent.session_id = resume
            self.notify(f"Resuming {resume[:8]}...")
        # Fetch SDK commands and update autocomplete
        await self._update_slash_commands()
        self.query_one("#input", ChatInput).focus()
        # Send initial prompt if provided
        if self._initial_prompt:
            self._send_initial_prompt()

    async def _update_slash_commands(self) -> None:
        """Fetch available commands from SDK and update autocomplete."""
        try:
            if not self.client:
                return
            info = await self.client.get_server_info()
            if not info:
                return
            sdk_commands = ["/" + cmd["name"] for cmd in info.get("commands", [])]
            all_commands = self.LOCAL_COMMANDS + sdk_commands
            autocomplete = self.query_one(TextAreaAutoComplete)
            autocomplete.slash_commands = all_commands
            # Update footer with model info - first model marked 'default' is active
            if "models" in info:
                models = info["models"]
                if isinstance(models, list) and models:
                    # Find active model (one marked default)
                    active = models[0]
                    for m in models:
                        if m.get("value") == "default":
                            active = m
                            break
                    # Extract short name from description like "Opus 4.5 · ..."
                    desc = active.get("description", "")
                    model_name = desc.split("·")[0].strip() if "·" in desc else active.get("displayName", "")
                    footer = self.query_one(StatusFooter)
                    footer.model = model_name
        except Exception as e:
            log.warning(f"Failed to fetch SDK commands: {e}")
        self.refresh_context()

    @work(exclusive=True, group="file_index")
    async def _refresh_file_index(self) -> None:
        """Refresh the file index in the background."""
        if self.file_index:
            await self.file_index.refresh()

    def _load_and_display_history(self, session_id: str, cwd: Path | None = None) -> None:
        """Load session history and display in chat view."""
        chat_view = self._chat_view
        if not chat_view:
            return
        chat_view.remove_children()
        for m in load_session_messages(session_id, limit=50, cwd=cwd):
            if m["type"] == "user":
                msg = ChatMessage(m["content"][:500])
                msg.add_class("user-message")
                chat_view.mount(msg)
            elif m["type"] == "assistant":
                msg = ChatMessage(m["content"][:1000])
                msg.add_class("assistant-message")
                chat_view.mount(msg)
            elif m["type"] == "tool_use":
                block = ToolUseBlock(id=m.get("id", ""), name=m["name"], input=m["input"])
                widget = ToolUseWidget(block, collapsed=True, completed=True)
                chat_view.mount(widget)
        self.call_after_refresh(_scroll_if_at_bottom, chat_view)

    def refresh_context(self) -> None:
        """Update context bar from session file (no API call)."""
        agent = self._agent
        if not agent or not agent.session_id:
            return
        tokens = get_context_from_session(agent.session_id, cwd=agent.cwd)
        if tokens is not None:
            self.query_one("#context-bar", ContextBar).tokens = tokens

    def _send_initial_prompt(self) -> None:
        """Send the initial prompt from CLI args."""
        prompt = self._initial_prompt
        self._initial_prompt = None  # Clear so it doesn't re-send
        if prompt:
            self._handle_prompt(prompt)

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        if not event.text.strip():
            return
        self.query_one("#input", ChatInput).clear()
        self._handle_prompt(event.text)

    def _handle_prompt(self, prompt: str) -> None:
        """Process a prompt - handles local commands or sends to Claude."""
        chat_view = self._chat_view
        if not chat_view:
            return

        if prompt.strip() == "/clear":
            chat_view.remove_children()
            self.notify("Conversation cleared")
            self.run_claude(prompt)
            return

        if prompt.strip().startswith("/resume"):
            parts = prompt.strip().split(maxsplit=1)
            if len(parts) > 1:
                self._load_and_display_history(parts[1])
                self.notify(f"Resuming {parts[1][:8]}...")
                self.resume_session(parts[1])
            else:
                self._show_session_picker()
            return

        if prompt.strip().startswith("/worktree"):
            handle_worktree_command(self, prompt.strip())
            return

        if prompt.strip().startswith("/agent"):
            self._handle_agent_command(prompt.strip())
            return

        if prompt.strip().startswith("/shell"):
            self._handle_shell_command(prompt.strip())
            return

        if prompt.strip() == "/theme":
            self.action_command_palette()
            return

        # Mount user message
        user_msg = ChatMessage(prompt)
        user_msg.add_class("user-message")
        chat_view.mount(user_msg)

        # Mount clickable attachment tags for images
        attachments_to_mount = list(self.pending_images)  # Copy before clearing
        screenshot_num = 0
        for path, name, _media, _data in attachments_to_mount:
            if name.lower().startswith("screenshot"):
                screenshot_num += 1
                display_name = f"Screenshot #{screenshot_num}"
            else:
                display_name = name
            chat_view.mount(ChatAttachment(path, display_name))

        self.call_after_refresh(_scroll_if_at_bottom, chat_view)

        self.current_response = None
        self._show_thinking()
        self.run_claude(prompt)

    @work(exit_on_error=False)
    async def run_claude(self, prompt: str) -> None:
        """Run a Claude query for the current agent (captured at call time)."""
        # Capture agent at start - messages go to this agent regardless of later switches
        agent = self._agent
        if not agent or not agent.client:
            log.warning(f"run_claude: no agent or client (agent={agent is not None})")
            self.notify("Agent not ready", severity="error")
            return
        agent_id = agent.id
        self._set_agent_status("busy", agent_id)
        try:
            # Send message with images if any are pending
            if self.pending_images:
                message = self._build_message_with_images(prompt)
                if agent.client._transport:
                    await agent.client._transport.write(json.dumps(message) + "\n")
            else:
                await agent.client.query(prompt)
            had_tool_use: dict[str | None, bool] = {}
            agent.response_had_tools = False
            awaiting_compact_summary = False

            async for message in agent.client.receive_response():
                if isinstance(message, AssistantMessage):
                    parent_id = message.parent_tool_use_id
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            new_msg = had_tool_use.get(parent_id, False)
                            self.post_message(
                                StreamChunk(block.text, new_message=new_msg, parent_tool_use_id=parent_id, agent_id=agent_id)
                            )
                            had_tool_use[parent_id] = False
                        elif isinstance(block, ToolUseBlock):
                            self.post_message(ToolUseMessage(block, parent_tool_use_id=parent_id, agent_id=agent_id))
                            had_tool_use[parent_id] = True
                            agent.response_had_tools = True
                        elif isinstance(block, ToolResultBlock):
                            self.post_message(ToolResultMessage(block, parent_tool_use_id=parent_id, agent_id=agent_id))
                elif isinstance(message, UserMessage):
                    content = getattr(message, "content", "")
                    # After compact_boundary, first UserMessage with string content is the summary
                    # (the second is <local-command-stdout>Compacted</local-command-stdout>)
                    if awaiting_compact_summary and isinstance(content, str) and not content.startswith("<"):
                        awaiting_compact_summary = False
                        self.post_message(StreamChunk(content, agent_id=agent_id))
                    elif isinstance(content, list):
                        # UserMessage contains tool results as a list of ToolResultBlock
                        for block in content:
                            if isinstance(block, ToolResultBlock):
                                self.post_message(ToolResultMessage(block, parent_tool_use_id=None, agent_id=agent_id))
                elif isinstance(message, SystemMessage):
                    subtype = getattr(message, "subtype", "")
                    if subtype == "compact_boundary":
                        meta = message.data.get("compact_metadata", {})
                        pre_tokens = meta.get("pre_tokens", "?")
                        self.notify(f"Compacted from {pre_tokens} tokens")
                        awaiting_compact_summary = True
                elif isinstance(message, ResultMessage):
                    self.post_message(ResponseComplete(message, agent_id=agent_id))
        except Exception as e:
            self.show_error("Claude response failed", e)
            self.post_message(ResponseComplete(None, agent_id=agent_id))

    def _show_thinking(self, agent_id: str | None = None) -> None:
        """Show the thinking indicator for a specific agent."""
        agent = self._get_agent(agent_id)
        if not agent or not agent.chat_view:
            return
        if agent.chat_view.query(ThinkingIndicator):
            return
        agent.chat_view.mount(ThinkingIndicator())
        self.call_after_refresh(_scroll_if_at_bottom, agent.chat_view)

    def _hide_thinking(self, agent_id: str | None = None) -> None:
        """Hide thinking indicator for a specific agent."""
        try:
            agent = self._get_agent(agent_id)
            if agent and agent.chat_view:
                for ind in agent.chat_view.query(ThinkingIndicator):
                    ind.remove()
        except Exception:
            pass  # OK to fail during shutdown

    def on_stream_chunk(self, event: StreamChunk) -> None:
        self._hide_thinking(event.agent_id)
        agent = self._get_agent(event.agent_id)
        if not agent:
            return

        if event.parent_tool_use_id and event.parent_tool_use_id in agent.active_tasks:
            task = agent.active_tasks[event.parent_tool_use_id]
            task.add_text(event.text, new_message=event.new_message)
            return

        chat_view = agent.chat_view
        if not chat_view:
            return
        if event.new_message or not agent.current_response:
            agent.current_response = ChatMessage("")
            agent.current_response.add_class("assistant-message")
            chat_view.mount(agent.current_response)
        agent.current_response.append_content(event.text)
        self.call_after_refresh(_scroll_if_at_bottom, chat_view)

    def on_tool_use_message(self, event: ToolUseMessage) -> None:
        self._hide_thinking()
        agent = self._get_agent(event.agent_id)
        if not agent:
            return

        if event.parent_tool_use_id and event.parent_tool_use_id in agent.active_tasks:
            task = agent.active_tasks[event.parent_tool_use_id]
            task.add_tool_use(event.block)
            return

        chat_view = agent.chat_view
        if not chat_view:
            return

        # TodoWrite gets special handling - update sidebar panel and/or inline widget
        if event.block.name == "TodoWrite":
            todos = event.block.input.get("todos", [])
            agent.todos = todos  # Store on agent for switching
            panel = self.query_one("#todo-panel", TodoPanel)
            panel.update_todos(todos)
            self._position_right_sidebar()
            # Also update inline widget if exists, or create if narrow
            existing = self.query(TodoWidget)
            if existing:
                existing[0].update_todos(todos)
            elif self.size.width < self.SIDEBAR_MIN_WIDTH:
                chat_view.mount(TodoWidget(todos))
            self.call_after_refresh(_scroll_if_at_bottom, chat_view)
            self._show_thinking(event.agent_id)
            return

        while len(agent.recent_tools) >= self.RECENT_TOOLS_EXPANDED:
            old = agent.recent_tools.pop(0)
            old.collapse()

        collapsed = event.block.name in self.COLLAPSE_BY_DEFAULT
        if event.block.name == "Task":
            widget = TaskWidget(event.block, collapsed=collapsed)
            agent.active_tasks[event.block.id] = widget
        else:
            widget = ToolUseWidget(event.block, collapsed=collapsed)

        agent.pending_tools[event.block.id] = widget
        agent.recent_tools.append(widget)
        chat_view.mount(widget)
        self.call_after_refresh(_scroll_if_at_bottom, chat_view)
        self._hide_thinking(event.agent_id)  # Tool widget has its own spinner

    def on_tool_result_message(self, event: ToolResultMessage) -> None:
        agent = self._get_agent(event.agent_id)
        if not agent:
            return

        if event.parent_tool_use_id and event.parent_tool_use_id in agent.active_tasks:
            task = agent.active_tasks[event.parent_tool_use_id]
            task.add_tool_result(event.block)
            return

        widget = agent.pending_tools.get(event.block.tool_use_id)
        if widget:
            widget.set_result(event.block)
            del agent.pending_tools[event.block.tool_use_id]
            if event.block.tool_use_id in agent.active_tasks:
                del agent.active_tasks[event.block.tool_use_id]
        self._show_thinking(event.agent_id)

    def on_resize(self, event) -> None:
        """Reposition right sidebar on resize."""
        self.call_after_refresh(self._position_right_sidebar)

    def _position_right_sidebar(self) -> None:
        """Show/hide right sidebar based on terminal width and content."""
        sidebar = self.query_one("#right-sidebar", Vertical)
        panel = self.query_one("#todo-panel", TodoPanel)
        agent_sidebar = self.query_one("#agent-sidebar", AgentSidebar)
        # Show sidebar when wide enough and we have multiple agents, worktrees, or todos
        has_content = len(self.agents) > 1 or agent_sidebar._worktrees or panel.todos
        if self.size.width >= self.SIDEBAR_MIN_WIDTH and has_content:
            sidebar.remove_class("hidden")
            # Show/hide todo panel based on whether it has content
            if panel.todos:
                panel.remove_class("hidden")
            else:
                panel.add_class("hidden")
        else:
            sidebar.add_class("hidden")

    def on_response_complete(self, event: ResponseComplete) -> None:
        self._hide_thinking()
        agent = self._get_agent(event.agent_id)
        self._set_agent_status("idle", event.agent_id)
        if event.result and agent:
            agent.session_id = event.result.session_id
            self.refresh_context()
        if agent:
            # Mark final message as summary if tools were used
            if agent.response_had_tools and agent.current_response:
                agent.current_response.add_class("summary")
            agent.current_response = None
        self.query_one("#input", ChatInput).focus()
        self.completions.put_nowait(event)

        # Continue worktree finish if this agent has a pending finish
        # This check is agent-scoped so switching agents won't trigger cleanup
        if agent and agent.finish_state:
            on_response_complete_finish(self, agent)

    @work(group="resume", exclusive=True, exit_on_error=False)
    async def resume_session(self, session_id: str) -> None:
        """Resume a session by creating a new client."""
        log.info(f"resume_session started: {session_id}")
        try:
            await self._replace_client(self._make_options(resume=session_id))
            self.session_id = session_id
            self.post_message(ResponseComplete(None))
            self.refresh_context()
            log.info(f"Resume complete for {session_id}")
        except Exception as e:
            self.show_error("Session resume failed", e)
            self.post_message(ResponseComplete(None))

    def action_clear(self) -> None:
        chat_view = self._chat_view
        if chat_view:
            chat_view.remove_children()

    def action_copy_selection(self) -> None:
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.notify("Copied to clipboard")

    def action_new_agent(self) -> None:
        """Create a new agent (prompts for name/path)."""
        self.notify("Use /agent <name> to create a new agent")

    def action_switch_agent(self, position: int) -> None:
        """Switch to agent by position (1-indexed)."""
        agent_ids = list(self.agents.keys())
        if 0 < position <= len(agent_ids):
            self._switch_to_agent(agent_ids[position - 1])

    def on_mouse_up(self, event: MouseUp) -> None:
        self.set_timer(0.05, self._check_and_copy_selection)

    def _check_and_copy_selection(self) -> None:
        selected = self.screen.get_selected_text()
        if selected and len(selected.strip()) > 0:
            self.copy_to_clipboard(selected)

    def action_quit(self) -> None:  # type: ignore[override]
        now = time.time()
        if hasattr(self, "_last_quit_time") and now - self._last_quit_time < 1.0:
            self.run_worker(self._cleanup_and_exit())
        else:
            self._last_quit_time = now
            self.notify("Press Ctrl+C again to quit")

    async def _cleanup_and_exit(self) -> None:
        """Disconnect all agents and exit."""
        for agent in self.agents.values():
            if agent.client:
                try:
                    await agent.client.interrupt()
                except Exception:
                    pass  # Best-effort cleanup during shutdown
                agent.client = None
        # Brief delay to let SDK hooks complete before stream closes
        await asyncio.sleep(0.1)
        # Suppress SDK stderr noise during exit (stream closed errors)
        sys.stderr = open(os.devnull, "w")
        self.exit()

    def _show_session_picker(self) -> None:
        picker = self.query_one("#session-picker", ListView)
        chat_view = self._chat_view
        picker.remove_class("hidden")
        if chat_view:
            chat_view.add_class("hidden")
        self._session_picker_active = True
        self._update_session_picker("")

    def _update_session_picker(self, search: str) -> None:
        picker = self.query_one("#session-picker", ListView)
        picker.clear()
        for session_id, preview, _, msg_count in get_recent_sessions(search=search):
            picker.append(SessionItem(session_id, preview, msg_count))

    def _hide_session_picker(self) -> None:
        self._session_picker_active = False
        self.query_one("#session-picker", ListView).add_class("hidden")
        chat_view = self._chat_view
        if chat_view:
            chat_view.remove_class("hidden")
        self.query_one("#input", ChatInput).clear()
        self.query_one("#input", ChatInput).focus()

    @work(group="reconnect", exclusive=True, exit_on_error=False)
    async def _reconnect_sdk(self, new_cwd: Path) -> None:
        """Reconnect SDK with a new working directory."""
        agent = self._agent
        if not agent:
            return
        try:
            # Check for existing session BEFORE creating client
            sessions = get_recent_sessions(limit=1, cwd=new_cwd)
            resume_id = sessions[0][0] if sessions else None

            await self._replace_client(self._make_options(cwd=new_cwd, resume=resume_id))

            # Clear internal state
            agent.current_response = None
            agent.pending_tools.clear()
            agent.active_tasks.clear()
            agent.recent_tools.clear()
            agent.cwd = new_cwd

            if resume_id:
                self._load_and_display_history(resume_id, cwd=new_cwd)
                agent.session_id = resume_id
                self.notify(f"Resumed session in {new_cwd.name}")
            else:
                # Clear chat view only if not resuming (resume does its own clear)
                if agent.chat_view:
                    try:
                        agent.chat_view.remove_children()
                    except Exception:
                        pass  # App may be exiting
                agent.session_id = None
                self.notify(f"SDK reconnected in {new_cwd.name}")
        except Exception as e:
            self.show_error("SDK reconnect failed", e)

    def action_escape(self) -> None:
        """Handle Escape: cancel picker, dismiss prompts, or interrupt agent."""
        # Session picker takes priority
        if self._session_picker_active:
            self._hide_session_picker()
            return

        # Cancel any active prompts
        for prompt in list(self.query(SelectionPrompt)) + list(self.query(QuestionPrompt)):
            prompt.cancel()
            return

        # Interrupt running agent - send interrupt to SDK
        if self.client:
            self.run_worker(self.client.interrupt(), exclusive=False)
            self._hide_thinking()
            self.notify("Interrupted")
            self.query_one("#input", ChatInput).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
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

    def on_agent_item_selected(self, event: AgentItem.Selected) -> None:
        """Handle agent selection from sidebar."""
        if event.agent_id == self.active_agent_id:
            return
        self._switch_to_agent(event.agent_id)

    def on_worktree_item_selected(self, event: WorktreeItem.Selected) -> None:
        """Handle ghost worktree selection - create an agent there."""
        self._create_new_agent(event.branch, event.path, worktree=event.branch, auto_resume=True)

    def _populate_worktrees(self) -> None:
        """Populate sidebar with ghost worktrees for feature branches."""
        try:
            worktrees = list_worktrees()
        except Exception:
            return  # Not a git repo or git not available
        sidebar = self.query_one("#agent-sidebar", AgentSidebar)
        # Get names of existing agents to skip
        agent_names = {a.name for a in self.agents.values()}
        for wt in worktrees:
            if wt.is_main:
                continue  # Skip main worktree
            if wt.branch in agent_names:
                continue  # Already have an agent
            sidebar.add_worktree(wt.branch, wt.path)

    def on_agent_item_close_requested(self, event: AgentItem.CloseRequested) -> None:
        """Handle close button click on agent item."""
        if len(self.agents) <= 1:
            self.notify("Cannot close the last agent", severity="error")
            return
        self._do_close_agent(event.agent_id)

    def _switch_to_agent(self, agent_id: str) -> None:
        """Switch to a different agent."""
        if agent_id not in self.agents:
            return
        old_agent = self._agent
        # Hide current agent's chat view and prompt
        if old_agent:
            if old_agent.chat_view:
                old_agent.chat_view.add_class("hidden")
            if old_agent.active_prompt:
                old_agent.active_prompt.add_class("hidden")
        # Switch active agent
        self.active_agent_id = agent_id
        agent = self._agent
        if agent and agent.chat_view:
            agent.chat_view.remove_class("hidden")
        # Show new agent's prompt if it has one, otherwise show input
        input_container = self.query_one("#input-container")
        if agent and agent.active_prompt:
            agent.active_prompt.remove_class("hidden")
            input_container.add_class("hidden")
        else:
            input_container.remove_class("hidden")
        # Update sidebar selection
        sidebar = self.query_one("#agent-sidebar", AgentSidebar)
        sidebar.set_active(agent_id)
        # Update footer branch for new agent's cwd
        footer = self.query_one(StatusFooter)
        footer.refresh_branch(str(agent.cwd) if agent else None)
        footer.auto_edit = agent.auto_approve_edits if agent else False
        # Update todo panel for new agent
        panel = self.query_one("#todo-panel", TodoPanel)
        panel.update_todos(agent.todos if agent else [])
        # Update context bar for new agent
        self.refresh_context()
        self._position_right_sidebar()
        self.query_one("#input", ChatInput).focus()

    def _handle_shell_command(self, command: str) -> None:
        """Handle /shell command - suspend TUI and run shell command."""
        parts = command.split(maxsplit=1)
        if len(parts) < 2:
            self.notify("Usage: /shell <command>")
            return
        cmd = parts[1]
        agent = self._agent
        cwd = str(agent.cwd) if agent else None
        with self.suspend():
            import subprocess, sys, tty, termios, time
            env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
            shell = os.environ.get("SHELL", "/bin/sh")
            start = time.monotonic()
            subprocess.run([shell, "-lc", cmd], cwd=cwd, env=env)
            # Only prompt if command completed quickly (likely non-interactive)
            if time.monotonic() - start < 1.0:
                print("\nPress any key to continue...", end="", flush=True)
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    sys.stdin.read(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                    print()  # newline after keypress

    def _handle_agent_command(self, command: str) -> None:
        """Handle /agent commands."""
        parts = command.split(maxsplit=2)
        if len(parts) == 1:
            # List agents
            for i, (aid, agent) in enumerate(self.agents.items(), 1):
                marker = "*" if aid == self.active_agent_id else " "
                self.notify(f"{marker}{i}. {agent.name} ({agent.status})")
            return

        subcommand = parts[1]
        if subcommand == "close":
            # Close agent by name or position
            target = parts[2] if len(parts) > 2 else None
            self._close_agent(target)
            return

        # Otherwise, create new agent
        name = subcommand
        path = Path(parts[2]) if len(parts) > 2 else Path.cwd()
        self._create_new_agent(name, path)

    @work(group="new_agent", exclusive=True, exit_on_error=False)
    async def _create_new_agent(self, name: str, cwd: Path, worktree: str | None = None, auto_resume: bool = False) -> None:
        """Create a new agent session."""
        agent = create_agent_session(name=name, cwd=cwd, worktree=worktree)

        chat_view = AutoHideScroll(id=f"chat-view-{agent.id}", classes="chat-view hidden")
        main = self.query_one("#main", Horizontal)
        main.mount(chat_view, after=self.query_one("#session-picker"))
        agent.chat_view = chat_view

        try:
            resume_id = None
            if auto_resume:
                sessions = get_recent_sessions(limit=1, cwd=cwd)
                resume_id = sessions[0][0] if sessions else None
            agent.client = ClaudeSDKClient(self._make_options(cwd=cwd, resume=resume_id))
            await agent.client.connect()
        except Exception as e:
            self.show_error(f"Failed to create agent '{name}'", e)
            chat_view.remove()
            return

        self.agents[agent.id] = agent
        sidebar = self.query_one("#agent-sidebar", AgentSidebar)
        sidebar.add_agent(agent.id, agent.name)
        self._switch_to_agent(agent.id)
        self._position_right_sidebar()

        if resume_id:
            self._load_and_display_history(resume_id, cwd=cwd)
            agent.session_id = resume_id
            self.notify(f"Resumed session in '{name}'")
        else:
            label = f"Worktree '{name}'" if worktree else f"Agent '{name}'"
            self.notify(f"{label} ready")

    def _close_agent(self, target: str | None) -> None:
        """Close an agent by name, position, or current if no target."""
        if len(self.agents) <= 1:
            self.notify("Cannot close the last agent", severity="error")
            return

        # Find agent to close
        agent_to_close: AgentSession | None = None
        if target is None:
            # Close current agent
            agent_to_close = self._agent
        elif target.isdigit():
            # Close by position (1-indexed)
            pos = int(target) - 1
            agent_ids = list(self.agents.keys())
            if 0 <= pos < len(agent_ids):
                agent_to_close = self.agents[agent_ids[pos]]
        else:
            # Close by name
            for agent in self.agents.values():
                if agent.name == target:
                    agent_to_close = agent
                    break

        if not agent_to_close:
            self.notify(f"Agent not found: {target}", severity="error")
            return

        self._do_close_agent(agent_to_close.id)

    @work(group="close_agent", exclusive=True, exit_on_error=False)
    async def _do_close_agent(self, agent_id: str) -> None:
        """Actually close an agent (async for client cleanup)."""
        agent = self.agents.get(agent_id)
        if not agent:
            return

        name = agent.name
        was_active = agent_id == self.active_agent_id

        # Disconnect client
        if agent.client:
            try:
                await agent.client.interrupt()
            except Exception:
                pass  # Best-effort cleanup
            agent.client = None

        # Remove chat view
        if agent.chat_view:
            agent.chat_view.remove()

        # Remove from sidebar
        sidebar = self.query_one("#agent-sidebar", AgentSidebar)
        sidebar.remove_agent(agent_id)

        # Remove from agents dict
        del self.agents[agent_id]

        # Switch to another agent if we closed the active one
        if was_active and self.agents:
            self._switch_to_agent(next(iter(self.agents)))

        self._position_right_sidebar()
        self.notify(f"Agent '{name}' closed")

    def on_app_focus(self) -> None:
        input_widgets = self.query("#input")
        if input_widgets:
            input_widgets.first(ChatInput).focus()

    def on_paste(self, event) -> None:
        """App-level paste handler - catches pastes when input isn't focused."""
        # Skip if already handled by ChatInput (check if input is focused)
        input_widgets = self.query("#input")
        if input_widgets and self.focused == input_widgets.first(ChatInput):
            return  # Let ChatInput handle it

        # Use ChatInput's image detection logic
        input_widget = input_widgets.first(ChatInput) if input_widgets else None
        if input_widget:
            images = input_widget._is_image_path(event.text)
            if images:
                # Use ChatInput's dedup tracking
                now = time.time()
                last = input_widget._last_image_paste
                if last and last[0] == event.text and now - last[1] < 0.5:
                    event.prevent_default()
                    event.stop()
                    return
                input_widget._last_image_paste = (event.text, now)

                for path in images:
                    self._attach_image(path)
                event.prevent_default()
                event.stop()

    def on_key(self, event) -> None:
        if self.query(SelectionPrompt) or self.query(QuestionPrompt):
            return
        input_widgets = self.query("#input")
        if not input_widgets:
            return
        input_widget = input_widgets.first(ChatInput)
        if self.focused == input_widget:
            return
        if len(event.character or "") == 1 and event.character.isprintable():
            input_widget.focus()
            input_widget.insert(event.character)
            event.prevent_default()
            event.stop()
