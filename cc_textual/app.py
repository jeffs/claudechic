"""Claude Code Textual UI - Main application."""

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll, Horizontal, Center
from textual.events import MouseUp
from textual.reactive import reactive
from textual.widgets import Footer, ListView, TextArea
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
    HookMatcher,
)

from cc_textual.messages import (
    StreamChunk,
    ResponseComplete,
    ToolUseMessage,
    ToolResultMessage,
    ContextUpdate,
)
from cc_textual.sessions import get_recent_sessions, load_session_messages
from cc_textual.worktree import (
    start_worktree, get_finish_worktree_info, list_worktrees,
    cleanup_worktrees, remove_worktree,
)
from cc_textual.formatting import parse_context_tokens
from cc_textual.permissions import PermissionRequest, dummy_hook
from cc_textual.widgets import (
    ContextHeader,
    ContextBar,
    ChatMessage,
    ChatInput,
    ThinkingIndicator,
    ToolUseWidget,
    TaskWidget,
    TodoWidget,
    TodoPanel,
    SelectionPrompt,
    QuestionPrompt,
    SessionItem,
    WorktreePrompt,
    TextAreaAutoComplete,
)

log = logging.getLogger(__name__)


class ChatApp(App):
    """Main chat application."""

    CSS_PATH = Path(__file__).parent / "styles.tcss"

    BINDINGS = [
        Binding("ctrl+y", "copy_selection", "Copy", priority=True, show=False),
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        Binding("ctrl+l", "clear", "Clear", show=False),
        Binding("shift+tab", "cycle_permission_mode", "Auto-edit", priority=True),
        Binding("escape", "escape", "Cancel", show=False),
    ]

    # Auto-approve Edit/Write tools (but still prompt for Bash, etc.)
    AUTO_EDIT_TOOLS = {"Edit", "Write"}

    # Tools to collapse by default
    COLLAPSE_BY_DEFAULT = {"WebSearch", "WebFetch", "AskUserQuestion"}

    RECENT_TOOLS_EXPANDED = 2

    # Width threshold for showing sidebar
    SIDEBAR_MIN_WIDTH = 140

    # Max retries for worktree cleanup before giving up
    MAX_CLEANUP_ATTEMPTS = 3

    auto_approve_edits = reactive(False)

    def __init__(self, resume_session_id: str | None = None) -> None:
        super().__init__()
        self.client: ClaudeSDKClient | None = None
        self.current_response: ChatMessage | None = None
        self.session_id: str | None = None
        self.sdk_cwd: Path = Path.cwd()  # Track SDK's working directory
        self.pending_tools: dict[str, ToolUseWidget | TaskWidget] = {}
        self.active_tasks: dict[str, TaskWidget] = {}
        self.recent_tools: list[ToolUseWidget | TaskWidget] = []
        self._resume_on_start = resume_session_id
        self._session_picker_active = False
        self._pending_worktree_finish: dict | None = None  # Info for cleanup after merge
        self._worktree_cleanup_attempts: int = 0  # Track retry attempts
        # Event queues for testing
        self.interactions: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        self.completions: asyncio.Queue[ResponseComplete] = asyncio.Queue()

    async def _replace_client(self, options: ClaudeAgentOptions) -> None:
        """Safely replace current client with a new one."""
        # Cancel any permission prompts waiting for user input
        for prompt in list(self.query(SelectionPrompt)) + list(self.query(QuestionPrompt)):
            prompt.cancel()
        # Cancel any workers using the client before we disconnect
        for worker in list(self.workers):
            if worker.group in ("claude", "context") and worker.is_running:
                worker.cancel()
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

    async def _handle_permission(
        self, tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResult:
        """Handle permission request from SDK."""
        log.info(f"Permission requested for {tool_name}: {str(tool_input)[:100]}")

        if tool_name == "AskUserQuestion":
            return await self._handle_ask_user_question(tool_input)

        if self.auto_approve_edits and tool_name in self.AUTO_EDIT_TOOLS:
            log.info(f"Auto-approved {tool_name}")
            return PermissionResultAllow()

        request = PermissionRequest(tool_name, tool_input)
        await self.interactions.put(request)

        options = [("allow", "Yes, this time only"), ("deny", "No")]
        if tool_name in self.AUTO_EDIT_TOOLS:
            options.insert(0, ("allow_all", "Yes, all edits in this session"))

        prompt = SelectionPrompt(request.title, options)
        input_widget = self.query_one("#input", ChatInput)
        input_widget.add_class("hidden")
        self.query_one("#input-wrapper").mount(prompt)

        async def ui_response():
            result = await prompt.wait()
            if not request._event.is_set():
                request.respond(result)

        self.run_worker(ui_response(), exclusive=False)
        result = await request.wait()

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

    async def _handle_ask_user_question(
        self, tool_input: dict[str, Any]
    ) -> PermissionResult:
        """Handle AskUserQuestion tool."""
        questions = tool_input.get("questions", [])
        if not questions:
            return PermissionResultAllow(updated_input=tool_input)

        log.info(f"AskUserQuestion with {len(questions)} questions")

        prompt = QuestionPrompt(questions)
        input_widget = self.query_one("#input", ChatInput)
        input_widget.add_class("hidden")
        self.query_one("#input-wrapper").mount(prompt)

        answers = await prompt.wait()

        try:
            prompt.remove()
        except Exception:
            pass
        input_widget.remove_class("hidden")

        if not answers:
            return PermissionResultDeny(message="User cancelled questions")

        log.info(f"AskUserQuestion answers: {answers}")
        return PermissionResultAllow(
            updated_input={"questions": questions, "answers": answers}
        )

    def action_cycle_permission_mode(self) -> None:
        """Toggle auto-approve for Edit/Write tools."""
        self.auto_approve_edits = not self.auto_approve_edits
        self.notify(f"Auto-edit: {'ON' if self.auto_approve_edits else 'OFF'}")

    # Built-in slash commands (local to this app)
    LOCAL_COMMANDS = ["/clear", "/resume", "/worktree", "/worktree finish", "/worktree cleanup"]

    def compose(self) -> ComposeResult:
        yield ContextHeader()
        with Horizontal(id="main"):
            yield ListView(id="session-picker", classes="hidden")
            yield VerticalScroll(id="chat-view")
        yield TodoPanel(id="todo-panel", classes="hidden")
        with Horizontal(id="input-wrapper"):
            yield ChatInput(id="input")
            yield TextAreaAutoComplete(
                "#input",
                slash_commands=self.LOCAL_COMMANDS,  # Updated in on_mount
                base_path=Path.cwd(),
            )
        yield Footer()

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
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[dummy_hook])]},
        )

    async def on_mount(self) -> None:
        # Create client with resume if provided (avoids double client creation)
        resume = self._resume_on_start
        self.client = ClaudeSDKClient(self._make_options(resume=resume))
        await self.client.connect()
        if resume:
            self._load_and_display_history(resume)
            self.session_id = resume
            self.notify(f"Resuming {resume[:8]}...")
        # Fetch SDK commands and update autocomplete
        await self._update_slash_commands()
        self.query_one("#input", ChatInput).focus()

    async def _update_slash_commands(self) -> None:
        """Fetch available commands from SDK and update autocomplete."""
        try:
            info = await self.client.get_server_info()
            sdk_commands = ["/" + cmd["name"] for cmd in info.get("commands", [])]
            all_commands = self.LOCAL_COMMANDS + sdk_commands
            autocomplete = self.query_one(TextAreaAutoComplete)
            autocomplete.slash_commands = all_commands
        except Exception as e:
            log.warning(f"Failed to fetch SDK commands: {e}")
        self.refresh_context()

    def _load_and_display_history(self, session_id: str, cwd: Path | None = None) -> None:
        """Load session history and display in chat view."""
        chat_view = self.query_one("#chat-view", VerticalScroll)
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
        self.call_after_refresh(chat_view.scroll_end, animate=False)

    @work(group="context", exclusive=True, exit_on_error=False)
    async def refresh_context(self) -> None:
        """Silently run /context to get current usage."""
        if not self.client:
            return
        await self.client.query("/context")
        async for message in self.client.receive_response():
            if isinstance(message, UserMessage):
                content = getattr(message, "content", "")
                tokens = parse_context_tokens(content)
                if tokens is not None:
                    self.post_message(ContextUpdate(tokens))

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        if not event.text.strip():
            return

        prompt = event.text
        self.query_one("#input", ChatInput).clear()
        chat_view = self.query_one("#chat-view", VerticalScroll)

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
            self._handle_worktree_command(prompt.strip())
            return

        user_msg = ChatMessage(prompt)
        user_msg.add_class("user-message")
        chat_view.mount(user_msg)
        self.call_after_refresh(chat_view.scroll_end, animate=False)

        self.current_response = None
        self._show_thinking()
        self.run_claude(prompt)

    @work(group="claude", exclusive=True, exit_on_error=False)
    async def run_claude(self, prompt: str) -> None:
        if not self.client:
            return

        await self.client.query(prompt)
        had_tool_use: dict[str | None, bool] = {}

        async for message in self.client.receive_response():
            log.info(f"Message type: {type(message).__name__}")
            if isinstance(message, AssistantMessage):
                parent_id = message.parent_tool_use_id
                for block in message.content:
                    if isinstance(block, TextBlock):
                        new_msg = had_tool_use.get(parent_id, False)
                        self.post_message(
                            StreamChunk(block.text, new_message=new_msg, parent_tool_use_id=parent_id)
                        )
                        had_tool_use[parent_id] = False
                    elif isinstance(block, ToolUseBlock):
                        self.post_message(ToolUseMessage(block, parent_tool_use_id=parent_id))
                        had_tool_use[parent_id] = True
                    elif isinstance(block, ToolResultBlock):
                        self.post_message(ToolResultMessage(block, parent_tool_use_id=parent_id))
            elif isinstance(message, UserMessage):
                # UserMessage after ToolUseBlock means tool execution completed
                for widget in self.pending_tools.values():
                    widget.stop_spinner()
                content = getattr(message, "content", "")
                if "<local-command-stdout>" in content:
                    tokens = parse_context_tokens(content)
                    if tokens is not None:
                        self.post_message(ContextUpdate(tokens))
            elif isinstance(message, SystemMessage):
                subtype = getattr(message, "subtype", "")
                if subtype == "compact_boundary":
                    meta = getattr(message, "compact_metadata", None)
                    if meta:
                        self.call_from_thread(
                            self.notify, f"Compacted: {getattr(meta, 'pre_tokens', '?')} tokens"
                        )
            elif isinstance(message, ResultMessage):
                self.post_message(ResponseComplete(message))

    def _show_thinking(self) -> None:
        """Show the thinking indicator."""
        if self.query(ThinkingIndicator):
            return
        chat_view = self.query_one("#chat-view", VerticalScroll)
        chat_view.mount(ThinkingIndicator())
        self.call_after_refresh(chat_view.scroll_end, animate=False)

    def _hide_thinking(self) -> None:
        try:
            for ind in self.query(ThinkingIndicator):
                ind.remove()
        except Exception:
            pass

    def on_stream_chunk(self, event: StreamChunk) -> None:
        self._hide_thinking()
        if event.parent_tool_use_id and event.parent_tool_use_id in self.active_tasks:
            task = self.active_tasks[event.parent_tool_use_id]
            task.add_text(event.text, new_message=event.new_message)
            return

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
        self._hide_thinking()
        if event.parent_tool_use_id and event.parent_tool_use_id in self.active_tasks:
            task = self.active_tasks[event.parent_tool_use_id]
            task.add_tool_use(event.block)
            return

        chat_view = self.query_one("#chat-view", VerticalScroll)

        # TodoWrite gets special handling - update sidebar panel and/or inline widget
        if event.block.name == "TodoWrite":
            todos = event.block.input.get("todos", [])
            panel = self.query_one("#todo-panel", TodoPanel)
            panel.update_todos(todos)
            self._position_todo_panel()
            # Also update inline widget if exists, or create if narrow
            existing = self.query(TodoWidget)
            if existing:
                existing[0].update_todos(todos)
            elif self.size.width < self.SIDEBAR_MIN_WIDTH:
                chat_view.mount(TodoWidget(todos))
            self.call_after_refresh(chat_view.scroll_end, animate=False)
            self._show_thinking()
            return

        while len(self.recent_tools) >= self.RECENT_TOOLS_EXPANDED:
            old = self.recent_tools.pop(0)
            old.collapse()

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
        self._hide_thinking()  # Tool widget has its own spinner

    def on_tool_result_message(self, event: ToolResultMessage) -> None:
        if event.parent_tool_use_id and event.parent_tool_use_id in self.active_tasks:
            task = self.active_tasks[event.parent_tool_use_id]
            task.add_tool_result(event.block)
            return

        widget = self.pending_tools.get(event.block.tool_use_id)
        if widget:
            widget.set_result(event.block)
            del self.pending_tools[event.block.tool_use_id]
            if event.block.tool_use_id in self.active_tasks:
                del self.active_tasks[event.block.tool_use_id]
        self._show_thinking()

    def on_context_update(self, event: ContextUpdate) -> None:
        self.query_one("#context-bar", ContextBar).tokens = event.tokens

    def on_resize(self, event) -> None:
        """Reposition todo panel on resize."""
        self._position_todo_panel()

    def _position_todo_panel(self) -> None:
        """Show/hide and position todo panel based on terminal width."""
        panel = self.query_one("#todo-panel", TodoPanel)
        if self.size.width >= self.SIDEBAR_MIN_WIDTH and panel.todos:
            panel.remove_class("hidden")
            chat_left = (self.size.width - 100) // 2
            panel.styles.offset = (chat_left + 100, 2)
        else:
            panel.add_class("hidden")

    def on_response_complete(self, event: ResponseComplete) -> None:
        self._hide_thinking()
        if event.result:
            self.session_id = event.result.session_id
            self.refresh_context()
        self.current_response = None
        self.query_one("#input", ChatInput).focus()
        self.completions.put_nowait(event)

        # Attempt worktree cleanup if pending
        if self._pending_worktree_finish:
            self._attempt_worktree_cleanup()

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
            log.exception(f"Resume failed: {e}")
            self.post_message(ResponseComplete(None))

    def action_clear(self) -> None:
        chat_view = self.query_one("#chat-view", VerticalScroll)
        chat_view.remove_children()

    def action_copy_selection(self) -> None:
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.notify("Copied to clipboard")

    def on_mouse_up(self, event: MouseUp) -> None:
        self.set_timer(0.05, self._check_and_copy_selection)

    def _check_and_copy_selection(self) -> None:
        selected = self.screen.get_selected_text()
        if selected and len(selected.strip()) > 0:
            self.copy_to_clipboard(selected)

    def action_quit(self) -> None:
        now = time.time()
        if hasattr(self, "_last_quit_time") and now - self._last_quit_time < 1.0:
            self.run_worker(self._cleanup_and_exit())
        else:
            self._last_quit_time = now
            self.notify("Press Ctrl+C again to quit")

    async def _cleanup_and_exit(self) -> None:
        """Disconnect SDK and exit."""
        old = self.client
        self.client = None
        if old:
            try:
                await old.interrupt()
            except Exception:
                pass
            # Skip disconnect() - causes race conditions
        self.exit()

    def _show_session_picker(self) -> None:
        picker = self.query_one("#session-picker", ListView)
        chat_view = self.query_one("#chat-view", VerticalScroll)
        picker.remove_class("hidden")
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
        self.query_one("#chat-view", VerticalScroll).remove_class("hidden")
        self.query_one("#input", ChatInput).clear()
        self.query_one("#input", ChatInput).focus()

    def _handle_worktree_command(self, command: str) -> None:
        """Handle /worktree commands."""
        parts = command.split(maxsplit=2)

        if len(parts) == 1:
            self._show_worktree_modal()
            return

        subcommand = parts[1]
        if subcommand == "finish":
            success, message, info = get_finish_worktree_info(self.sdk_cwd)
            if not success:
                self.notify(message, severity="error")
                return
            # Store info for cleanup after Claude completes
            self._pending_worktree_finish = info
            self._worktree_cleanup_attempts = 0
            # Send prompt to Claude for rebase/merge only
            prompt = f"""Rebase and merge this feature branch:

Branch: {info['branch_name']}
Base branch: {info['base_branch']}
Worktree dir: {info['worktree_dir']}
Main dir: {info['main_dir']}

Steps:
1. Check for uncommitted changes in the worktree (fail if any)
2. Rebase {info['branch_name']} onto {info['base_branch']} (resolve any conflicts)
3. In the main dir ({info['main_dir']}), merge {info['branch_name']}:
   cd {info['main_dir']} && git merge {info['branch_name']}

Do NOT remove the worktree or delete the branch - the app will handle cleanup."""
            chat_view = self.query_one("#chat-view", VerticalScroll)
            user_msg = ChatMessage(f"/worktree finish")
            user_msg.add_class("user-message")
            chat_view.mount(user_msg)
            self._show_thinking()
            self.run_claude(prompt)
        elif subcommand == "cleanup":
            branches = parts[2].split() if len(parts) > 2 else None
            self._handle_worktree_cleanup(branches)
        else:
            self._switch_or_create_worktree(subcommand)

    def _switch_or_create_worktree(self, feature_name: str) -> None:
        """Switch to existing worktree or create new one."""
        existing = [wt for wt in list_worktrees() if wt.branch == feature_name]
        if existing:
            wt = existing[0]
            self.notify(f"Switching to {feature_name}...")
            self.sub_title = f"[worktree: {feature_name}]"
            self._reconnect_sdk(wt.path)
        else:
            success, message, new_cwd = start_worktree(feature_name)
            if success and new_cwd:
                self.notify(f"Worktree ready: {feature_name}")
                self.sub_title = f"[worktree: {feature_name}]"
                self._reconnect_sdk(new_cwd)
            else:
                self.notify(message, severity="error")

    def _handle_worktree_cleanup(self, branches: list[str] | None) -> None:
        """Handle /worktree cleanup command."""
        results = cleanup_worktrees(branches)

        if not results:
            self.notify("No worktrees to clean up")
            return

        # Check if any need confirmation
        needs_confirm = [(b, msg) for b, success, msg, confirm in results if confirm]
        removed = [(b, msg) for b, success, msg, confirm in results if success]
        failed = [(b, msg) for b, success, msg, confirm in results if not success and not confirm]

        # Report results
        for branch, msg in removed:
            self.notify(f"Removed: {branch}")
        for branch, msg in failed:
            self.notify(f"Failed: {branch} - {msg}", severity="error")

        # Prompt for confirmation on dirty/unmerged
        if needs_confirm:
            self._run_cleanup_prompt(needs_confirm)

    @work(group="cleanup_prompt", exclusive=True, exit_on_error=False)
    async def _run_cleanup_prompt(self, needs_confirm: list[tuple[str, str]]) -> None:
        """Show prompt for confirming worktree removal."""
        from cc_textual.widgets import SelectionPrompt
        input_wrapper = self.query_one("#input-wrapper", Horizontal)
        try:
            input_wrapper.query_one("#input", ChatInput).remove()
        except Exception:
            pass

        # Build options: (value, label) tuples
        branches_to_confirm = [b for b, _ in needs_confirm]
        options = [("all", f"Remove all ({len(needs_confirm)})")]
        options.extend((b, f"Remove {b} ({msg})") for b, msg in needs_confirm)
        options.append(("cancel", "Cancel"))

        prompt = SelectionPrompt("Worktrees with changes or unmerged:", options)
        input_wrapper.mount(prompt)
        prompt.focus()

        selected = await prompt.wait()

        if selected and selected != "cancel":
            to_remove = branches_to_confirm if selected == "all" else [selected]
            worktrees = list_worktrees()
            for branch in to_remove:
                wt = next((w for w in worktrees if w.branch == branch), None)
                if wt:
                    success, msg = remove_worktree(wt, force=True)
                    self.notify(f"Removed: {branch}" if success else msg, severity="error" if not success else "information")
        else:
            self.notify("Cleanup cancelled")

        self._restore_chat_input()

    def _attempt_worktree_cleanup(self) -> None:
        """Attempt to clean up worktree, asking Claude for help if it fails."""
        info = self._pending_worktree_finish
        if not info:
            return
        main_dir = Path(info["main_dir"])
        worktree_dir = info["worktree_dir"]
        branch_name = info["branch_name"]

        # Try worktree removal - this is the critical step
        result = subprocess.run(
            ["git", "worktree", "remove", worktree_dir],
            cwd=main_dir, capture_output=True, text=True
        )
        if result.returncode != 0:
            self._handle_cleanup_failure(result.stderr, info)
            return

        # Worktree removed - clear pending state before branch deletion
        self._pending_worktree_finish = None
        self._worktree_cleanup_attempts = 0

        # Try branch deletion - less critical, just warn if it fails
        result = subprocess.run(
            ["git", "branch", "-d", branch_name],
            cwd=main_dir, capture_output=True, text=True
        )
        if result.returncode != 0:
            self.notify(f"Branch {branch_name} not deleted: {result.stderr.strip()}", severity="warning")
        else:
            self.notify(f"Cleaned up {branch_name}")

        self.sub_title = ""
        self._reconnect_sdk(main_dir)

    def _handle_cleanup_failure(self, error: str, info: dict) -> None:
        """Handle cleanup failure by asking Claude to fix it or giving up."""
        self._worktree_cleanup_attempts += 1

        if self._worktree_cleanup_attempts >= self.MAX_CLEANUP_ATTEMPTS:
            self._pending_worktree_finish = None
            self._worktree_cleanup_attempts = 0
            self.notify(f"Cleanup failed after {self.MAX_CLEANUP_ATTEMPTS} attempts: {error}", severity="error")
            return

        # Ask Claude to fix the issue
        prompt = f"""The worktree cleanup failed with this error:

{error}

Worktree dir: {info['worktree_dir']}

Please fix this issue (e.g., remove untracked files, resolve uncommitted changes) so the worktree can be removed cleanly."""

        chat_view = self.query_one("#chat-view", VerticalScroll)
        user_msg = ChatMessage(f"[Cleanup attempt {self._worktree_cleanup_attempts}/{self.MAX_CLEANUP_ATTEMPTS} failed]")
        user_msg.add_class("user-message")
        chat_view.mount(user_msg)
        self._show_thinking()
        self.run_claude(prompt)

    def _show_worktree_modal(self) -> None:
        """Show worktree selection modal."""
        worktrees = [(str(wt.path), wt.branch) for wt in list_worktrees() if not wt.is_main]
        prompt = WorktreePrompt(worktrees)
        container = Center(prompt, id="worktree-modal")
        self.mount(container)
        self._wait_for_worktree_selection(prompt, container)

    @work(group="worktree", exclusive=True, exit_on_error=False)
    async def _wait_for_worktree_selection(self, prompt: WorktreePrompt, container: Center) -> None:
        """Wait for worktree modal selection and act on it."""
        try:
            result = await prompt.wait()
            container.remove()
            if result is None:
                return  # Cancelled

            action, value = result
            if action == "switch":
                # value is the path; find the branch name from worktrees
                path = Path(value)
                worktrees = {str(wt.path): wt.branch for wt in list_worktrees()}
                branch = worktrees.get(value, path.name)
                self.notify(f"Switching to {branch}...")
                self.sub_title = f"[worktree: {branch}]"
                self._reconnect_sdk(path)
            elif action == "new":
                self._switch_or_create_worktree(value)
        except Exception as e:
            log.exception(f"Worktree selection error: {e}")
            self.notify(f"Error: {e}", severity="error")

    @work(group="reconnect", exclusive=True, exit_on_error=False)
    async def _reconnect_sdk(self, new_cwd: Path) -> None:
        """Reconnect SDK with a new working directory."""
        try:
            # Check for existing session BEFORE creating client
            sessions = get_recent_sessions(limit=1, cwd=new_cwd)
            resume_id = sessions[0][0] if sessions else None

            await self._replace_client(self._make_options(cwd=new_cwd, resume=resume_id))

            # Clear internal state
            self.current_response = None
            self.pending_tools.clear()
            self.active_tasks.clear()
            self.recent_tools.clear()
            self.sdk_cwd = new_cwd

            if resume_id:
                self._load_and_display_history(resume_id, cwd=new_cwd)
                self.session_id = resume_id
                self.notify(f"Resumed session in {new_cwd.name}")
            else:
                # Clear chat view only if not resuming (resume does its own clear)
                try:
                    chat_view = self.query_one("#chat-view", VerticalScroll)
                    chat_view.remove_children()
                except Exception:
                    pass  # App may be exiting
                self.session_id = None
                self.notify(f"SDK reconnected in {new_cwd.name}")
        except Exception as e:
            log.exception(f"SDK reconnect failed: {e}")
            self.notify(f"SDK reconnect failed: {e}", severity="error")

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

        # Interrupt running agent (check for active "claude" worker)
        claude_workers = [w for w in self.workers if w.group == "claude" and w.is_running]
        if self.client and claude_workers:
            # Cancel workers and send interrupt signal to SDK
            for worker in claude_workers:
                worker.cancel()
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

    def on_app_focus(self) -> None:
        self.query_one("#input", ChatInput).focus()

    def on_key(self, event) -> None:
        if self.query(SelectionPrompt):
            return
        input_widget = self.query_one("#input", ChatInput)
        if self.focused == input_widget:
            return
        if len(event.character or "") == 1 and event.character.isprintable():
            input_widget.focus()
            input_widget.insert(event.character)
            event.prevent_default()
            event.stop()
