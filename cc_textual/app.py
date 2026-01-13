"""Claude Code Textual UI - Main application."""

import asyncio
import logging
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
from cc_textual.worktree import start_worktree, finish_worktree, get_worktree_status, list_worktrees
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
    SelectionPrompt,
    QuestionPrompt,
    SessionItem,
    WorktreePrompt,
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
        Binding("escape", "cancel_picker", "Cancel", show=False),
    ]

    # Auto-approve Edit/Write tools (but still prompt for Bash, etc.)
    AUTO_EDIT_TOOLS = {"Edit", "Write"}

    # Tools to collapse by default
    COLLAPSE_BY_DEFAULT = {"WebSearch", "WebFetch", "AskUserQuestion"}

    RECENT_TOOLS_EXPANDED = 2

    auto_approve_edits = reactive(False)

    def __init__(self, resume_session_id: str | None = None) -> None:
        super().__init__()
        self.options = ClaudeAgentOptions(
            permission_mode="default",
            env={"ANTHROPIC_API_KEY": ""},
            setting_sources=["user", "project", "local"],
            can_use_tool=self._handle_permission,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[dummy_hook])]},
        )
        self.client: ClaudeSDKClient | None = None
        self.current_response: ChatMessage | None = None
        self.session_id: str | None = None
        self.pending_tools: dict[str, ToolUseWidget | TaskWidget] = {}
        self.active_tasks: dict[str, TaskWidget] = {}
        self.recent_tools: list[ToolUseWidget | TaskWidget] = []
        self._resume_on_start = resume_session_id
        self._session_picker_active = False
        # Event queues for testing
        self.interactions: asyncio.Queue[PermissionRequest] = asyncio.Queue()
        self.completions: asyncio.Queue[ResponseComplete] = asyncio.Queue()

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
                msg = ChatMessage(m["content"][:500])
                msg.add_class("user-message")
                chat_view.mount(msg)
            elif m["type"] == "assistant":
                msg = ChatMessage(m["content"][:1000])
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
        self._show_thinking()

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

    def on_response_complete(self, event: ResponseComplete) -> None:
        self._hide_thinking()
        if event.result:
            self.session_id = event.result.session_id
            self.refresh_context()
        self.current_response = None
        self.query_one("#input", ChatInput).focus()
        self.completions.put_nowait(event)

    @work(group="resume", exclusive=True, exit_on_error=False)
    async def resume_session(self, session_id: str) -> None:
        """Resume a session by creating a new client."""
        log.info(f"resume_session started: {session_id}")
        try:
            if self.client:
                await self.client.disconnect()
            self.client = None
            options = ClaudeAgentOptions(
                permission_mode="default",
                env={"ANTHROPIC_API_KEY": ""},
                setting_sources=["user", "project", "local"],
                resume=session_id,
                can_use_tool=self._handle_permission,
            )
            client = ClaudeSDKClient(options)
            await client.connect()
            self.client = client
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
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass  # Best effort cleanup
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
            success, message, original_cwd = finish_worktree()
            if success and original_cwd:
                self.notify("Worktree merged and cleaned up")
                self.sub_title = ""
                self._reconnect_sdk(original_cwd)
            else:
                self.notify(message, severity="error")
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
            old_client = self.client

            # Interrupt old client first (like pressing Escape)
            if old_client:
                try:
                    old_client.interrupt()
                except Exception:
                    pass

            # Create new client
            options = ClaudeAgentOptions(
                permission_mode="default",
                env={"ANTHROPIC_API_KEY": ""},
                setting_sources=["user", "project", "local"],
                cwd=new_cwd,
                can_use_tool=self._handle_permission,
                hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[dummy_hook])]},
            )
            new_client = ClaudeSDKClient(options)
            await new_client.connect()
            self.client = new_client

            # Don't disconnect old client - let it be garbage collected
            # The disconnect() seems to cause the app to exit

            # Clear UI state - this is effectively a new session
            chat_view = self.query_one("#chat-view", VerticalScroll)
            chat_view.remove_children()
            self.current_response = None
            self.pending_tools.clear()
            self.active_tasks.clear()
            self.recent_tools.clear()
            self.session_id = None

            self.notify(f"SDK reconnected in {new_cwd.name}")
        except Exception as e:
            log.exception(f"SDK reconnect failed: {e}")
            self.notify(f"SDK reconnect failed: {e}", severity="error")

    def action_cancel_picker(self) -> None:
        if self._session_picker_active:
            self._hide_session_picker()

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
