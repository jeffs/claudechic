"""Selection and question prompts for user interaction."""

import threading

import anyio

from textual.app import ComposeResult
from textual.widgets import Static, Label, ListItem, Input


class SessionItem(ListItem):
    """A session in the sidebar."""

    def __init__(self, session_id: str, preview: str, msg_count: int = 0) -> None:
        super().__init__()
        self.session_id = session_id
        self.preview = preview
        self.msg_count = msg_count

    def compose(self) -> ComposeResult:
        yield Label(f"{self.preview[:50]}\n({self.msg_count} msgs)")


class WorktreeItem(ListItem):
    """A worktree in the picker."""

    def __init__(self, path: str, branch: str, is_main: bool = False) -> None:
        super().__init__()
        self.path = path
        self.branch = branch
        self.is_main = is_main

    def compose(self) -> ComposeResult:
        marker = " (main)" if self.is_main else ""
        yield Label(f"{self.branch}{marker}")


class WorktreePrompt(Static):
    """Modal prompt for selecting or creating worktrees."""

    can_focus = True

    def __init__(self, worktrees: list[tuple[str, str]]) -> None:
        """Create worktree prompt.

        Args:
            worktrees: List of (path, branch) tuples for existing worktrees
        """
        super().__init__()
        self.worktrees = worktrees
        self.selected_idx = 0
        self._result_event = threading.Event()
        self._result_value: tuple[str, str] | None = None  # (action, value)
        self._in_new_mode = False
        self._new_text = ""

    def compose(self) -> ComposeResult:
        yield Static("Worktrees", classes="wt-title")
        for i, (path, branch) in enumerate(self.worktrees):
            classes = "prompt-option selected" if i == 0 else "prompt-option"
            yield Static(f"{i + 1}. {branch}", classes=classes, id=f"wt-{i}")
        # "New" option at the end - placeholder style
        new_idx = len(self.worktrees)
        classes = "prompt-option wt-placeholder selected" if new_idx == 0 else "prompt-option wt-placeholder"
        yield Static(f"{new_idx + 1}. Enter name...", classes=classes, id=f"wt-{new_idx}")

    def on_mount(self) -> None:
        self.focus()

    def _total_options(self) -> int:
        return len(self.worktrees) + 1  # +1 for "New"

    def _update_selection(self) -> None:
        for i in range(self._total_options()):
            try:
                opt = self.query_one(f"#wt-{i}", Static)
                if i == self.selected_idx:
                    opt.add_class("selected")
                else:
                    opt.remove_class("selected")
            except Exception:
                pass

    def _enter_new_mode(self) -> None:
        self._in_new_mode = True
        new_idx = len(self.worktrees)
        opt = self.query_one(f"#wt-{new_idx}", Static)
        opt.remove_class("wt-placeholder")
        opt.update(f"{new_idx + 1}. {self._new_text}_")

    def _update_new_display(self) -> None:
        new_idx = len(self.worktrees)
        opt = self.query_one(f"#wt-{new_idx}", Static)
        opt.update(f"{new_idx + 1}. {self._new_text}_")

    def _exit_new_mode(self) -> None:
        self._in_new_mode = False
        self._new_text = ""
        new_idx = len(self.worktrees)
        opt = self.query_one(f"#wt-{new_idx}", Static)
        opt.add_class("wt-placeholder")
        opt.update(f"{new_idx + 1}. Enter name...")

    def on_key(self, event) -> None:
        if self._in_new_mode:
            if event.key == "escape":
                self._exit_new_mode()
                event.prevent_default()
                event.stop()
            elif event.key == "enter":
                if self._new_text.strip():
                    self._resolve(("new", self._new_text.strip()))
                else:
                    self._exit_new_mode()
                event.prevent_default()
                event.stop()
            elif event.key == "backspace":
                self._new_text = self._new_text[:-1]
                self._update_new_display()
                event.prevent_default()
                event.stop()
            elif len(event.character or "") == 1 and event.character.isprintable():
                self._new_text += event.character
                self._update_new_display()
                event.prevent_default()
                event.stop()
            return

        # Normal mode
        if event.key == "up":
            self.selected_idx = (self.selected_idx - 1) % self._total_options()
            self._update_selection()
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self.selected_idx = (self.selected_idx + 1) % self._total_options()
            self._update_selection()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            if self.selected_idx < len(self.worktrees):
                path, branch = self.worktrees[self.selected_idx]
                self._resolve(("switch", path))
            else:
                self._enter_new_mode()
            event.prevent_default()
            event.stop()
        elif event.key == "escape":
            self._resolve(None)
            event.prevent_default()
            event.stop()
        elif event.key.isdigit():
            idx = int(event.key) - 1
            if 0 <= idx < len(self.worktrees):
                path, branch = self.worktrees[idx]
                self._resolve(("switch", path))
                event.prevent_default()
                event.stop()
            elif idx == len(self.worktrees):
                self._enter_new_mode()
                event.prevent_default()
                event.stop()
        elif len(event.character or "") == 1 and event.character.isalpha():
            # Start typing -> go to new mode with this character
            self.selected_idx = len(self.worktrees)
            self._update_selection()
            self._new_text = event.character
            self._enter_new_mode()
            event.prevent_default()
            event.stop()

    def _resolve(self, result: tuple[str, str] | None) -> None:
        if not self._result_event.is_set():
            self._result_value = result
            self._result_event.set()
        self.remove()

    async def wait(self) -> tuple[str, str] | None:
        """Wait for selection. Returns (action, value) or None if cancelled."""
        while not self._result_event.is_set():
            await anyio.sleep(0.05)
        return self._result_value


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
        self._in_other_mode = False

    def compose(self) -> ComposeResult:
        yield from self._render_question()

    def _render_question(self):
        """Yield widgets for current question."""
        q = self.questions[self.current_q]
        yield Static(
            f"[{self.current_q + 1}/{len(self.questions)}] {q['question']}",
            classes="prompt-title",
        )
        for i, opt in enumerate(q.get("options", [])):
            classes = "prompt-option selected" if i == self.selected_idx else "prompt-option"
            label = opt.get("label", "?")
            desc = opt.get("description", "")
            text = f"{i + 1}. {label}" + (f" - {desc}" if desc else "")
            yield Static(text, classes=classes, id=f"opt-{i}")
        # "Other" option
        other_idx = len(q.get("options", []))
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
        total = len(q.get("options", [])) + 1
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
        options = q.get("options", [])
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
        options = q.get("options", [])

        if self.selected_idx < len(options):
            answer = options[self.selected_idx].get("label", "?")
            self._record_answer(answer)
        else:
            self._enter_other_mode()

    def _enter_other_mode(self) -> None:
        """Show text input for custom answer."""
        self._in_other_mode = True
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
            input_widget = self.query_one("#other-input", Input)
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
        self.answers[q["question"]] = answer

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
