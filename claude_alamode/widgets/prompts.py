"""Selection and question prompts for user interaction."""

import threading
from typing import Any

import anyio

from textual.app import ComposeResult
from textual.widgets import Static, Label, ListItem


class SessionItem(ListItem):
    """A session in the sidebar."""

    def __init__(self, session_id: str, preview: str, msg_count: int = 0) -> None:
        super().__init__()
        self.session_id = session_id
        self.preview = preview
        self.msg_count = msg_count

    def compose(self) -> ComposeResult:
        yield Label(f"{self.preview[:50]}\n({self.msg_count} msgs)")


class BasePrompt(Static):
    """Base class for selection prompts with arrow/number navigation and optional text input."""

    can_focus = True

    def __init__(self) -> None:
        super().__init__()
        self.selected_idx = 0
        self._result_event = threading.Event()
        self._result_value: Any = None
        # Text input mode (for "Other" / "New" options)
        self._in_text_mode = False
        self._text_buffer = ""

    def _total_options(self) -> int:
        """Return total number of selectable options. Override in subclasses."""
        return 0

    def _get_option_id(self, idx: int) -> str:
        """Return the DOM id for option at index."""
        return f"opt-{idx}"

    def _text_option_idx(self) -> int | None:
        """Return index of text input option, or None if no text input. Override in subclasses."""
        return None

    def _text_option_placeholder(self) -> str:
        """Return placeholder text for text input option. Override in subclasses."""
        return "Enter text..."

    def _update_selection(self) -> None:
        """Update visual selection state."""
        for i in range(self._total_options()):
            try:
                opt = self.query_one(f"#{self._get_option_id(i)}", Static)
                if i == self.selected_idx:
                    opt.add_class("selected")
                else:
                    opt.remove_class("selected")
            except Exception:
                pass

    def _resolve(self, result: Any) -> None:
        """Set result and signal completion."""
        if not self._result_event.is_set():
            self._result_value = result
            self._result_event.set()
        self.remove()

    def on_mount(self) -> None:
        """Auto-focus on mount to capture keys immediately."""
        self.focus()

    def on_blur(self) -> None:
        """Refocus when focus is lost - prompt must stay focused."""
        self.focus()

    def cancel(self) -> None:
        """Cancel this prompt."""
        self._resolve(None)

    def _select_option(self, idx: int) -> None:
        """Handle selection of option at index. Override in subclasses."""
        pass

    def _submit_text(self, text: str) -> None:
        """Handle text submission. Override in subclasses that support text input."""
        pass

    # Text mode methods
    def _enter_text_mode(self) -> None:
        """Enter inline text input mode."""
        self._in_text_mode = True
        text_idx = self._text_option_idx()
        if text_idx is not None:
            opt = self.query_one(f"#{self._get_option_id(text_idx)}", Static)
            opt.remove_class("prompt-placeholder")

    def _update_text_display(self) -> None:
        """Update the text input display with current buffer."""
        text_idx = self._text_option_idx()
        if text_idx is not None:
            opt = self.query_one(f"#{self._get_option_id(text_idx)}", Static)
            opt.update(f"{text_idx + 1}. {self._text_buffer}_")

    def _exit_text_mode(self) -> None:
        """Exit text mode and restore placeholder."""
        self._in_text_mode = False
        self._text_buffer = ""
        text_idx = self._text_option_idx()
        if text_idx is not None:
            opt = self.query_one(f"#{self._get_option_id(text_idx)}", Static)
            opt.add_class("prompt-placeholder")
            opt.update(f"{text_idx + 1}. {self._text_option_placeholder()}")

    def _handle_text_mode_key(self, event) -> bool:
        """Handle keys in text mode. Returns True if handled."""
        if event.key in ("escape", "up", "down"):
            self._exit_text_mode()
            if event.key in ("up", "down"):
                # Let navigation happen after exiting text mode
                return False
            return True
        elif event.key == "enter":
            if self._text_buffer.strip():
                self._submit_text(self._text_buffer.strip())
            else:
                self._exit_text_mode()
            return True
        elif event.key == "backspace":
            self._text_buffer = self._text_buffer[:-1]
            self._update_text_display()
            return True
        elif len(event.character or "") == 1 and event.character.isprintable():
            self._text_buffer += event.character
            self._update_text_display()
            return True
        return True  # Consume other keys in text mode

    def _handle_key(self, event) -> bool:
        """Handle common navigation keys. Returns True if handled."""
        if event.key == "up":
            self.selected_idx = (self.selected_idx - 1) % self._total_options()
            self._update_selection()
            return True
        elif event.key == "down":
            self.selected_idx = (self.selected_idx + 1) % self._total_options()
            self._update_selection()
            return True
        elif event.key == "enter":
            self._select_option(self.selected_idx)
            return True
        elif event.key == "escape":
            self.cancel()
            return True
        elif event.key.isdigit():
            idx = int(event.key) - 1
            if 0 <= idx < self._total_options():
                self._select_option(idx)
                return True
        return False

    def on_key(self, event) -> None:
        # Text mode takes priority
        if self._in_text_mode:
            if self._handle_text_mode_key(event):
                event.prevent_default()
                event.stop()
                return
            # Fall through for up/down navigation

        # Start typing to enter text mode (if text option exists)
        text_idx = self._text_option_idx()
        if text_idx is not None and len(event.character or "") == 1 and event.character.isalpha():
            self.selected_idx = text_idx
            self._update_selection()
            self._text_buffer = event.character
            self._enter_text_mode()
            self._update_text_display()
            event.prevent_default()
            event.stop()
            return

        # Default navigation
        if self._handle_key(event):
            event.prevent_default()
            event.stop()

    async def wait(self) -> Any:
        """Wait for selection. Returns result or None if cancelled."""
        while not self._result_event.is_set():
            await anyio.sleep(0.05)
        return self._result_value


class SelectionPrompt(BasePrompt):
    """Simple selection prompt with arrow/number navigation."""

    def __init__(self, title: str, options: list[tuple[str, str]]) -> None:
        """Create selection prompt.

        Args:
            title: Prompt title/question
            options: List of (value, label) tuples
        """
        super().__init__()
        self.title = title
        self.options = options
        self._result_value = options[0][0] if options else ""

    def compose(self) -> ComposeResult:
        yield Static(self.title, classes="prompt-title")
        for i, (value, label) in enumerate(self.options):
            classes = "prompt-option selected" if i == 0 else "prompt-option"
            yield Static(f"{i + 1}. {label}", classes=classes, id=f"opt-{i}")

    def _total_options(self) -> int:
        return len(self.options)

    def _select_option(self, idx: int) -> None:
        self._resolve(self.options[idx][0])

    def cancel(self) -> None:
        self._resolve("")

    async def wait(self) -> str:
        """Wait for selection. Returns value or empty string if cancelled."""
        await super().wait()
        return self._result_value


class WorktreePrompt(BasePrompt):
    """Prompt for selecting or creating worktrees."""

    def __init__(self, worktrees: list[tuple[str, str]]) -> None:
        """Create worktree prompt.

        Args:
            worktrees: List of (path, branch) tuples for existing worktrees
        """
        super().__init__()
        self.worktrees = worktrees

    def compose(self) -> ComposeResult:
        yield Static("Worktrees", classes="prompt-title")
        for i, (path, branch) in enumerate(self.worktrees):
            classes = "prompt-option selected" if i == 0 else "prompt-option"
            yield Static(f"{i + 1}. {branch}", classes=classes, id=f"opt-{i}")
        # "New" option at the end
        new_idx = len(self.worktrees)
        classes = "prompt-option prompt-placeholder"
        if new_idx == 0:
            classes += " selected"
        yield Static(f"{new_idx + 1}. {self._text_option_placeholder()}", classes=classes, id=f"opt-{new_idx}")

    def _total_options(self) -> int:
        return len(self.worktrees) + 1  # +1 for "New"

    def _text_option_idx(self) -> int:
        return len(self.worktrees)

    def _text_option_placeholder(self) -> str:
        return "Enter name..."

    def _select_option(self, idx: int) -> None:
        if idx < len(self.worktrees):
            path, branch = self.worktrees[idx]
            self._resolve(("switch", path))
        else:
            self._text_buffer = ""
            self._enter_text_mode()
            self._update_text_display()

    def _submit_text(self, text: str) -> None:
        self._resolve(("new", text))

    async def wait(self) -> tuple[str, str] | None:
        """Wait for selection. Returns (action, value) or None if cancelled."""
        await super().wait()
        return self._result_value


class QuestionPrompt(BasePrompt):
    """Multi-question prompt for AskUserQuestion tool."""

    def __init__(self, questions: list[dict]) -> None:
        super().__init__()
        self.questions = questions
        self.current_q = 0
        self.answers: dict[str, str] = {}

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
        classes = "prompt-option prompt-placeholder"
        if self.selected_idx == other_idx:
            classes += " selected"
        yield Static(f"{other_idx + 1}. {self._text_option_placeholder()}", classes=classes, id=f"opt-{other_idx}")

    def _total_options(self) -> int:
        q = self.questions[self.current_q]
        return len(q.get("options", [])) + 1  # +1 for "Other"

    def _text_option_idx(self) -> int:
        q = self.questions[self.current_q]
        return len(q.get("options", []))

    def _text_option_placeholder(self) -> str:
        return "Other..."

    def _select_option(self, idx: int) -> None:
        q = self.questions[self.current_q]
        options = q.get("options", [])
        if idx < len(options):
            answer = options[idx].get("label", "?")
            self._record_answer(answer)
        else:
            self._text_buffer = ""
            self._enter_text_mode()
            self._update_text_display()

    def _submit_text(self, text: str) -> None:
        self._record_answer(text)

    def _record_answer(self, answer: str) -> None:
        """Record answer and advance to next question or finish."""
        q = self.questions[self.current_q]
        self.answers[q["question"]] = answer

        if self.current_q < len(self.questions) - 1:
            self.current_q += 1
            self.selected_idx = 0
            self._in_text_mode = False
            self._text_buffer = ""
            self.remove_children()
            for w in self._render_question():
                self.mount(w)
        else:
            self._resolve(self.answers)

    def cancel(self) -> None:
        self.answers = {}
        self._resolve({})

    async def wait(self) -> dict[str, str]:
        """Wait for all answers. Returns answers dict or empty if cancelled."""
        await super().wait()
        return self._result_value if self._result_value else {}
