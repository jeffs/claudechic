"""In-process MCP server for claudechic agent control.

Exposes tools for Claude to manage agents within claudechic:
- spawn_agent: Create new agent, optionally with initial prompt
- spawn_worktree: Create git worktree + agent
- ask_agent: Send question to existing agent (expects reply)
- tell_agent: Send message to existing agent (no reply expected)
- list_agents: List current agents and their status
- close_agent: Close an agent by name
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from claudechic.analytics import capture
from claudechic.features.worktree.git import start_worktree

if TYPE_CHECKING:
    from claudechic.app import ChatApp

# Global app reference, set by ChatApp.on_mount()
_app: ChatApp | None = None


def set_app(app: ChatApp) -> None:
    """Register the app instance for MCP tools to use."""
    global _app
    _app = app


def _text_response(text: str) -> dict[str, Any]:
    """Format a text response for MCP."""
    return {"content": [{"type": "text", "text": text}]}


def _find_agent_by_name(name: str):
    """Find an agent by name. Returns (agent, error_message)."""
    if _app is None or _app.agent_mgr is None:
        return None, "App not initialized"
    agent = _app.agent_mgr.find_by_name(name)
    if agent:
        return agent, None
    return None, f"Agent '{name}' not found. Use list_agents to see available agents."


def _track_mcp_tool(tool_name: str) -> None:
    """Track MCP tool usage for analytics."""
    if _app and _app.agent_mgr:
        active = _app.agent_mgr.active
        _app.run_worker(
            capture(
                "mcp_tool_used",
                tool=tool_name,
                agent_id=active.analytics_id if active else "unknown",
            )
        )


async def _send_prompt_to_agent(agent, prompt: str) -> None:
    """Send prompt directly to agent without switching UI.

    Uses Agent.send() for concurrent operation.
    """
    if agent.client is None:
        raise RuntimeError(f"Agent '{agent.name}' not connected")
    await agent.send(prompt)


def _make_spawn_agent(caller_name: str | None = None):
    """Create spawn_agent tool with optional caller name bound."""

    @tool(
        "spawn_agent",
        "Create a new Claude agent in claudechic. The agent gets its own chat view and can work independently.",
        {"name": str, "path": str, "prompt": str},
    )
    async def spawn_agent(args: dict[str, Any]) -> dict[str, Any]:
        """Spawn a new agent, optionally with an initial prompt."""
        if _app is None or _app.agent_mgr is None:
            return _text_response("Error: App not initialized")
        _track_mcp_tool("spawn_agent")

        name = args["name"]
        # Default to active agent's cwd (so agents inherit creator's directory)
        default_cwd = _app.agent_mgr.active.cwd if _app.agent_mgr.active else Path.cwd()
        path = Path(args.get("path", str(default_cwd))).resolve()
        prompt = args.get("prompt")

        if not path.exists():
            return _text_response(f"Error: Path '{path}' does not exist")

        # Check if agent with this name already exists
        if _app.agent_mgr.find_by_name(name):
            return _text_response(f"Error: Agent '{name}' already exists")

        try:
            # Create agent via AgentManager (handles SDK connection)
            agent = await _app.agent_mgr.create(name=name, cwd=path, switch_to=False)
        except Exception as e:
            return _text_response(f"Error creating agent: {e}")

        result = f"Created agent '{name}' in {path}"

        if prompt:
            # Wrap prompt with spawner info
            if caller_name:
                prompt = f"[Spawned by agent '{caller_name}']\n\n{prompt}"
            try:
                await _send_prompt_to_agent(agent, prompt)
                result += f"\nSent initial prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}"
            except Exception as e:
                result += f"\nWarning: Failed to send prompt: {e}"

        return _text_response(result)

    return spawn_agent


def _make_spawn_worktree(caller_name: str | None = None):
    """Create spawn_worktree tool with optional caller name bound."""

    @tool(
        "spawn_worktree",
        "Create a git worktree (feature branch) with a new agent. Useful for isolated feature development.",
        {"name": str, "base_branch": str, "prompt": str},
    )
    async def spawn_worktree(args: dict[str, Any]) -> dict[str, Any]:
        """Create a git worktree and spawn an agent in it."""
        if _app is None or _app.agent_mgr is None:
            return _text_response("Error: App not initialized")
        _track_mcp_tool("spawn_worktree")

        name = args["name"]
        prompt = args.get("prompt")

        # Create the worktree
        success, message, wt_path = start_worktree(name)
        if not success or wt_path is None:
            return _text_response(f"Error creating worktree: {message}")

        try:
            # Create agent in the worktree via AgentManager
            agent = await _app.agent_mgr.create(
                name=name, cwd=wt_path, worktree=name, switch_to=False
            )
        except Exception as e:
            return _text_response(
                f"Worktree created at {wt_path}, but agent failed: {e}"
            )

        result = f"Created worktree '{name}' at {wt_path} with new agent"

        if prompt:
            # Wrap prompt with spawner info
            if caller_name:
                prompt = f"[Spawned by agent '{caller_name}']\n\n{prompt}"
            try:
                await _send_prompt_to_agent(agent, prompt)
                result += f"\nSent initial prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}"
            except Exception as e:
                result += f"\nWarning: Failed to send prompt: {e}"

        return _text_response(result)

    return spawn_worktree


def _make_ask_agent(caller_name: str | None = None):
    """Create ask_agent tool with optional caller name bound."""

    @tool(
        "ask_agent",
        "Send a question to another agent. Returns immediately - the agent will respond back using tell_agent (or ask_agent if they need more context) when ready.",
        {"name": str, "prompt": str},
    )
    async def ask_agent(args: dict[str, Any]) -> dict[str, Any]:
        """Send question to an agent. Non-blocking."""
        if _app is None or _app.agent_mgr is None:
            return _text_response("Error: App not initialized")
        _track_mcp_tool("ask_agent")

        name = args["name"]
        prompt = args["prompt"]

        agent, error = _find_agent_by_name(name)
        if agent is None:
            return _text_response(f"Error: {error}")

        # Wrap prompt with caller info and reply expectation
        if caller_name:
            prompt = f"[Question from agent '{caller_name}' - please respond back using tell_agent, or ask_agent if you need more context]\n\n{prompt}"

        try:
            await _send_prompt_to_agent(agent, prompt)
        except Exception as e:
            return _text_response(f"Error: {e}")

        return _text_response(
            f"Question sent to '{name}'. They will respond when ready."
        )

    return ask_agent


def _make_tell_agent(caller_name: str | None = None):
    """Create tell_agent tool with optional caller name bound."""

    @tool(
        "tell_agent",
        "Send a message to another agent without expecting a reply. Use for status updates, results, or answering questions.",
        {"name": str, "message": str},
    )
    async def tell_agent(args: dict[str, Any]) -> dict[str, Any]:
        """Send message to an agent. Non-blocking, no reply expected."""
        if _app is None or _app.agent_mgr is None:
            return _text_response("Error: App not initialized")
        _track_mcp_tool("tell_agent")

        name = args["name"]
        message = args["message"]

        agent, error = _find_agent_by_name(name)
        if agent is None:
            return _text_response(f"Error: {error}")

        # Wrap message with caller info (no reply expectation)
        if caller_name:
            message = f"[Message from agent '{caller_name}']\n\n{message}"

        try:
            await _send_prompt_to_agent(agent, message)
        except Exception as e:
            return _text_response(f"Error: {e}")

        return _text_response(f"Message sent to '{name}'.")

    return tell_agent


@tool(
    "list_agents",
    "List all agents currently running in claudechic with their status and working directory.",
    {},
)
async def list_agents(args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """List all agents and their status."""
    try:
        if _app is None or _app.agent_mgr is None:
            return _text_response("Error: App not initialized")

        if len(_app.agent_mgr) == 0:
            return _text_response("No agents running")

        lines = ["Agents:"]
        for i, agent in enumerate(_app.agent_mgr, 1):
            active = "*" if agent.id == _app.agent_mgr.active_id else " "
            wt = " (worktree)" if agent.worktree else ""
            lines.append(f"{active}{i}. {agent.name} [{agent.status}] - {agent.cwd}{wt}")

        return _text_response("\n".join(lines))
    except Exception as e:
        return _text_response(f"Error listing agents: {e}")


@tool(
    "close_agent",
    "Close an agent by name. Cannot close the last remaining agent.",
    {"name": str},
)
async def close_agent(args: dict[str, Any]) -> dict[str, Any]:
    """Close an agent."""
    try:
        if _app is None or _app.agent_mgr is None:
            return _text_response("Error: App not initialized")

        name = args["name"]

        # Can't close the last agent
        if len(_app.agent_mgr) <= 1:
            return _text_response("Error: Cannot close the last agent")

        agent, error = _find_agent_by_name(name)
        if agent is None:
            return _text_response(f"Error: {error}")

        agent_id = agent.id
        agent_name = agent.name

        # Use app's close method which handles UI cleanup
        _app._do_close_agent(agent_id)

        return _text_response(f"Closed agent '{agent_name}'")
    except Exception as e:
        return _text_response(f"Error closing agent: {e}")


def create_chic_server(caller_name: str | None = None):
    """Create the chic MCP server with all tools.

    Args:
        caller_name: Name of the agent that will use this server.
            Used to identify the sender in spawn/ask/tell agent calls.
    """
    return create_sdk_mcp_server(
        name="chic",
        version="1.0.0",
        tools=[
            _make_spawn_agent(caller_name),
            _make_spawn_worktree(caller_name),
            _make_ask_agent(caller_name),
            _make_tell_agent(caller_name),
            list_agents,
            close_agent,
        ],
    )
