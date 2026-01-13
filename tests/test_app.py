"""End-to-end tests for cc-textual app."""

import asyncio
import pytest
from pathlib import Path

from claude_alamode import ChatApp
from claude_alamode.widgets import ChatInput
from tests.conftest import wait_for


@pytest.mark.asyncio
async def test_write_permission_flow(tmp_path: Path):
    """Test: ask Claude to write file, verify permission prompt, allow all,
    then second write should auto-approve, then rm should still prompt."""
    file1 = tmp_path / "test1.txt"
    file2 = tmp_path / "test2.txt"

    app = ChatApp()
    async with app.run_test(size=(120, 40)) as pilot:
        # Wait for SDK to connect
        await wait_for(lambda: app.client is not None, timeout=10)

        # Send request to write a file
        input_widget = app.query_one(ChatInput)
        input_widget.text = f"Write 'hello' to {file1}. Do not read first, just write. Do not explain, just use the Write tool now."
        await pilot.press("enter")

        # Wait for Write permission (allow Read if it comes first)
        while True:
            request = await asyncio.wait_for(app.interactions.get(), timeout=30)
            if request.tool_name in ("Write", "Edit"):
                break
            request.respond("allow")

        # Respond with "allow all"
        assert request.tool_name == "Write"
        request.respond("allow_all")

        # Wait for auto_approve_edits to be set
        await wait_for(lambda: app.auto_approve_edits is True, timeout=2)

        # Wait for Claude to complete this response
        await asyncio.wait_for(app.completions.get(), timeout=30)

        # Verify file was created
        assert file1.exists(), f"File {file1} should have been created"

        # Ask for second write - should auto-approve (no interaction)
        input_widget.text = f"Write 'world' to {file2}. Just write it."
        await pilot.press("enter")

        # Wait for completion - no permission request should come
        await asyncio.wait_for(app.completions.get(), timeout=30)

        # Verify second file created and no permission was requested
        assert file2.exists(), f"File {file2} should have been created"
        assert app.interactions.empty(), "Second write should have been auto-approved"

        # Ask to delete with rm - should prompt (Bash not auto-approved)
        input_widget.text = f"Delete {file1} using rm. Just do it."
        await pilot.press("enter")

        # Wait for Bash permission request
        request = await asyncio.wait_for(app.interactions.get(), timeout=30)
        assert request.tool_name == "Bash", f"Expected Bash, got {request.tool_name}"

        # Deny it
        request.respond("deny")

        # Wait for completion
        await asyncio.wait_for(app.completions.get(), timeout=30)

        # File should still exist (rm was denied)
        assert file1.exists(), f"File {file1} should still exist (rm was denied)"
