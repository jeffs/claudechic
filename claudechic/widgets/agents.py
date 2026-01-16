"""Agent sidebar widget for multi-agent management."""

from pathlib import Path

from textual.app import ComposeResult
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static
from rich.text import Text

class WorktreeItem(Widget):
    """A ghost worktree in the sidebar (not yet an agent)."""

    class Selected(Message):
        """Posted when worktree is clicked."""
        def __init__(self, branch: str, path: Path) -> None:
            self.branch = branch
            self.path = path
            super().__init__()

    DEFAULT_CSS = """
    WorktreeItem {
        height: 3;
        padding: 1 1 1 2;
        layout: horizontal;
    }
    WorktreeItem:hover {
        background: $surface-lighten-1;
    }
    WorktreeItem .worktree-label {
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
        color: $text-muted;
    }
    """

    def __init__(self, branch: str, path: Path) -> None:
        super().__init__()
        self.branch = branch
        self.path = path

    def compose(self) -> ComposeResult:
        name = self.branch
        if len(name) > 16:
            name = name[:15] + "…"
        label = Text.assemble(("◌", ""), " ", (name, "dim"))
        yield Static(label, classes="worktree-label")

    def on_click(self) -> None:
        self.post_message(self.Selected(self.branch, self.path))


class AgentItem(Widget):
    """A single agent in the sidebar."""

    class Selected(Message):
        """Posted when agent is clicked."""
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            super().__init__()

    class CloseRequested(Message):
        """Posted when close button is clicked."""
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            super().__init__()

    DEFAULT_CSS = """
    AgentItem {
        height: 3;
        padding: 1 1 1 2;
        layout: horizontal;
    }
    AgentItem:hover {
        background: $surface-lighten-1;
    }
    AgentItem.active {
        padding: 1 1 1 1;
        border-left: wide $primary;
        background: $surface;
    }
    AgentItem .agent-label {
        width: 1fr;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    AgentItem .agent-close {
        width: 3;
        min-width: 3;
        height: 1;
        padding: 0;
        background: $panel;
        color: $text-muted;
        text-align: center;
    }
    AgentItem .agent-close:hover {
        color: $error;
        background: $panel-lighten-1;
    }
    """

    status = reactive("idle")

    def __init__(self, agent_id: str, display_name: str, status: str = "idle") -> None:
        super().__init__()
        self.agent_id = agent_id
        self.display_name = display_name
        self.status = status

    def compose(self) -> ComposeResult:
        yield Static(self._render_label(), classes="agent-label")
        yield Static(Text("X"), classes="agent-close")

    def _render_label(self) -> Text:
        if self.status == "busy":
            indicator = "\u25cf"
            style = ""  # default text color
        elif self.status == "needs_input":
            indicator = "\u25cf"
            style = self.app.current_theme.primary if self.app else "bold"
        else:
            indicator = "\u25cb"
            style = "dim"
        name = self.display_name
        if len(name) > 14:
            name = name[:13] + "…"
        return Text.assemble((indicator, style), " ", (name, ""))

    def watch_status(self, _status: str) -> None:
        """Update label when status changes."""
        try:
            label = self.query_one(".agent-label", Static)
            label.update(self._render_label())
        except Exception:
            pass  # Widget may not be mounted yet

    def on_click(self, event: Click) -> None:
        """Handle clicks - check if on close button."""
        if event.widget and event.widget.has_class("agent-close"):
            event.stop()
            self.post_message(self.CloseRequested(self.agent_id))
        else:
            self.post_message(self.Selected(self.agent_id))


class AgentSidebar(Widget):
    """Sidebar showing all agents with status indicators."""

    DEFAULT_CSS = """
    AgentSidebar {
        width: 24;
        height: auto;
        max-height: 50%;
        padding: 0;
    }
    AgentSidebar .sidebar-title {
        color: $text-muted;
        text-style: bold;
        padding: 1 1 1 1;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._agents: dict[str, AgentItem] = {}
        self._worktrees: dict[str, WorktreeItem] = {}  # branch -> item

    def compose(self) -> ComposeResult:
        yield Static("Agents", classes="sidebar-title")

    def add_agent(self, agent_id: str, name: str, status: str = "idle") -> None:
        """Add an agent to the sidebar."""
        if agent_id in self._agents:
            return
        # Remove ghost worktree if there's one for this name
        if name in self._worktrees:
            self._worktrees[name].remove()
            del self._worktrees[name]
        item = AgentItem(agent_id, name, status)
        item.id = f"agent-{agent_id}"
        self._agents[agent_id] = item
        self.mount(item)

    def remove_agent(self, agent_id: str) -> None:
        """Remove an agent from the sidebar."""
        if agent_id in self._agents:
            self._agents[agent_id].remove()
            del self._agents[agent_id]

    def set_active(self, agent_id: str) -> None:
        """Mark an agent as active (selected)."""
        for aid, item in self._agents.items():
            if aid == agent_id:
                item.add_class("active")
            else:
                item.remove_class("active")

    def update_status(self, agent_id: str, status: str) -> None:
        """Update an agent's status."""
        if agent_id in self._agents:
            self._agents[agent_id].status = status

    def add_worktree(self, branch: str, path: Path) -> None:
        """Add a ghost worktree to the sidebar."""
        # Skip if already have an agent with this name
        for agent_item in self._agents.values():
            if agent_item.display_name == branch:
                return
        if branch in self._worktrees:
            return
        item = WorktreeItem(branch, path)
        item.id = f"worktree-{branch}"
        self._worktrees[branch] = item
        self.mount(item)

    def remove_worktree(self, branch: str) -> None:
        """Remove a ghost worktree from the sidebar."""
        if branch in self._worktrees:
            self._worktrees[branch].remove()
            del self._worktrees[branch]
