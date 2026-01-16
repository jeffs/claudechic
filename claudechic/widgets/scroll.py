"""Auto-hiding scrollbar container."""

from textual.containers import VerticalScroll


class AutoHideScroll(VerticalScroll):
    """VerticalScroll with always-visible scrollbar and smart tailing.

    Previously auto-hid after inactivity, but layout shifts caused rendering issues.
    Tracks whether user is at bottom to enable/disable auto-scroll on new content.
    """

    DEFAULT_CSS = """
    AutoHideScroll {
        scrollbar-size-vertical: 1;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tailing = True  # Whether to auto-scroll on new content

    def action_scroll_up(self) -> None:
        """User scrolled up - disable tailing."""
        self._tailing = False
        super().action_scroll_up()

    def action_scroll_down(self) -> None:
        """User scrolled down - check if at bottom."""
        super().action_scroll_down()
        self._tailing = self.scroll_y >= self.max_scroll_y - 50

    def action_page_up(self) -> None:
        """User paged up - disable tailing."""
        self._tailing = False
        super().action_page_up()

    def action_page_down(self) -> None:
        """User paged down - check if at bottom."""
        super().action_page_down()
        self._tailing = self.scroll_y >= self.max_scroll_y - 50

    def _on_mouse_scroll_up(self, event) -> None:
        """Mouse wheel up - disable tailing."""
        self._tailing = False
        super()._on_mouse_scroll_up(event)

    def _on_mouse_scroll_down(self, event) -> None:
        """Mouse wheel down - check if at bottom."""
        super()._on_mouse_scroll_down(event)
        self._tailing = self.scroll_y >= self.max_scroll_y - 50
