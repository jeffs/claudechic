"""Custom Collapsible that doesn't auto-scroll on toggle."""

from textual.widgets import Collapsible

from claudechic.widgets.base.cursor import ClickableMixin


class QuietCollapsible(Collapsible, ClickableMixin):
    """Collapsible that doesn't scroll itself into view on toggle.

    The default Textual Collapsible calls scroll_visible() whenever it's
    toggled, which causes scroll jumping in chat interfaces where we want
    to control scrolling ourselves (e.g., tail-following new content).

    Also shows pointer cursor to indicate clickability.
    """

    def _watch_collapsed(self, collapsed: bool) -> None:
        """Update collapsed state without auto-scrolling."""
        self._update_collapsed(collapsed)
        # Post the appropriate message
        if collapsed:
            self.post_message(self.Collapsed(self))
        else:
            self.post_message(self.Expanded(self))
        # NOTE: We deliberately omit the scroll_visible() call that
        # the parent class makes here. Scrolling is handled by ChatView.
