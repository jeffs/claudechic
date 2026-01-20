"""Chat widgets - messages, input, and thinking indicator."""

import re
import time
from pathlib import Path

import pyperclip

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Markdown, TextArea, Static, Button

from claudechic.errors import log_exception
from claudechic.profiling import profile, timed


class Spinner(Static):
    """Animated spinner - all instances share a single timer for efficiency."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    DEFAULT_CSS = """
    Spinner {
        width: 1;
        height: 1;
        color: $text-muted;
    }
    """

    # Class-level shared state
    _instances: set["Spinner"] = set()
    _frame: int = 0
    _timer = None

    def __init__(self, text: str = "") -> None:
        self._text = f" {text}" if text else ""
        super().__init__()

    def render(self) -> str:
        """Return current frame from shared counter."""
        return f"{self.FRAMES[Spinner._frame]}{self._text}"

    def on_mount(self) -> None:
        Spinner._instances.add(self)
        # Start shared timer if this is the first spinner
        # Use app.set_interval so timer survives widget unmount
        if Spinner._timer is None:
            Spinner._timer = self.app.set_interval(1 / 10, Spinner._tick_all)  # 10 FPS

    def on_unmount(self) -> None:
        Spinner._instances.discard(self)
        # Stop timer if no spinners left
        if not Spinner._instances and Spinner._timer is not None:
            Spinner._timer.stop()
            Spinner._timer = None

    def _is_visible(self) -> bool:
        """Check if spinner and all ancestors are visible."""
        if self.has_class("hidden") or not self.display or not self.is_attached:
            return False
        node = self.parent
        while node is not None:
            if node.has_class("hidden"):
                return False
            node = node.parent
        return True

    @staticmethod
    @profile
    def _tick_all() -> None:
        """Advance frame and refresh only visible spinners."""
        Spinner._frame = (Spinner._frame + 1) % len(Spinner.FRAMES)
        with timed("Spinner._tick_all.refresh"):
            for spinner in list(Spinner._instances):
                if spinner._is_visible():
                    spinner.refresh()


class ThinkingIndicator(Spinner):
    """Animated spinner shown when Claude is thinking."""

    can_focus = False
    DEFAULT_CSS = """
    ThinkingIndicator {
        width: auto;
        height: 1;
    }
    """

    def __init__(self, id: str | None = None, classes: str | None = None) -> None:
        super().__init__("Thinking...")
        if id:
            self.id = id
        if classes:
            self.set_classes(classes)


class ErrorMessage(Static):
    """Error message displayed in the chat view with red styling."""

    can_focus = False

    def __init__(self, message: str, exception: Exception | None = None) -> None:
        super().__init__()
        self._message = message
        self._exception = exception
        # Log the exception if provided
        if exception:
            log_exception(exception, message)

    def compose(self) -> ComposeResult:
        display = f"**Error:** {self._message}"
        if self._exception:
            display += f"\n\n`{type(self._exception).__name__}: {self._exception}`"
        yield Markdown(display, id="content")


class SystemInfo(Static):
    """System info message displayed in chat (not stored in history)."""

    can_focus = False

    def __init__(self, message: str, severity: str = "info") -> None:
        super().__init__(classes=f"system-{severity}")
        self._message = message

    def compose(self) -> ComposeResult:
        yield Markdown(self._message, id="content")


class ChatMessage(Static):
    """A single chat message with copy button."""

    can_focus = False
    DEBOUNCE_MS = 50  # Batch updates every 50ms

    def __init__(self, content: str = "", is_agent: bool = False) -> None:
        super().__init__()
        self._content = content.rstrip()
        self._debounce_timer = None
        self._is_agent = is_agent

    def compose(self) -> ComposeResult:
        yield Button("⧉", id="copy-btn", classes="copy-btn")
        if self._is_agent:
            # Wrap in container for nested border effect
            with Vertical(id="agent-inner"):
                yield Markdown(self._content, id="content")
        else:
            yield Markdown(self._content, id="content")

    def append_content(self, text: str) -> None:
        """Append text to message content (debounced)."""
        self._content += text
        if self._debounce_timer is None:
            self._debounce_timer = self.set_timer(
                self.DEBOUNCE_MS / 1000, self._flush_content
            )

    @profile
    def _flush_content(self) -> None:
        """Flush pending content to the Markdown widget."""
        self._debounce_timer = None
        try:
            with timed("ChatMessage._flush.query"):
                md = self.query_one("#content", Markdown)
            with timed("ChatMessage._flush.update"):
                md.update(self._content.rstrip())
        except Exception:
            pass  # Widget not mounted yet

    def flush(self) -> None:
        """Force flush any pending content (call on stream complete)."""
        if self._debounce_timer:
            self._debounce_timer.stop()
            self._debounce_timer = None
        self._flush_content()

    def get_raw_content(self) -> str:
        """Get raw content for copying."""
        return self._content

    def on_mouse_move(self) -> None:
        if not self.has_class("hovered"):
            self.add_class("hovered")

    def on_leave(self) -> None:
        self.remove_class("hovered")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-btn":
            try:
                pyperclip.copy(self.get_raw_content())
                self.app.notify("Copied to clipboard")
            except Exception as e:
                self.app.notify(f"Copy failed: {e}", severity="error")


class ChatAttachment(Button):
    """Clickable attachment tag in chat messages - opens file on click."""

    def __init__(self, path: str, display_name: str) -> None:
        super().__init__(f"📎 {display_name}", classes="chat-attachment")
        self._path = path

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Open the file when clicked."""
        import subprocess
        import sys
        event.stop()
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", self._path], check=True)
            elif sys.platform == "win32":
                subprocess.run(["start", self._path], shell=True, check=True)
            else:
                subprocess.run(["xdg-open", self._path], check=True)
        except Exception as e:
            self.app.notify(f"Failed to open: {e}", severity="error")


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
                btn._image_name = name  # type: ignore[attr-defined]  # Store actual name for removal
                self.mount(btn)
            self.remove_class("hidden")
        else:
            self.add_class("hidden")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle click on image tag to remove it."""
        if hasattr(event.button, "_image_name"):
            self.remove_image(event.button._image_name)  # type: ignore[attr-defined]
            event.stop()


class ChatInput(TextArea):
    """Text input that submits on Enter, newline on Shift+Enter, history with Up/Down."""

    BINDINGS = [
        Binding("enter", "submit", "Send", priority=True, show=False),
        Binding("ctrl+j", "newline", "Newline", priority=True, show=False),
        Binding("up", "history_prev", "Previous", priority=True, show=False),
        Binding("down", "history_next", "Next", priority=True, show=False),
        Binding("ctrl+a", "select_all", "Select all", priority=True, show=False),
        Binding("alt+backspace", "delete_word_left", "Delete word", priority=True, show=False),
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

    async def _on_key(self, event) -> None:  # type: ignore[override]
        """Intercept keys for autocomplete before normal processing."""
        if self._autocomplete and self._autocomplete.handle_key(event.key):
            event.prevent_default()
            event.stop()
            return
        await super()._on_key(event)

    def _is_image_path(self, text: str) -> list:
        """Check if text contains image file paths."""
        images = []
        text = text.strip()

        # Handle file:// URLs (newline or space separated)
        if text.startswith("file://"):
            for part in text.split():
                if part.startswith("file://"):
                    part = part[7:]
                path = Path(part)
                if path.exists() and path.suffix.lower() in self.IMAGE_EXTENSIONS:
                    images.append(path)
            return images

        # Handle shell-escaped paths (backslash-escaped spaces)
        # Split on unescaped spaces (space not preceded by backslash)
        if "\\ " in text:
            parts = re.split(r'(?<!\\) ', text)
            for part in parts:
                part = part.replace("\\ ", " ")
                path = Path(part)
                if path.exists() and path.suffix.lower() in self.IMAGE_EXTENSIONS:
                    images.append(path)
            return images

        # Simple case: one path per line or single path
        for line in text.splitlines():
            line = line.strip()
            path = Path(line)
            if path.exists() and path.suffix.lower() in self.IMAGE_EXTENSIONS:
                images.append(path)
        return images

    def on_paste(self, event) -> None:
        """Intercept paste - check for images BEFORE inserting text."""
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
                self.app._attach_image(path)  # type: ignore[attr-defined]
            event.prevent_default()
            event.stop()
            return
        # Wrap multi-line pastes in triple backticks for markdown formatting
        if "\n" in event.text:
            wrapped = f"```\n{event.text}\n```"
            self.insert(wrapped)
            event.prevent_default()
            event.stop()
            return
        # Normal paste - let parent handle it

    def action_submit(self) -> None:
        # If autocomplete is showing, complete instead of submit
        if self._autocomplete and self._autocomplete.display:
            self._autocomplete.handle_key("enter")
            return
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
        """Go to previous command in history (only when cursor at top visual row)."""
        # If autocomplete is visible, navigate it instead
        if self._autocomplete and self._autocomplete.display:
            self._autocomplete.handle_key("up")
            return
        # Check if we're at the top visual row (considering soft wrap)
        visual_offset = self.wrapped_document.location_to_offset(self.cursor_location)
        if visual_offset.y > 0:
            # Not at top - use built-in wrap-aware cursor movement
            self.action_cursor_up()
            return
        if not self._history:
            return
        if self._history_index == -1:
            self._current_input = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        # Suppress autocomplete BEFORE setting text to prevent timer start
        if self._autocomplete:
            self._autocomplete.suppress()
        self.text = self._history[self._history_index]
        self.move_cursor(self.document.end)

    def action_history_next(self) -> None:
        """Go to next command in history (only when cursor at bottom visual row)."""
        # If autocomplete is visible, navigate it instead
        if self._autocomplete and self._autocomplete.display:
            self._autocomplete.handle_key("down")
            return
        # Check if we're at the bottom visual row (considering soft wrap)
        visual_offset = self.wrapped_document.location_to_offset(self.cursor_location)
        total_visual_rows = self.wrapped_document.height
        if visual_offset.y < total_visual_rows - 1:
            # Not at bottom - use built-in wrap-aware cursor movement
            self.action_cursor_down()
            return
        if self._history_index == -1:
            return
        # Suppress autocomplete BEFORE setting text to prevent timer start
        if self._autocomplete:
            self._autocomplete.suppress()
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.text = self._history[self._history_index]
        else:
            self._history_index = -1
            self.text = self._current_input
        self.move_cursor(self.document.end)
