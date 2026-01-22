"""Session browser screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import ListView, Input, Static

from claudechic.widgets.layout.sidebar import SessionItem


class SessionScreen(Screen[str | None]):
    """Full-screen session browser for resuming sessions."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
    ]

    DEFAULT_CSS = """
    SessionScreen {
        background: $background;
        align: center top;
    }

    SessionScreen #session-container {
        width: 100%;
        max-width: 80;
        height: 100%;
        padding: 1 2;
    }

    SessionScreen #session-title {
        height: 1;
        margin-bottom: 1;
        text-style: bold;
    }

    SessionScreen #session-search {
        height: 3;
        margin-bottom: 1;
    }

    SessionScreen #session-list {
        height: 1fr;
    }

    SessionScreen #session-list > SessionItem {
        padding: 0 0 0 1;
        height: auto;
        margin: 0 0 1 0;
        border-left: tall $panel;
    }

    SessionScreen #session-list > SessionItem:hover,
    SessionScreen #session-list > SessionItem.-highlight {
        background: $surface-darken-1;
        border-left: tall $primary;
    }

    SessionScreen .session-meta {
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="session-container"):
            yield Static("Resume Session", id="session-title")
            yield Input(placeholder="Search sessions...", id="session-search")
            yield ListView(id="session-list")

    def on_mount(self) -> None:
        self._update_list("")
        self.query_one("#session-search", Input).focus()

    def action_go_back(self) -> None:
        """Return to chat without selecting a session."""
        self.dismiss(None)

    async def _fetch_sessions(self, search: str) -> list[tuple[str, str, float, int]]:
        from claudechic.sessions import get_recent_sessions

        return await get_recent_sessions(search=search)

    def _update_list(self, search: str) -> None:
        self.run_worker(self._do_update(search))

    async def _do_update(self, search: str) -> None:
        sessions = await self._fetch_sessions(search)
        list_view = self.query_one("#session-list", ListView)
        list_view.clear()
        for session_id, title, mtime, msg_count in sessions:
            list_view.append(SessionItem(session_id, title, mtime, msg_count))
        if sessions:
            list_view.index = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "session-search":
            self._update_list(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            self.dismiss(event.item.session_id)
