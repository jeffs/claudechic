"""Todo display widget for TodoWrite tool."""

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static


class TodoPanel(Static):
    """Sidebar panel for todo list - docked right when space allows."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.todos: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static("Tasks", classes="todo-title")

    def update_todos(self, todos: list[dict]) -> None:
        """Replace todos with new list."""
        self.todos = todos
        # Remove old items (keep title)
        for child in list(self.children):
            if isinstance(child, TodoItem):
                child.remove()
        # Add new items
        for todo in todos:
            self.mount(TodoItem(todo))


class TodoWidget(Static):
    """Inline display of todo list in chat stream."""

    def __init__(self, todos: list[dict]) -> None:
        super().__init__()
        self.todos = todos

    def compose(self) -> ComposeResult:
        for todo in self.todos:
            yield TodoItem(todo)

    def update_todos(self, todos: list[dict]) -> None:
        """Replace todos with new list."""
        self.todos = todos
        self.remove_children()
        for todo in todos:
            self.mount(TodoItem(todo))


class TodoItem(Static):
    """Single todo item with status icon."""

    ICONS = {"pending": "☐", "in_progress": "◉", "completed": "✓"}

    def __init__(self, todo: dict) -> None:
        super().__init__()
        self.todo = todo
        self.add_class(todo.get("status", "pending"))

    def render(self) -> Text:
        status = self.todo.get("status", "pending")
        icon = self.ICONS.get(status, "?")

        # Show activeForm for in_progress, content otherwise
        if status == "in_progress":
            label = self.todo.get("activeForm", self.todo.get("content", ""))
        else:
            label = self.todo.get("content", "")

        result = Text()
        if status == "completed":
            result.append(f"{icon} ", style="green")
            result.append(label, style="strike dim")
        elif status == "in_progress":
            result.append(f"{icon} ", style="yellow bold")
            result.append(label, style="yellow")
        else:  # pending
            result.append(f"{icon} ", style="dim")
            result.append(label, style="dim")

        return result
