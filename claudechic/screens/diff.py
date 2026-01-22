"""Diff review screen."""

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Static

from claudechic.features.diff import DiffSidebar, DiffView, get_changes
from claudechic.features.diff.widgets import DiffFileItem


class DiffScreen(Screen[None]):
    """Full-screen diff viewer for reviewing uncommitted changes."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
    ]

    DEFAULT_CSS = """
    DiffScreen {
        background: $background;
    }

    DiffScreen #diff-container {
        width: 100%;
        height: 100%;
    }

    DiffScreen #diff-empty {
        width: 100%;
        height: 100%;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def __init__(self, cwd: Path) -> None:
        super().__init__()
        self._cwd = cwd
        self._sidebar: DiffSidebar | None = None
        self._view: DiffView | None = None

    def compose(self) -> ComposeResult:
        # Placeholder - will be replaced with actual content on mount
        yield Static("Loading...", id="diff-empty")

    async def on_mount(self) -> None:
        """Fetch changes and build the diff view."""
        changes = await get_changes(str(self._cwd))

        # Remove placeholder
        self.query_one("#diff-empty").remove()

        if not changes:
            self.mount(Static("No uncommitted changes", id="diff-empty"))
            return

        # Build diff UI
        container = Horizontal(id="diff-container")
        self._sidebar = DiffSidebar(changes, id="diff-sidebar")
        self._view = DiffView(changes, id="diff-view")

        self.mount(container)
        container.mount(self._sidebar)
        container.mount(self._view)

        self._view.focus()

    def action_go_back(self) -> None:
        """Return to chat."""
        self.dismiss(None)

    def on_key(self, event) -> None:
        """Handle j/k for hunk navigation."""
        if event.key in ("j", "down"):
            if self._view:
                self._view.action_next_file()
            event.prevent_default()
            event.stop()
        elif event.key in ("k", "up"):
            if self._view:
                self._view.action_prev_file()
            event.prevent_default()
            event.stop()

    def on_diff_file_item_selected(self, event: DiffFileItem.Selected) -> None:
        """Handle programmatic file selection - update sidebar highlight."""
        if self._sidebar:
            self._sidebar.set_active(event.path)

    def on_diff_file_item_clicked(self, event: DiffFileItem.Clicked) -> None:
        """Handle user click on sidebar item - scroll to file."""
        if self._sidebar:
            self._sidebar.set_active(event.path)
        if self._view:
            self._view.scroll_to_file(event.path)
