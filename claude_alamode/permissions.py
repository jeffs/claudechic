"""Permission request handling for tool approvals."""

import threading
from dataclasses import dataclass, field
from typing import Any

import anyio

from claude_alamode.formatting import format_tool_header


@dataclass
class PermissionRequest:
    """Represents a pending permission request.

    Used for both UI display and programmatic testing.
    """

    tool_name: str
    tool_input: dict[str, Any]
    _event: threading.Event = field(default_factory=threading.Event)
    _result: str = "deny"

    @property
    def title(self) -> str:
        """Format permission prompt title."""
        return f"Allow {format_tool_header(self.tool_name, self.tool_input)}?"

    def respond(self, result: str) -> None:
        """Respond to this permission request.

        Args:
            result: One of "allow", "allow_all", or "deny"
        """
        self._result = result
        self._event.set()

    async def wait(self) -> str:
        """Wait for response (from UI or programmatic).

        Returns:
            The response string ("allow", "allow_all", or "deny")
        """
        while not self._event.is_set():
            await anyio.sleep(0.05)
        return self._result


async def dummy_hook(input_data, tool_use_id, context):
    """Dummy hook required for can_use_tool to work in Python SDK."""
    return {"continue_": True}
