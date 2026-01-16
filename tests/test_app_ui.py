"""App-level UI tests without SDK dependency."""

import pytest

from claudechic.app import ChatApp
from claudechic.widgets import ChatInput, ChatMessage, AgentSidebar, TodoPanel
from claudechic.widgets.footer import StatusFooter
from claudechic.messages import StreamChunk, ResponseComplete, ToolUseMessage, ToolResultMessage
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
        assert app._agent is not None
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
        assert chat_view is not None

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
        assert chat_view is not None

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
        assert chat_view is not None
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
        assert chat_view is not None
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


@pytest.mark.asyncio
async def test_command_output_displays(mock_sdk):
    """CommandOutputMessage displays content in chat."""
    from claudechic.messages import CommandOutputMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Post a command output message
        agent_id = app.active_agent_id
        app.post_message(CommandOutputMessage("## Test Output\n\nSome content", agent_id=agent_id))
        await pilot.pause()

        # Should have created a ChatMessage with system-message class
        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 1
        assert "## Test Output" in messages[0].get_raw_content()
        assert messages[0].has_class("system-message")


@pytest.mark.asyncio
async def test_context_report_displays(mock_sdk):
    """Context command output displays as ContextReport widget."""
    from claudechic.messages import CommandOutputMessage
    from claudechic.widgets.context_report import ContextReport

    CONTEXT_OUTPUT = """## Context Usage

**Model:** claude-opus-4-5-20251101
**Tokens:** 81.0k / 200.0k (41%)

### Categories

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 3.0k | 1.5% |
| Messages | 58.8k | 29.4% |
| Free space | 74.0k | 36.9% |
"""

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        agent_id = app.active_agent_id
        app.post_message(CommandOutputMessage(CONTEXT_OUTPUT, agent_id=agent_id))
        await pilot.pause()

        # Should have created a ContextReport, not ChatMessage
        reports = list(chat_view.query(ContextReport))
        assert len(reports) == 1

        # Verify data was parsed
        assert reports[0].data["model"] == "claude-opus-4-5-20251101"
        assert reports[0].data["tokens_used"] == 81000


@pytest.mark.asyncio
async def test_system_notification_shows_in_chat(mock_sdk):
    """SystemNotification creates SystemInfo widget in chat."""
    from claudechic.messages import SystemNotification
    from claudechic.widgets import SystemInfo
    from claude_agent_sdk import SystemMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Create a system message (simulating SDK)
        sdk_msg = SystemMessage(
            subtype="test_notification",
            data={"content": "Test system message", "level": "info"}
        )

        # Post the notification
        app.post_message(SystemNotification(sdk_msg, agent_id=app.active_agent_id))
        await pilot.pause()

        # Should have a SystemInfo widget in chat
        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 1
        assert info_widgets[0]._message == "Test system message"


@pytest.mark.asyncio
async def test_system_notification_api_error(mock_sdk):
    """API error notification displays correctly."""
    from claudechic.messages import SystemNotification
    from claudechic.widgets import SystemInfo
    from claude_agent_sdk import SystemMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Create an api_error system message
        sdk_msg = SystemMessage(
            subtype="api_error",
            data={
                "level": "error",
                "error": {"error": {"message": "Rate limited"}},
                "retryAttempt": 2,
                "maxRetries": 10
            }
        )

        app.post_message(SystemNotification(sdk_msg, agent_id=app.active_agent_id))
        await pilot.pause()

        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 1
        assert "retry 2/10" in info_widgets[0]._message
        assert "Rate limited" in info_widgets[0]._message


@pytest.mark.asyncio
async def test_system_notification_compact_boundary(mock_sdk):
    """Compact boundary notification displays."""
    from claudechic.messages import SystemNotification
    from claudechic.widgets import SystemInfo
    from claude_agent_sdk import SystemMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        sdk_msg = SystemMessage(
            subtype="compact_boundary",
            data={"content": "Conversation compacted", "level": "info"}
        )

        app.post_message(SystemNotification(sdk_msg, agent_id=app.active_agent_id))
        await pilot.pause()

        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 1
        assert "compacted" in info_widgets[0]._message.lower()


@pytest.mark.asyncio
async def test_system_notification_ignored_subtypes(mock_sdk):
    """Certain subtypes are silently ignored."""
    from claudechic.messages import SystemNotification
    from claudechic.widgets import SystemInfo
    from claude_agent_sdk import SystemMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # These subtypes should not create widgets
        for subtype in ["stop_hook_summary", "turn_duration", "local_command"]:
            sdk_msg = SystemMessage(
                subtype=subtype,
                data={"level": "info"}
            )
            app.post_message(SystemNotification(sdk_msg, agent_id=app.active_agent_id))

        await pilot.pause()

        # No SystemInfo widgets should be created
        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 0
