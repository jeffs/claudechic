"""Claude Code Textual UI - Main application."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
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
    ToolUseBlock,
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
from claude_alamode.sessions import (
    get_context_from_session,
    get_recent_sessions,
    load_session_messages,
)
from claude_alamode.features.worktree import (
    handle_worktree_command,
    list_worktrees,
)
from claude_alamode.features.worktree.commands import on_response_complete_finish
from claude_alamode.permissions import PermissionRequest
from claude_alamode.agent import Agent, ToolUse
from claude_alamode.agent_manager import AgentManager
from claude_alamode.mcp import set_app, create_alamode_server
from claude_alamode.file_index import FileIndex
from claude_alamode.history import append_to_history
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
    AgentToolWidget,
    TodoWidget,
    TodoPanel,
    SelectionPrompt,
    QuestionPrompt,
    SessionItem,
    TextAreaAutoComplete,
    HistorySearch,
    AgentSidebar,
    AgentItem,
    WorktreeItem,
    AutoHideScroll,
)
from claude_alamode.widgets.footer import StatusFooter
from claude_alamode.errors import setup_logging  # noqa: F401 - used at startup

log = logging.getLogger(__name__)


def _scroll_if_at_bottom(scroll_view: VerticalScroll) -> None:
    """Scroll to end only if user hasn't scrolled up."""
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
        Binding("ctrl+r", "history_search", "History", priority=True, show=False),
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
        # AgentManager is the single source of truth for agents
        self.agent_mgr: AgentManager | None = None

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
        # Cached widget references (initialized lazily)
        self._agent_sidebar: AgentSidebar | None = None
        self._todo_panel: TodoPanel | None = None
        self._context_bar: ContextBar | None = None
        self._right_sidebar: Vertical | None = None
        self._input_container: Vertical | None = None
        self._chat_input: ChatInput | None = None
        self._status_footer: StatusFooter | None = None

    # Properties to access active agent's state
    @property
    def _agent(self) -> Agent | None:
        """Get the active agent."""
        return self.agent_mgr.active if self.agent_mgr else None

    @property
    def agents(self) -> dict[str, Agent]:
        """Get all agents dict (from AgentManager)."""
        return self.agent_mgr.agents if self.agent_mgr else {}

    @property
    def active_agent_id(self) -> str | None:
        """Get active agent ID (from AgentManager)."""
        return self.agent_mgr.active_id if self.agent_mgr else None

    @active_agent_id.setter
    def active_agent_id(self, value: str | None) -> None:
        """Set active agent ID (syncs to AgentManager)."""
        if self.agent_mgr:
            self.agent_mgr.active_id = value

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
    def pending_tools(self) -> dict[str, ToolUseWidget | TaskWidget | AgentToolWidget]:
        return self._agent.pending_tool_widgets if self._agent else {}

    @property
    def active_tasks(self) -> dict[str, TaskWidget]:
        return self._agent.active_task_widgets if self._agent else {}

    @property
    def recent_tools(self) -> list[ToolUseWidget | TaskWidget | AgentToolWidget]:
        return self._agent.recent_tools if self._agent else []

    @property
    def _chat_view(self) -> VerticalScroll | None:
        """Get the active agent's chat view."""
        return self._agent.chat_view if self._agent else None

    def _get_agent(self, agent_id: str | None) -> Agent | None:
        """Get agent by ID, or active agent if None."""
        if self.agent_mgr is None:
            return None
        return self.agent_mgr.get(agent_id)

    # Cached widget accessors (lazy init on first access)
    @property
    def agent_sidebar(self) -> AgentSidebar:
        if self._agent_sidebar is None:
            self._agent_sidebar = self.query_one("#agent-sidebar", AgentSidebar)
        return self._agent_sidebar

    @property
    def todo_panel(self) -> TodoPanel:
        if self._todo_panel is None:
            self._todo_panel = self.query_one("#todo-panel", TodoPanel)
        return self._todo_panel

    @property
    def context_bar(self) -> ContextBar:
        if self._context_bar is None:
            self._context_bar = self.query_one("#context-bar", ContextBar)
        return self._context_bar

    @property
    def right_sidebar(self) -> Vertical:
        if self._right_sidebar is None:
            self._right_sidebar = self.query_one("#right-sidebar", Vertical)
        return self._right_sidebar

    @property
    def input_container(self) -> Vertical:
        if self._input_container is None:
            self._input_container = self.query_one("#input-container", Vertical)
        return self._input_container

    @property
    def chat_input(self) -> ChatInput:
        if self._chat_input is None:
            self._chat_input = self.query_one("#input", ChatInput)
        return self._chat_input

    @property
    def status_footer(self) -> StatusFooter:
        if self._status_footer is None:
            self._status_footer = self.query_one(StatusFooter)
        return self._status_footer

    def _set_agent_status(self, status: Literal["idle", "busy", "needs_input"], agent_id: str | None = None) -> None:
        """Update an agent's status and sidebar display."""
        agent = self._get_agent(agent_id)
        if not agent:
            return
        agent.status = status
        try:
            self.agent_sidebar.update_status(agent.id, status)
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
    async def _show_prompt(self, prompt, agent: Agent | None = None):
        """Show a prompt widget, hiding input container. Restores on exit.

        If agent is provided, the prompt is associated with that agent and only
        shown when that agent is active. If agent is None, uses the currently
        active agent.
        """
        if agent is None:
            agent = self._agent
        if agent:
            agent.active_prompt = prompt

        # Mount prompt; only show if it belongs to the currently active agent
        is_active = agent is None or agent.id == self.active_agent_id
        self.query_one("#input-wrapper").mount(prompt)
        if is_active:
            self.input_container.add_class("hidden")
        else:
            prompt.add_class("hidden")
        try:
            yield prompt
        finally:
            if agent:
                agent.active_prompt = None
            try:
                prompt.remove()
            except Exception:
                pass  # Prompt may already be removed
            # Restore input if this agent is now active (user may have switched)
            if agent is None or agent.id == self.active_agent_id:
                self.input_container.remove_class("hidden")

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

        # Alamode MCP tools are always allowed (they're our own tools)
        if tool_name.startswith("mcp__alamode__"):
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
            self.status_footer.auto_edit = self._agent.auto_approve_edits if self._agent else False
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
                yield HistorySearch(id="history-search")
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
            mcp_servers={"alamode": create_alamode_server()},
            include_partial_messages=True,
        )

    async def on_mount(self) -> None:
        # Register app for MCP tools
        set_app(self)

        # Register and activate custom theme
        self.register_theme(ALAMODE_THEME)
        self.theme = "alamode"

        # Initialize AgentManager (new architecture)
        self.agent_mgr = AgentManager(self._make_options)
        self._wire_agent_manager_callbacks()

        # Create initial agent synchronously (UI populated immediately)
        cwd = Path.cwd()
        self.agent_mgr.create_unconnected(name=cwd.name, cwd=cwd)

        # Populate ghost worktrees (feature branches only)
        self._populate_worktrees()

        # Initialize file index for fuzzy file search
        self.file_index = FileIndex(root=cwd)
        self._refresh_file_index()

        # Focus input immediately - UI is ready
        self.chat_input.focus()

        # Connect SDK in background - UI renders while this happens
        self._connect_initial_client()

    @work(exclusive=True, group="connect")
    async def _connect_initial_client(self) -> None:
        """Connect SDK for the initial agent."""
        if self.agent_mgr is None or self.agent_mgr.active is None:
            return

        agent = self.agent_mgr.active

        # Show connecting status
        self.status_footer.model = "connecting..."

        # Resolve resume ID (handle __most_recent__ sentinel from CLI)
        resume = self._resume_on_start
        if resume == "__most_recent__":
            sessions = await get_recent_sessions(limit=1)
            resume = sessions[0][0] if sessions else None

        # Connect the agent to SDK
        options = self._make_options(cwd=agent.cwd, resume=resume)
        await agent.connect(options, resume=resume)

        # Load history if resuming
        if resume:
            await self._load_and_display_history(resume)
            self.notify(f"Resuming {resume[:8]}...")

        # Fetch SDK commands and update autocomplete
        await self._update_slash_commands()

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
                    self.status_footer.model = model_name
        except Exception as e:
            log.warning(f"Failed to fetch SDK commands: {e}")
        self.refresh_context()

    @work(exclusive=True, group="file_index")
    async def _refresh_file_index(self) -> None:
        """Refresh the file index in the background."""
        if self.file_index:
            await self.file_index.refresh()

    async def _load_and_display_history(self, session_id: str, cwd: Path | None = None) -> None:
        """Load session history and display in chat view."""
        chat_view = self._chat_view
        if not chat_view:
            return
        chat_view.remove_children()
        messages = await load_session_messages(session_id, limit=50, cwd=cwd)
        for m in messages:
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

    @work(group="refresh_context", exclusive=True)
    async def refresh_context(self) -> None:
        """Update context bar from session file (no API call)."""
        agent = self._agent
        if not agent or not agent.session_id:
            return
        tokens = await get_context_from_session(agent.session_id, cwd=agent.cwd)
        if tokens is not None:
            self.context_bar.tokens = tokens

    def _send_initial_prompt(self) -> None:
        """Send the initial prompt from CLI args."""
        prompt = self._initial_prompt
        self._initial_prompt = None  # Clear so it doesn't re-send
        if prompt:
            self._handle_prompt(prompt)

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        if not event.text.strip():
            return
        self.chat_input.clear()
        self._handle_prompt(event.text)

    def _handle_prompt(self, prompt: str) -> None:
        """Process a prompt - handles local commands or sends to Claude."""
        chat_view = self._chat_view
        if not chat_view:
            return

        # Append to global history
        agent = self._agent
        if agent:
            append_to_history(prompt, agent.cwd, agent.session_id or agent.id)

        if prompt.strip() == "/clear":
            chat_view.remove_children()
            self.notify("Conversation cleared")
            self._send_to_active_agent(prompt)
            return

        if prompt.strip().startswith("/resume"):
            parts = prompt.strip().split(maxsplit=1)
            if len(parts) > 1:
                self.run_worker(self._load_and_display_history(parts[1]))
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
            self.search_themes()
            return

        if prompt.strip() == "/exit":
            self.exit()
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
        self._send_to_active_agent(prompt)

    def _send_to_active_agent(self, prompt: str) -> None:
        """Send prompt to active agent using Agent.send()."""
        if self.agent_mgr is None or self.agent_mgr.active is None:
            log.warning("_send_to_active_agent: no agent manager or active agent")
            self.notify("Agent not ready", severity="error")
            return

        agent = self.agent_mgr.active

        # Transfer pending images to agent
        if self.pending_images:
            agent.pending_images = list(self.pending_images)
            self.pending_images.clear()
            self.query_one("#image-attachments", ImageAttachments).clear()

        # Start async send (returns immediately, callbacks handle UI)
        asyncio.create_task(agent.send(prompt), name=f"send-{agent.id}")

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

        if event.parent_tool_use_id and event.parent_tool_use_id in agent.active_task_widgets:
            task = agent.active_task_widgets[event.parent_tool_use_id]
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

        if event.parent_tool_use_id and event.parent_tool_use_id in agent.active_task_widgets:
            task = agent.active_task_widgets[event.parent_tool_use_id]
            task.add_tool_use(event.block)
            return

        chat_view = agent.chat_view
        if not chat_view:
            return

        # TodoWrite gets special handling - update sidebar panel and/or inline widget
        if event.block.name == "TodoWrite":
            todos = event.block.input.get("todos", [])
            agent.todos = todos  # Store on agent for switching
            self.todo_panel.update_todos(todos)
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
            agent.active_task_widgets[event.block.id] = widget
        elif event.block.name.startswith("mcp__alamode__"):
            # Custom widget for alamode MCP tools
            widget = AgentToolWidget(event.block)
        else:
            widget = ToolUseWidget(event.block, collapsed=collapsed)

        agent.pending_tool_widgets[event.block.id] = widget
        agent.recent_tools.append(widget)
        chat_view.mount(widget)
        self.call_after_refresh(_scroll_if_at_bottom, chat_view)
        self._hide_thinking(event.agent_id)  # Tool widget has its own spinner

    def on_tool_result_message(self, event: ToolResultMessage) -> None:
        agent = self._get_agent(event.agent_id)
        if not agent:
            return

        if event.parent_tool_use_id and event.parent_tool_use_id in agent.active_task_widgets:
            task = agent.active_task_widgets[event.parent_tool_use_id]
            task.add_tool_result(event.block)
            return

        widget = agent.pending_tool_widgets.get(event.block.tool_use_id)
        if widget:
            widget.set_result(event.block)
            del agent.pending_tool_widgets[event.block.tool_use_id]
            if event.block.tool_use_id in agent.active_task_widgets:
                del agent.active_task_widgets[event.block.tool_use_id]
        self._show_thinking(event.agent_id)

    def on_resize(self, event) -> None:
        """Reposition right sidebar on resize."""
        self.call_after_refresh(self._position_right_sidebar)

    def _position_right_sidebar(self) -> None:
        """Show/hide right sidebar based on terminal width and content."""
        # Show sidebar when wide enough and we have multiple agents, worktrees, or todos
        agent_count = len(self.agent_mgr) if self.agent_mgr else 0
        has_content = agent_count > 1 or self.agent_sidebar._worktrees or self.todo_panel.todos
        if self.size.width >= self.SIDEBAR_MIN_WIDTH and has_content:
            self.right_sidebar.remove_class("hidden")
            # Show/hide todo panel based on whether it has content
            if self.todo_panel.todos:
                self.todo_panel.remove_class("hidden")
            else:
                self.todo_panel.add_class("hidden")
        else:
            self.right_sidebar.add_class("hidden")

    def on_response_complete(self, event: ResponseComplete) -> None:
        self._hide_thinking()
        agent = self._get_agent(event.agent_id)
        self._set_agent_status("idle", event.agent_id)
        if event.result and agent:
            agent.session_id = event.result.session_id
            # Store response text and signal completion for MCP ask_agent
            agent._last_response = event.result.result or ""
            agent._completion_event.set()
            self.refresh_context()
        if agent:
            # Flush any pending debounced content
            if agent.current_response:
                agent.current_response.flush()
            # Mark final message as summary if tools were used
            if agent.response_had_tools and agent.current_response:
                agent.current_response.add_class("summary")
            agent.current_response = None
        self.chat_input.focus()
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

    def action_history_search(self) -> None:
        """Open reverse history search, or cycle if already open."""
        hs = self.query_one("#history-search", HistorySearch)
        if hs.display:
            hs.action_next_match()
        else:
            hs.show()

    def on_history_search_selected(self, event: HistorySearch.Selected) -> None:
        """Handle history selection - populate input."""
        self.chat_input.text = event.text
        self.chat_input.move_cursor(self.chat_input.document.end)
        self.chat_input.focus()

    def on_history_search_cancelled(self, event: HistorySearch.Cancelled) -> None:
        """Handle history search cancellation."""
        self.chat_input.focus()

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

    @work(group="session_picker", exclusive=True)
    async def _update_session_picker(self, search: str) -> None:
        picker = self.query_one("#session-picker", ListView)
        picker.clear()
        sessions = await get_recent_sessions(search=search)
        for session_id, preview, _, msg_count in sessions:
            picker.append(SessionItem(session_id, preview, msg_count))

    def _hide_session_picker(self) -> None:
        self._session_picker_active = False
        self.query_one("#session-picker", ListView).add_class("hidden")
        chat_view = self._chat_view
        if chat_view:
            chat_view.remove_class("hidden")
        self.chat_input.clear()
        self.chat_input.focus()

    @work(group="reconnect", exclusive=True, exit_on_error=False)
    async def _reconnect_sdk(self, new_cwd: Path) -> None:
        """Reconnect SDK with a new working directory."""
        agent = self._agent
        if not agent:
            return
        try:
            # Check for existing session BEFORE creating client
            sessions = await get_recent_sessions(limit=1, cwd=new_cwd)
            resume_id = sessions[0][0] if sessions else None

            await self._replace_client(self._make_options(cwd=new_cwd, resume=resume_id))

            # Clear internal state
            agent.current_response = None
            agent.pending_tool_widgets.clear()
            agent.active_task_widgets.clear()
            agent.recent_tools.clear()
            agent.cwd = new_cwd

            if resume_id:
                await self._load_and_display_history(resume_id, cwd=new_cwd)
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
            self.chat_input.focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self._session_picker_active and event.text_area.id == "input":
            self._update_session_picker(event.text_area.text)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            session_id = event.item.session_id
            log.info(f"Resuming session: {session_id}")
            self._hide_session_picker()
            self.run_worker(self._load_and_display_history(session_id))
            self.notify(f"Resuming {session_id[:8]}...")
            self.resume_session(session_id)

    def on_agent_item_selected(self, event: AgentItem.Selected) -> None:
        """Handle agent selection from sidebar."""
        if event.agent_id == self.active_agent_id:
            return
        self._switch_to_agent(event.agent_id)

    def on_agent_tool_widget_go_to_agent(self, event: AgentToolWidget.GoToAgent) -> None:
        """Handle 'Go to agent' button click from AgentToolWidget."""
        for agent_id, agent in self.agents.items():
            if agent.name == event.agent_name:
                self._switch_to_agent(agent_id)
                return
        self.notify(f"Agent '{event.agent_name}' not found", severity="warning")

    def on_worktree_item_selected(self, event: WorktreeItem.Selected) -> None:
        """Handle ghost worktree selection - create an agent there."""
        self._create_new_agent(event.branch, event.path, worktree=event.branch, auto_resume=True)

    def _populate_worktrees(self) -> None:
        """Populate sidebar with ghost worktrees for feature branches."""
        try:
            worktrees = list_worktrees()
        except Exception:
            return  # Not a git repo or git not available
        # Get names of existing agents to skip
        agent_names = {a.name for a in self.agents.values()}
        for wt in worktrees:
            if wt.is_main:
                continue  # Skip main worktree
            if wt.branch in agent_names:
                continue  # Already have an agent
            self.agent_sidebar.add_worktree(wt.branch, wt.path)

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
        # Switch active agent (setter syncs to AgentManager)
        self.active_agent_id = agent_id
        agent = self._agent
        if agent and agent.chat_view:
            agent.chat_view.remove_class("hidden")
        # Show new agent's prompt if it has one, otherwise show input
        if agent and agent.active_prompt:
            agent.active_prompt.remove_class("hidden")
            self.input_container.add_class("hidden")
        else:
            self.input_container.remove_class("hidden")
        # Update sidebar selection
        self.agent_sidebar.set_active(agent_id)
        # Update footer branch for new agent's cwd (async, non-blocking)
        asyncio.create_task(self.status_footer.refresh_branch(str(agent.cwd) if agent else None))
        self.status_footer.auto_edit = agent.auto_approve_edits if agent else False
        # Update todo panel for new agent
        self.todo_panel.update_todos(agent.todos if agent else [])
        # Update context bar for new agent
        self.refresh_context()
        self._position_right_sidebar()
        self.chat_input.focus()

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
    async def _create_new_agent(
        self, name: str, cwd: Path, worktree: str | None = None, auto_resume: bool = False, switch_to: bool = True
    ) -> None:
        """Create a new agent via AgentManager.

        Args:
            name: Display name for the agent
            cwd: Working directory
            worktree: Git worktree branch name if applicable
            auto_resume: Try to resume most recent session in cwd
            switch_to: Whether to switch to the new agent (default True)
        """
        if self.agent_mgr is None:
            self.notify("Agent manager not initialized", severity="error")
            return

        try:
            # Resolve resume ID if auto_resume
            resume_id = None
            if auto_resume:
                sessions = await get_recent_sessions(limit=1, cwd=cwd)
                resume_id = sessions[0][0] if sessions else None

            # Create agent via AgentManager (handles SDK connection, UI callbacks)
            agent = await self.agent_mgr.create(
                name=name, cwd=cwd, worktree=worktree, resume=resume_id, switch_to=switch_to
            )
        except Exception as e:
            self.show_error(f"Failed to create agent '{name}'", e)
            return

        self._position_right_sidebar()

        if resume_id:
            await self._load_and_display_history(resume_id, cwd=cwd)
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
        agent_to_close: Agent | None = None
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
        if self.agent_mgr is None:
            return
        agent = self.agents.get(agent_id)
        if not agent:
            return

        agent_name = agent.name
        was_active = agent_id == self.active_agent_id

        # Remove chat view before closing (AgentManager.close removes from agents dict)
        if agent.chat_view:
            agent.chat_view.remove()

        # Close via AgentManager (handles disconnect and removes from agents dict)
        await self.agent_mgr.close(agent_id)

        # Remove from sidebar
        self.agent_sidebar.remove_agent(agent_id)

        # Switch to another agent if we closed the active one
        if was_active and self.agents:
            self._switch_to_agent(next(iter(self.agents)))

        self._position_right_sidebar()
        self.notify(f"Agent '{agent_name}' closed")

    def on_app_focus(self) -> None:
        if self._chat_input:
            self._chat_input.focus()

    def on_paste(self, event) -> None:
        """App-level paste handler - catches pastes when input isn't focused."""
        if not self._chat_input:
            return
        # Skip if already handled by ChatInput (check if input is focused)
        if self.focused == self._chat_input:
            return  # Let ChatInput handle it

        # Use ChatInput's image detection logic
        images = self._chat_input._is_image_path(event.text)
        if images:
            # Use ChatInput's dedup tracking
            now = time.time()
            last = self._chat_input._last_image_paste
            if last and last[0] == event.text and now - last[1] < 0.5:
                event.prevent_default()
                event.stop()
                return
            self._chat_input._last_image_paste = (event.text, now)

            for path in images:
                self._attach_image(path)
            event.prevent_default()
            event.stop()

    def on_key(self, event) -> None:
        if self.query(SelectionPrompt) or self.query(QuestionPrompt):
            return
        if not self._chat_input or self.focused == self._chat_input:
            return
        if len(event.character or "") == 1 and event.character.isprintable():
            self._chat_input.focus()
            self._chat_input.insert(event.character)
            event.prevent_default()
            event.stop()

    # -----------------------------------------------------------------------
    # AgentManager callbacks (new architecture)
    # -----------------------------------------------------------------------

    def _wire_agent_manager_callbacks(self) -> None:
        """Wire AgentManager callbacks for UI integration.

        This sets up the callbacks that translate Agent events into UI updates.
        Called once during on_mount().
        """
        if self.agent_mgr is None:
            return

        # Manager-level callbacks
        self.agent_mgr.on_created = self._on_new_agent_created
        self.agent_mgr.on_switched = self._on_agent_switched
        self.agent_mgr.on_closed = self._on_agent_closed

        # Agent-level callbacks (applied to all agents via AgentManager)
        self.agent_mgr.on_agent_status_changed = self._on_agent_status_changed
        self.agent_mgr.on_agent_error = self._on_agent_error
        self.agent_mgr.on_agent_complete = self._on_agent_complete
        self.agent_mgr.on_agent_todos_updated = self._on_agent_todos_updated

        # Fine-grained streaming callbacks (post Textual Messages for UI handlers)
        self.agent_mgr.on_agent_text_chunk = self._on_agent_text_chunk
        self.agent_mgr.on_agent_tool_use = self._on_agent_tool_use
        self.agent_mgr.on_agent_tool_result = self._on_agent_tool_result

        # Permission UI callback
        self.agent_mgr.permission_ui_callback = self._handle_agent_permission_ui

    def _on_new_agent_created(self, agent: Agent) -> None:
        """Handle new agent creation from AgentManager."""
        log.info(f"New agent created: {agent.name} (id={agent.id})")

        try:
            # Create chat view for the agent
            is_first_agent = len(self.agent_mgr.agents) == 1 if self.agent_mgr else True
            if is_first_agent:
                # First agent uses the existing chat view from compose()
                chat_view = self.query_one("#chat-view", AutoHideScroll)
                chat_view.add_class("chat-view")  # Add class for consistent query behavior
            else:
                # Additional agents get new chat views
                chat_view = AutoHideScroll(id=f"chat-view-{agent.id}", classes="chat-view hidden")
                main = self.query_one("#main", Horizontal)
                main.mount(chat_view, after=self.query_one("#session-picker"))

            # Store chat view on agent
            agent.chat_view = chat_view

            # Add to sidebar
            try:
                self.agent_sidebar.add_agent(agent.id, agent.name)
            except Exception:
                pass  # Sidebar may not be mounted
        except Exception as e:
            log.exception(f"Failed to create agent UI: {e}")

    def _on_agent_switched(self, new_agent: Agent, old_agent: Agent | None) -> None:
        """Handle agent switch from AgentManager."""
        log.info(f"Switched to agent: {new_agent.name}")

        # Hide old agent's chat view
        if old_agent and old_agent.chat_view:
            old_agent.chat_view.add_class("hidden")

        # Show new agent's chat view
        if new_agent.chat_view:
            new_agent.chat_view.remove_class("hidden")

        # Update sidebar
        try:
            self.agent_sidebar.set_active(new_agent.id)
        except Exception:
            pass

        # Update footer
        self._update_footer_auto_edit()
        self._update_footer_cwd(new_agent.cwd)

        # Update todo panel
        self.todo_panel.update_todos(new_agent.todos)
        self.refresh_context()
        self._position_right_sidebar()

    def _on_agent_closed(self, agent_id: str) -> None:
        """Handle agent closure from AgentManager."""
        log.info(f"Agent closed: {agent_id}")
        try:
            self.agent_sidebar.remove_agent(agent_id)
        except Exception:
            pass

    def _on_agent_status_changed(self, agent: Agent) -> None:
        """Handle agent status change."""
        try:
            self.agent_sidebar.update_status(agent.id, agent.status)
        except Exception:
            pass

    def _on_agent_error(self, agent: Agent, message: str, exception: Exception | None) -> None:
        """Handle error from agent."""
        # Show error in UI if this is active agent
        if self.agent_mgr and agent.id == self.agent_mgr.active_id:
            self.show_error(message, exception)

    def _on_agent_complete(self, agent: Agent, result: ResultMessage | None) -> None:
        """Handle agent response completion."""
        log.info(f"Agent {agent.name} completed response")
        # Post ResponseComplete message for existing UI handler
        self.post_message(ResponseComplete(result, agent_id=agent.id))

    def _on_agent_todos_updated(self, agent: Agent) -> None:
        """Handle agent todos update."""
        if self.agent_mgr and agent.id == self.agent_mgr.active_id:
            try:
                self.todo_panel.update_todos(agent.todos)
            except Exception:
                pass

    def _on_agent_text_chunk(
        self, agent: Agent, text: str, new_message: bool, parent_tool_id: str | None
    ) -> None:
        """Handle text chunk from agent - post Textual Message for UI."""
        self.post_message(
            StreamChunk(text, new_message=new_message, parent_tool_use_id=parent_tool_id, agent_id=agent.id)
        )

    def _on_agent_tool_use(self, agent: Agent, tool: ToolUse) -> None:
        """Handle tool use from agent - post Textual Message for UI."""
        from claude_agent_sdk import ToolUseBlock
        block = ToolUseBlock(id=tool.id, name=tool.name, input=tool.input)
        self.post_message(ToolUseMessage(block, parent_tool_use_id=None, agent_id=agent.id))

    def _on_agent_tool_result(self, agent: Agent, tool: ToolUse) -> None:
        """Handle tool result from agent - post Textual Message for UI."""
        from claude_agent_sdk import ToolResultBlock
        block = ToolResultBlock(tool_use_id=tool.id, content=tool.result or "", is_error=tool.is_error)
        self.post_message(ToolResultMessage(block, parent_tool_use_id=None, agent_id=agent.id))

    async def _handle_agent_permission_ui(
        self, agent: Agent, request: PermissionRequest
    ) -> str:
        """Handle permission UI for an agent.

        This is called by Agent when it needs user input for a permission.
        Returns "allow", "deny", or "allow_all".
        """
        # Put in interactions queue for testing
        await self.interactions.put(request)

        if request.tool_name == "AskUserQuestion":
            # Handle question prompts
            questions = request.tool_input.get("questions", [])
            async with self._show_prompt(QuestionPrompt(questions), agent) as prompt:
                answers = await prompt.wait()

            if not answers:
                return "deny"

            # Store answers on request for Agent to retrieve
            request._answers = answers  # type: ignore[attr-defined]
            return "allow"

        # Regular permission prompt
        options = [("allow", "Yes, this time only"), ("deny", "No")]
        if request.tool_name in self.AUTO_EDIT_TOOLS:
            options.insert(0, ("allow_all", "Yes, all edits in this session"))

        async with self._show_prompt(SelectionPrompt(request.title, options), agent) as prompt:
            async def ui_response():
                result = await prompt.wait()
                if not request._event.is_set():
                    request.respond(result)

            asyncio.create_task(ui_response())
            result = await request.wait()

        if result == "allow_all":
            self.notify("Auto-edit enabled (Shift+Tab to disable)")

        return result

    def _update_footer_cwd(self, cwd: Path) -> None:
        """Update footer to show cwd/branch info."""
        try:
            # refresh_branch is async, schedule it
            asyncio.create_task(self.status_footer.refresh_branch(str(cwd)))
        except Exception:
            pass
