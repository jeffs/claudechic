"""App-level UI tests without SDK dependency."""

import pytest

from claude_alamode.app import ChatApp
from claude_alamode.widgets import ChatInput, ChatMessage, AgentSidebar, TodoPanel
from claude_alamode.widgets.footer import StatusFooter
from claude_alamode.messages import StreamChunk, ResponseComplete, ToolUseMessage, ToolResultMessage
from claude_agent_sdk import ToolUseBlock, ToolResultBlock
from tests.conftest import wait_for_workers


@pytest.mark.asyncio
async def test_app_mounts_basic_widgets(mock_sdk):
    """App mounts all expected widgets on startup."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Check key widgets exist
        assert app.query_one("#input", ChatInput)
        assert app.query_one("#agent-sidebar", AgentSidebar)
        assert app.query_one("#todo-panel", TodoPanel)
        assert app.query_one(StatusFooter)


@pytest.mark.asyncio
async def test_auto_edit_toggle(mock_sdk):
    """Shift+Tab toggles auto-edit mode for current agent."""
    app = ChatApp()
    async with app.run_test() as pilot:
        assert not app._agent.auto_approve_edits

        await pilot.press("shift+tab")
        assert app._agent.auto_approve_edits

        await pilot.press("shift+tab")
        assert not app._agent.auto_approve_edits


@pytest.mark.asyncio
async def test_auto_edit_footer_updates(mock_sdk):
    """Footer reflects auto-edit state."""
    app = ChatApp()
    async with app.run_test() as pilot:
        footer = app.query_one(StatusFooter)
        assert not footer.auto_edit

        await pilot.press("shift+tab")
        assert footer.auto_edit


@pytest.mark.asyncio
async def test_clear_command(mock_sdk):
    """'/clear' removes chat messages."""
    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view

        # Add some fake messages
        msg1 = ChatMessage("Test 1")
        msg2 = ChatMessage("Test 2")
        chat_view.mount(msg1)
        chat_view.mount(msg2)
        await pilot.pause()

        assert len(chat_view.children) == 2

        # Send /clear (which clears UI and sends to SDK)
        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/clear"
        await pilot.press("enter")
        await wait_for_workers(app)  # Give time for async operations

        # Chat view should be empty (the ErrorMessage from SDK is also cleared)
        # Actually /clear removes children THEN runs SDK - so any error appears after
        # Just check we cleared successfully initially
        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 0  # Our messages were cleared


@pytest.mark.asyncio
async def test_agent_list_command(mock_sdk):
    """'/agent' lists agents."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Should have one default agent
        assert len(app.agents) == 1

        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/agent"
        await pilot.press("enter")

        # The command shows notifications - just verify we have one agent
        assert len(app.agents) == 1


@pytest.mark.asyncio
async def test_agent_create_command(mock_sdk):
    """'/agent foo' creates new agent."""
    app = ChatApp()
    async with app.run_test() as pilot:
        assert len(app.agents) == 1

        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/agent test-agent"
        await pilot.press("enter")
        await wait_for_workers(app)

        assert len(app.agents) == 2
        agent_names = [a.name for a in app.agents.values()]
        assert "test-agent" in agent_names


@pytest.mark.asyncio
async def test_agent_switch_keybinding(mock_sdk):
    """Ctrl+1-9 switches agents."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Create second agent
        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/agent second"
        await pilot.press("enter")
        await wait_for_workers(app)

        assert len(app.agents) == 2
        agent_ids = list(app.agents.keys())

        # Should be on second agent now (just created)
        assert app.active_agent_id == agent_ids[1]

        # Switch to first agent with ctrl+1
        await pilot.press("ctrl+1")
        assert app.active_agent_id == agent_ids[0]

        # Switch to second agent with ctrl+2
        await pilot.press("ctrl+2")
        assert app.active_agent_id == agent_ids[1]


@pytest.mark.asyncio
async def test_agent_close_command(mock_sdk):
    """'/agent close' closes current agent."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Create second agent first
        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/agent to-close"
        await pilot.press("enter")
        await wait_for_workers(app)

        assert len(app.agents) == 2
        assert any(a.name == "to-close" for a in app.agents.values())

        # Close current agent
        input_widget.text = "/agent close"
        await pilot.press("enter")
        await wait_for_workers(app)

        # Should be back to one agent
        assert len(app.agents) == 1


@pytest.mark.asyncio
async def test_cannot_close_last_agent(mock_sdk):
    """Cannot close the last remaining agent."""
    app = ChatApp()
    async with app.run_test() as pilot:
        assert len(app.agents) == 1

        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/agent close"
        await pilot.press("enter")
        await wait_for_workers(app)

        # Still have one agent
        assert len(app.agents) == 1


@pytest.mark.asyncio
async def test_sidebar_agent_selection(mock_sdk):
    """Clicking agent in sidebar switches to it."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Create second agent
        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/agent sidebar-test"
        await pilot.press("enter")
        await wait_for_workers(app)

        sidebar = app.query_one("#agent-sidebar", AgentSidebar)
        agent_ids = list(app.agents.keys())

        # Second agent should be active (just created)
        assert app.active_agent_id == agent_ids[1]

        # Simulate clicking first agent
        first_agent_widget = sidebar._agents[agent_ids[0]]
        first_agent_widget.post_message(first_agent_widget.Selected(agent_ids[0]))
        await pilot.pause()

        # First agent should now be active
        assert app.active_agent_id == agent_ids[0]


@pytest.mark.asyncio
async def test_resume_shows_session_picker(mock_sdk):
    """'/resume' shows session picker."""
    app = ChatApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/resume"
        await pilot.press("enter")

        # Session picker should be visible
        assert app._session_picker_active


@pytest.mark.asyncio
async def test_escape_hides_session_picker(mock_sdk):
    """Escape hides session picker."""
    app = ChatApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/resume"
        await pilot.press("enter")

        assert app._session_picker_active

        # Need to refocus app to receive escape (session picker may have focus)
        app.screen.focus_next()
        await pilot.pause()
        app.action_escape()
        await pilot.pause()

        assert not app._session_picker_active


@pytest.mark.asyncio
async def test_double_ctrl_c_quits(mock_sdk):
    """Double Ctrl+C quits app."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # First Ctrl+C shows warning
        await pilot.press("ctrl+c")
        assert hasattr(app, "_last_quit_time")

        # Second quick Ctrl+C would exit (but we can't test actual exit easily)
        # Just verify the mechanism exists
        import time
        assert time.time() - app._last_quit_time < 2.0


@pytest.mark.asyncio
async def test_stream_chunk_creates_message(mock_sdk):
    """StreamChunk message creates ChatMessage widget."""
    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view

        # Post a stream chunk
        agent_id = app.active_agent_id
        app.post_message(StreamChunk("Hello ", new_message=True, agent_id=agent_id))
        await pilot.pause()

        # Should have created a ChatMessage
        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 1
        assert messages[0].get_raw_content() == "Hello "


@pytest.mark.asyncio
async def test_stream_chunk_appends_to_message(mock_sdk):
    """Sequential StreamChunks append to same message."""
    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        agent_id = app.active_agent_id

        app.post_message(StreamChunk("Hello ", new_message=True, agent_id=agent_id))
        await pilot.pause()
        app.post_message(StreamChunk("world!", new_message=False, agent_id=agent_id))
        await pilot.pause()

        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 1
        assert messages[0].get_raw_content() == "Hello world!"


@pytest.mark.asyncio
async def test_stream_chunks_interleaved_with_tools(mock_sdk):
    """Text after tool use creates a new ChatMessage (not appended to first)."""
    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        agent_id = app.active_agent_id

        # First text chunk
        app.post_message(StreamChunk("Planning...", new_message=True, agent_id=agent_id))
        await pilot.pause()

        # Tool use
        tool_block = ToolUseBlock(id="tool-1", name="Read", input={"file_path": "/test.py"})
        app.post_message(ToolUseMessage(tool_block, agent_id=agent_id))
        await pilot.pause()

        # Tool result
        result_block = ToolResultBlock(tool_use_id="tool-1", content="file contents", is_error=False)
        app.post_message(ToolResultMessage(result_block, agent_id=agent_id))
        await pilot.pause()

        # Second text chunk (should be new_message=True after tool)
        app.post_message(StreamChunk("Done!", new_message=True, agent_id=agent_id))
        await pilot.pause()

        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 2, f"Expected 2 messages, got {len(messages)}"
        assert messages[0].get_raw_content() == "Planning..."
        assert messages[1].get_raw_content() == "Done!"


@pytest.mark.asyncio
async def test_response_complete_enables_input(mock_sdk):
    """ResponseComplete focuses input."""
    app = ChatApp()
    async with app.run_test() as pilot:
        agent_id = app.active_agent_id
        app.post_message(ResponseComplete(None, agent_id=agent_id))
        await pilot.pause()

        input_widget = app.query_one("#input", ChatInput)
        assert app.focused == input_widget


@pytest.mark.asyncio
async def test_sidebar_hidden_when_single_agent(mock_sdk):
    """Right sidebar hidden with single agent and no todos."""
    app = ChatApp()
    async with app.run_test(size=(100, 40)) as pilot:
        sidebar = app.query_one("#right-sidebar")
        # With single agent and no todos, sidebar should be hidden
        assert sidebar.has_class("hidden")


@pytest.mark.asyncio
async def test_sidebar_shows_with_multiple_agents(mock_sdk):
    """Right sidebar shows with multiple agents when wide enough."""
    app = ChatApp()
    async with app.run_test(size=(160, 40)) as pilot:
        # Create second agent
        input_widget = app.query_one("#input", ChatInput)
        input_widget.text = "/agent second"
        await pilot.press("enter")
        await wait_for_workers(app)

        # Trigger resize handling
        app._position_right_sidebar()

        sidebar = app.query_one("#right-sidebar")
        # With multiple agents and wide enough, sidebar should show
        assert not sidebar.has_class("hidden")
