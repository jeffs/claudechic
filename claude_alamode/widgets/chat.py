"""Chat widgets - messages, input, and thinking indicator."""

import pyperclip

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Markdown, TextArea, Static, Button


class ThinkingIndicator(Static):
    """Animated spinner shown when Claude is thinking."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    frame = reactive(0)

    def __init__(self) -> None:
        super().__init__("⠋ Thinking...")

    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / 10, self._tick)

    def on_unmount(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self.frame = (self.frame + 1) % len(self.FRAMES)

    def watch_frame(self, frame: int) -> None:
        self.update(f"{self.FRAMES[frame]} Thinking...")
        self.refresh()


class ChatMessage(Static):
    """A single chat message with copy button."""

    def __init__(self, content: str = "") -> None:
        super().__init__()
        self._content = content.rstrip()

    def compose(self) -> ComposeResult:
        yield Button("\u238c", id="copy-btn", classes="copy-btn")
        yield Markdown(self._content, id="content")

    def append_content(self, text: str) -> None:
        """Append text to message content."""
        self._content += text
        try:
            md = self.query_one("#content", Markdown)
            md.update(self._content.rstrip())
        except Exception:
            pass  # Widget not mounted yet

    def get_raw_content(self) -> str:
        """Get raw content for copying."""
        return self._content

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-btn":
            try:
                pyperclip.copy(self.get_raw_content())
                self.app.notify("Copied to clipboard")
            except Exception as e:
                self.app.notify(f"Copy failed: {e}", severity="error")


class ChatInput(TextArea):
    """Text input that submits on Enter, newline on Shift+Enter, history with Up/Down."""

    BINDINGS = [
        Binding("enter", "submit", "Send", priority=True, show=False),
        Binding("ctrl+j", "newline", "Newline", priority=True, show=False),
        Binding("up", "history_prev", "Previous", priority=True, show=False),
        Binding("down", "history_next", "Next", priority=True, show=False),
    ]

    class Submitted(Message):
        """Posted when user presses Enter."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("tab_behavior", "indent")
        kwargs.setdefault("soft_wrap", True)
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index: int = -1  # -1 means not browsing history
        self._current_input: str = ""  # Saved input when browsing history
        self._autocomplete = None  # Set by TextAreaAutoComplete on mount

    def _on_key(self, event) -> None:
        """Intercept keys for autocomplete before normal processing."""
        if self._autocomplete and self._autocomplete.handle_key(event.key):
            event.prevent_default()
            event.stop()
            return
        super()._on_key(event)

    def action_submit(self) -> None:
        text = self.text.strip()
        if text:
            # Add to history (avoid duplicates of last entry)
            if not self._history or self._history[-1] != text:
                self._history.append(text)
        self._history_index = -1
        self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        self.insert("\n")

    def action_history_prev(self) -> None:
        """Go to previous command in history (only when cursor at top)."""
        if self.cursor_location[0] != 0:
            self.move_cursor_relative(rows=-1)
            return
        if not self._history:
            return
        if self._history_index == -1:
            self._current_input = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self.text = self._history[self._history_index]
        self.move_cursor(self.document.end)

    def action_history_next(self) -> None:
        """Go to next command in history (only when cursor at bottom)."""
        last_line = self.document.line_count - 1
        if self.cursor_location[0] != last_line:
            self.move_cursor_relative(rows=1)
            return
        if self._history_index == -1:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.text = self._history[self._history_index]
        else:
            self._history_index = -1
            self.text = self._current_input
        self.move_cursor(self.document.end)
