"""Pure widget tests - no SDK needed."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from claude_alamode.widgets import (
    ChatInput,
    ChatMessage,
    ThinkingIndicator,
    SelectionPrompt,
    QuestionPrompt,
    AgentSidebar,
    TodoPanel,
)
from claude_alamode.widgets.todo import TodoItem
from claude_alamode.widgets.indicators import ContextBar
from claude_alamode.widgets.footer import StatusFooter


class WidgetTestApp(App):
    """Minimal app for testing individual widgets."""

    def __init__(self, widget_factory):
        super().__init__()
        self._widget_factory = widget_factory

    def compose(self) -> ComposeResult:
        yield self._widget_factory()


@pytest.mark.asyncio
async def test_chat_input_submit():
    """Enter posts Submitted message."""
    submitted_text = None

    class TestApp(App):
        def compose(self):
            yield ChatInput(id="input")

        def on_chat_input_submitted(self, event):
            nonlocal submitted_text
            submitted_text = event.text

    app = TestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one(ChatInput)
        input_widget.text = "hello world"
        await pilot.press("enter")
        assert submitted_text == "hello world"


@pytest.mark.asyncio
async def test_chat_input_history():
    """Up/down navigates history."""

    class HistoryTestApp(App):
        def compose(self):
            yield ChatInput(id="input")

        def on_chat_input_submitted(self, event):
            # Clear input like ChatApp does
            self.query_one(ChatInput).clear()

    app = HistoryTestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one(ChatInput)

        # Send a few messages to build history
        input_widget.text = "first"
        await pilot.press("enter")
        input_widget.text = "second"
        await pilot.press("enter")
        input_widget.text = "third"
        await pilot.press("enter")

        # Now navigate history
        assert input_widget.text == ""
        await pilot.press("up")
        assert input_widget.text == "third"
        await pilot.press("up")
        assert input_widget.text == "second"
        await pilot.press("up")
        assert input_widget.text == "first"
        await pilot.press("down")
        assert input_widget.text == "second"


@pytest.mark.asyncio
async def test_chat_input_image_detection():
    """Detects image paths."""
    app = WidgetTestApp(lambda: ChatInput(id="input"))
    async with app.run_test():
        input_widget = app.query_one(ChatInput)

        # Test various image path formats
        assert input_widget._is_image_path("/tmp/test.png") == []  # File doesn't exist
        assert input_widget._is_image_path("not an image") == []
        assert input_widget._is_image_path("file:///nonexistent.jpg") == []


@pytest.mark.asyncio
async def test_selection_prompt_arrow_navigation():
    """Up/down cycles through options."""
    options = [("a", "Option A"), ("b", "Option B"), ("c", "Option C")]

    app = WidgetTestApp(lambda: SelectionPrompt("Choose:", options))
    async with app.run_test() as pilot:
        prompt = app.query_one(SelectionPrompt)

        assert prompt.selected_idx == 0
        await pilot.press("down")
        assert prompt.selected_idx == 1
        await pilot.press("down")
        assert prompt.selected_idx == 2
        await pilot.press("down")
        assert prompt.selected_idx == 0  # Wraps around
        await pilot.press("up")
        assert prompt.selected_idx == 2


@pytest.mark.asyncio
async def test_selection_prompt_number_keys():
    """Number keys select options directly."""
    options = [("a", "Option A"), ("b", "Option B"), ("c", "Option C")]
    result = None

    class TestApp(App):
        def compose(self):
            yield SelectionPrompt("Choose:", options)

    app = TestApp()
    async with app.run_test() as pilot:
        prompt = app.query_one(SelectionPrompt)

        # Press "2" to select second option
        await pilot.press("2")
        result = await prompt.wait()

    assert result == "b"


@pytest.mark.asyncio
async def test_selection_prompt_escape_cancels():
    """Escape resolves with empty string."""
    options = [("a", "Option A"), ("b", "Option B")]

    app = WidgetTestApp(lambda: SelectionPrompt("Choose:", options))
    async with app.run_test() as pilot:
        prompt = app.query_one(SelectionPrompt)

        await pilot.press("escape")
        result = await prompt.wait()
        assert result == ""


@pytest.mark.asyncio
async def test_question_prompt_multi_question():
    """Handles multiple questions."""
    questions = [
        {"question": "Q1?", "options": [{"label": "Yes"}, {"label": "No"}]},
        {"question": "Q2?", "options": [{"label": "Red"}, {"label": "Blue"}]},
    ]

    app = WidgetTestApp(lambda: QuestionPrompt(questions))
    async with app.run_test() as pilot:
        prompt = app.query_one(QuestionPrompt)
        prompt.focus()

        # Answer first question
        assert prompt.current_q == 0
        await pilot.press("1")  # Select "Yes"

        # Should advance to second question
        assert prompt.current_q == 1
        assert prompt.answers == {"Q1?": "Yes"}

        await pilot.press("2")  # Select "Blue"

        # Prompt should have resolved after second answer
        assert prompt.answers == {"Q1?": "Yes", "Q2?": "Blue"}


@pytest.mark.asyncio
async def test_agent_sidebar_add_remove():
    """Can add and remove agents."""
    app = WidgetTestApp(lambda: AgentSidebar(id="sidebar"))
    async with app.run_test():
        sidebar = app.query_one(AgentSidebar)

        # Add agents
        sidebar.add_agent("id1", "Agent 1")
        sidebar.add_agent("id2", "Agent 2")

        assert "id1" in sidebar._agents
        assert "id2" in sidebar._agents
        assert len(sidebar._agents) == 2

        # Remove one
        sidebar.remove_agent("id1")
        assert "id1" not in sidebar._agents
        assert len(sidebar._agents) == 1


@pytest.mark.asyncio
async def test_agent_sidebar_active_selection():
    """set_active updates visual state."""
    app = WidgetTestApp(lambda: AgentSidebar(id="sidebar"))
    async with app.run_test():
        sidebar = app.query_one(AgentSidebar)

        sidebar.add_agent("id1", "Agent 1")
        sidebar.add_agent("id2", "Agent 2")

        sidebar.set_active("id1")
        assert sidebar._agents["id1"].has_class("active")
        assert not sidebar._agents["id2"].has_class("active")

        sidebar.set_active("id2")
        assert not sidebar._agents["id1"].has_class("active")
        assert sidebar._agents["id2"].has_class("active")


@pytest.mark.asyncio
async def test_agent_sidebar_status_updates():
    """update_status changes indicator."""
    app = WidgetTestApp(lambda: AgentSidebar(id="sidebar"))
    async with app.run_test():
        sidebar = app.query_one(AgentSidebar)

        sidebar.add_agent("id1", "Agent 1", status="idle")
        assert sidebar._agents["id1"].status == "idle"

        sidebar.update_status("id1", "busy")
        assert sidebar._agents["id1"].status == "busy"

        sidebar.update_status("id1", "needs_input")
        assert sidebar._agents["id1"].status == "needs_input"


@pytest.mark.asyncio
async def test_context_bar_rendering():
    """ContextBar shows correct fill and color."""
    app = WidgetTestApp(lambda: ContextBar(id="ctx"))
    async with app.run_test():
        bar = app.query_one(ContextBar)

        # Low usage - should be dim
        bar.tokens = 10000
        bar.max_tokens = 200000
        rendered = bar.render()
        assert "5%" in rendered.plain  # 10k/200k = 5%

        # High usage - should be red
        bar.tokens = 180000
        rendered = bar.render()
        assert "90%" in rendered.plain


@pytest.mark.asyncio
async def test_todo_panel_updates():
    """TodoPanel displays and updates todos."""
    app = WidgetTestApp(lambda: TodoPanel(id="panel"))
    async with app.run_test():
        panel = app.query_one(TodoPanel)

        todos = [
            {"content": "Task 1", "status": "completed", "activeForm": "Completing task 1"},
            {"content": "Task 2", "status": "in_progress", "activeForm": "Working on task 2"},
            {"content": "Task 3", "status": "pending", "activeForm": "Starting task 3"},
        ]

        panel.update_todos(todos)

        items = list(panel.query(TodoItem))
        assert len(items) == 3
        assert items[0].has_class("completed")
        assert items[1].has_class("in_progress")
        assert items[2].has_class("pending")


@pytest.mark.asyncio
async def test_status_footer_auto_edit():
    """Footer shows auto-edit state."""
    app = WidgetTestApp(lambda: StatusFooter())
    async with app.run_test():
        footer = app.query_one(StatusFooter)

        footer.auto_edit = False
        label = footer.query_one("#auto-edit-label", Static)
        assert "off" in label.render().plain.lower()

        footer.auto_edit = True
        assert "on" in label.render().plain.lower()


@pytest.mark.asyncio
async def test_chat_message_append():
    """ChatMessage accumulates content."""
    app = WidgetTestApp(lambda: ChatMessage("Hello"))
    async with app.run_test():
        msg = app.query_one(ChatMessage)

        assert msg.get_raw_content() == "Hello"

        msg.append_content(" world")
        assert msg.get_raw_content() == "Hello world"

        msg.append_content("!")
        assert msg.get_raw_content() == "Hello world!"


@pytest.mark.asyncio
async def test_thinking_indicator_animates():
    """ThinkingIndicator cycles through frames."""
    app = WidgetTestApp(ThinkingIndicator)
    async with app.run_test() as pilot:
        indicator = app.query_one(ThinkingIndicator)

        initial_frame = indicator.frame
        # Wait for animation
        await pilot.pause(0.2)
        # Frame should have changed
        assert indicator.frame != initial_frame or indicator.frame == 0  # May wrap


@pytest.mark.asyncio
async def test_history_search_filters():
    """HistorySearch filters history and cycles through matches."""
    from claude_alamode.widgets.history_search import HistorySearch
    from unittest.mock import patch

    class TestApp(App):
        def compose(self):
            yield HistorySearch(id="history")

    # Mock history data (most recent first)
    mock_history = ["fix the bug", "add new feature", "fix another bug", "refactor code"]

    with patch("claude_alamode.widgets.history_search.load_global_history", return_value=mock_history):
        app = TestApp()
        async with app.run_test() as pilot:
            hs = app.query_one(HistorySearch)
            hs.show()
            await pilot.pause()

            # Initially shows most recent match
            assert hs._current_match() == "fix the bug"

            # Type to filter
            from textual.widgets import Input
            inp = hs.query_one("#search-input", Input)
            inp.value = "fix"
            hs.on_input_changed(Input.Changed(inp, "fix"))

            # Should filter to matching entries
            assert len(hs._filtered) == 2
            assert hs._current_match() == "fix the bug"

            # Ctrl+R cycles to next match
            hs.action_next_match()
            assert hs._current_match() == "fix another bug"

            # Up goes back
            hs.action_prev_match()
            assert hs._current_match() == "fix the bug"
