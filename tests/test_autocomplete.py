"""Tests for autocomplete widget."""

import pytest
from pathlib import Path

from claude_alamode import ChatApp
from claude_alamode.widgets import ChatInput, TextAreaAutoComplete


@pytest.mark.asyncio
async def test_slash_command_autocomplete(tmp_path: Path):
    """Test slash command autocomplete shows and filters correctly."""
    app = ChatApp()
    async with app.run_test(size=(80, 24)) as pilot:
        input_widget = app.query_one(ChatInput)
        autocomplete = app.query_one(TextAreaAutoComplete)

        # Initially hidden
        assert autocomplete.styles.display == "none"

        # Type / to trigger autocomplete
        input_widget.text = "/"
        await pilot.pause()

        # Should show commands (includes SDK commands, so count varies)
        assert autocomplete.styles.display == "block"
        assert autocomplete.option_list.option_count >= 4  # At least local commands

        # Type more to filter - /worktree should narrow it down
        input_widget.text = "/worktree"
        await pilot.pause()

        # Should show worktree commands (base, finish, cleanup)
        assert autocomplete.option_list.option_count == 3

        # Type even more to narrow to just one
        input_widget.text = "/worktree f"
        await pilot.pause()

        # Should show just /worktree finish
        assert autocomplete.option_list.option_count == 1

        # Clear input - should hide
        input_widget.text = ""
        await pilot.pause()

        assert autocomplete.styles.display == "none"


@pytest.mark.asyncio
async def test_path_autocomplete(tmp_path: Path):
    """Test file path autocomplete with @ trigger."""
    # Create some test files
    (tmp_path / "file1.txt").touch()
    (tmp_path / "file2.txt").touch()
    (tmp_path / "subdir").mkdir()

    app = ChatApp()
    # Override base_path for test
    async with app.run_test(size=(80, 24)) as pilot:
        autocomplete = app.query_one(TextAreaAutoComplete)
        autocomplete.base_path = tmp_path

        input_widget = app.query_one(ChatInput)

        # Type @ to start path completion
        input_widget.text = "@"
        await pilot.pause()

        # Should show files from tmp_path
        assert autocomplete.styles.display == "block"
        assert autocomplete.option_list.option_count == 3  # file1.txt, file2.txt, subdir/

        # Filter to just .txt files
        input_widget.text = "@file"
        await pilot.pause()

        assert autocomplete.option_list.option_count == 2


@pytest.mark.asyncio
async def test_tab_completion():
    """Test that Tab completes the selection."""
    app = ChatApp()
    async with app.run_test(size=(80, 24)) as pilot:
        input_widget = app.query_one(ChatInput)
        autocomplete = app.query_one(TextAreaAutoComplete)

        # Type enough to filter to a unique match
        input_widget.text = "/worktree f"
        await pilot.pause()

        # Should show just /worktree finish
        assert autocomplete.option_list.option_count == 1

        # Press Tab to complete
        await pilot.press("tab")
        await pilot.pause()

        # Input should now be /worktree finish
        assert input_widget.text == "/worktree finish"
        assert autocomplete.styles.display == "none"
