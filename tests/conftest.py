"""Shared test fixtures."""

import pytest
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch


async def empty_async_gen():
    """Empty async generator for mocking receive_response."""
    return
    yield  # noqa: unreachable - makes this an async generator


async def wait_for_workers(app):
    """Wait for all workers to complete."""
    await app.workers.wait_for_complete()


async def submit_command(app, pilot, command: str):
    """Submit a command, handling autocomplete properly.

    When setting input text directly, autocomplete may activate.
    This helper hides it before submitting to ensure the command goes through.
    """
    from claudechic.widgets import ChatInput

    input_widget = app.query_one("#input", ChatInput)
    input_widget.text = command
    await pilot.pause()

    # Hide autocomplete if it's showing (triggered by / or @)
    if input_widget._autocomplete and input_widget._autocomplete.display:
        input_widget._autocomplete.action_hide()
        await pilot.pause()

    input_widget.action_submit()
    await pilot.pause()


@pytest.fixture
def mock_sdk():
    """Patch SDK to not actually connect.

    Patches both app.py and agent.py imports since agents create their own clients.
    Also patches FileIndex to avoid subprocess transport leaks during test cleanup.
    Disables analytics to avoid httpx connection leaks.
    """
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.interrupt = AsyncMock()
    mock_client.get_server_info = AsyncMock(return_value={"commands": [], "models": []})
    mock_client.receive_response = lambda: empty_async_gen()
    mock_client._transport = None  # For get_claude_pid_from_client

    # Mock FileIndex to avoid git subprocess transport leaks
    # The subprocess transports try to close after the event loop is closed
    from claudechic.file_index import FileIndex

    mock_file_index = MagicMock(spec=FileIndex)
    mock_file_index.refresh = AsyncMock()
    mock_file_index.files = []

    # Use ExitStack to avoid deep nesting
    with ExitStack() as stack:
        # Disable analytics to avoid httpx AsyncClient connection leaks
        stack.enter_context(
            patch("claudechic.analytics.get_analytics_enabled", return_value=False)
        )
        stack.enter_context(
            patch("claudechic.app.ClaudeSDKClient", return_value=mock_client)
        )
        stack.enter_context(
            patch("claudechic.agent.ClaudeSDKClient", return_value=mock_client)
        )
        stack.enter_context(
            patch("claudechic.agent.FileIndex", return_value=mock_file_index)
        )
        stack.enter_context(
            patch("claudechic.app.FileIndex", return_value=mock_file_index)
        )
        yield mock_client
