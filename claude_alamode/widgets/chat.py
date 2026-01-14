"""Chat widgets - messages, input, and thinking indicator."""

import pyperclip

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
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


class ImageAttachments(Horizontal):
    """Shows pending image attachments as removable tags."""

    class Removed(Message):
        """Posted when user removes an image."""
        def __init__(self, filename: str) -> None:
            self.filename = filename
            super().__init__()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._images: list[str] = []
        self._counter = 0  # Unique ID counter

    def add_image(self, filename: str) -> None:
        """Add an image tag."""
        self._images.append(filename)
        self._update_display()

    def remove_image(self, filename: str) -> None:
        """Remove a specific image."""
        if filename in self._images:
            self._images.remove(filename)
            self._update_display()
            self.post_message(self.Removed(filename))

    def clear(self) -> None:
        """Clear all images."""
        self._images.clear()
        self._update_display()

    def _update_display(self) -> None:
        # Remove existing buttons
        for child in list(self.children):
            child.remove()

        if self._images:
            screenshot_num = 0
            for name in self._images:
                self._counter += 1
                # Shorten screenshot names for display
                if name.lower().startswith("screenshot"):
                    screenshot_num += 1
                    display_name = f"Screenshot #{screenshot_num}"
                else:
                    display_name = name
                btn = Button(f"📎 {display_name} ×", id=f"img-{self._counter}", classes="image-tag")
                btn._image_name = name  # Store actual name for removal
                self.mount(btn)
            self.remove_class("hidden")
        else:
            self.add_class("hidden")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle click on image tag to remove it."""
        if hasattr(event.button, "_image_name"):
            self.remove_image(event.button._image_name)
            event.stop()


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

    # Supported image extensions for drag-and-drop
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("tab_behavior", "indent")
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index: int = -1
        self._current_input: str = ""
        self._autocomplete = None
        self._last_image_paste: tuple[str, float] | None = None  # (text, time) for dedup

    def _on_key(self, event) -> None:
        """Intercept keys for autocomplete before normal processing."""
        if self._autocomplete and self._autocomplete.handle_key(event.key):
            event.prevent_default()
            event.stop()
            return
        super()._on_key(event)

    def _is_image_path(self, text: str) -> list:
        """Check if text contains image file paths."""
        from pathlib import Path
        images = []
        for line in text.strip().splitlines():
            line = line.strip()
            if line.startswith("file://"):
                line = line[7:]
            # Unescape backslash-escaped spaces (terminal escaping)
            line = line.replace("\\ ", " ")
            path = Path(line)
            if path.exists() and path.suffix.lower() in self.IMAGE_EXTENSIONS:
                images.append(path)
        return images

    async def _on_paste(self, event) -> None:
        """Intercept paste - check for images BEFORE inserting text."""
        import time

        images = self._is_image_path(event.text)
        if images:
            # Deduplicate - terminals sometimes fire paste twice
            now = time.time()
            if self._last_image_paste and self._last_image_paste[0] == event.text:
                if now - self._last_image_paste[1] < 0.5:  # Within 500ms = duplicate
                    event.prevent_default()
                    event.stop()
                    return
            self._last_image_paste = (event.text, now)

            # Attach images
            for path in images:
                self.app._attach_image(path)
            event.prevent_default()
            event.stop()
            return
        # Normal paste
        await super()._on_paste(event)

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
