# Claude à la Mode

A stylish terminal UI for Claude Code, built with Textual and wrapping the `claude-agent-sdk`.

## Run

```bash
uv run claude-alamode
uv run claude-alamode --resume     # Resume most recent session
uv run claude-alamode -s <uuid>    # Resume specific session
```

Requires Claude Code to be logged in with a Max/Pro subscription (`claude /login`).

## File Map

```
claude_alamode/
├── __init__.py        # Package entry, exports ChatApp
├── __main__.py        # CLI entry point
├── agent.py           # AgentSession dataclass for multi-agent state
├── app.py             # ChatApp - main application, event handlers
├── errors.py          # Logging infrastructure, error handling
├── file_index.py      # Fuzzy file search using git ls-files
├── formatting.py      # Tool formatting, diff rendering (pure functions)
├── history.py         # Global history loading from ~/.claude/history.jsonl
├── mcp.py             # In-process MCP server for agent control tools
├── messages.py        # Custom Textual Message types for SDK events
├── permissions.py     # PermissionRequest dataclass for tool approval
├── sessions.py        # Session file loading and listing (pure functions)
├── styles.tcss        # Textual CSS - visual styling
├── theme.py           # Textual theme definition
├── features/
│   ├── __init__.py    # Feature module exports
│   └── worktree/
│       ├── __init__.py   # Public API (list_worktrees, handle_worktree_command)
│       ├── commands.py   # /worktree command handlers
│       ├── git.py        # Git worktree operations
│       └── prompts.py    # WorktreePrompt widget
└── widgets/
    ├── __init__.py    # Re-exports all widgets
    ├── agents.py      # AgentSidebar, AgentItem for multi-agent UI
    ├── autocomplete.py # Autocomplete for slash commands and file paths
    ├── chat.py        # ChatMessage, ChatInput, ThinkingIndicator
    ├── diff.py        # Syntax-highlighted diff widget
    ├── footer.py      # Custom footer with git branch, CPU/context bars
    ├── history_search.py # Reverse history search widget (Ctrl+R)
    ├── indicators.py  # CPUBar, ContextBar resource monitors
    ├── prompts.py     # SelectionPrompt, QuestionPrompt, SessionItem
    ├── scroll.py      # AutoHideScroll - auto-hiding scrollbar container
    ├── todo.py        # TodoPanel for TodoWrite tool display
    └── tools.py       # ToolUseWidget, TaskWidget

tests/
├── __init__.py        # Package marker
├── conftest.py        # Shared fixtures (wait_for)
├── test_app.py        # E2E tests with real SDK
├── test_app_ui.py     # App UI tests without SDK
├── test_autocomplete.py # Autocomplete widget tests
├── test_file_index.py # Fuzzy file search tests
└── test_widgets.py    # Pure widget tests
```

## Architecture

### Module Boundaries

**Pure functions (no UI dependencies):**
- `formatting.py` - Tool header formatting, diff rendering, language detection
- `sessions.py` - Session file I/O, listing, filtering
- `file_index.py` - Fuzzy file search, git ls-files integration

**Internal protocol:**
- `messages.py` - Custom `Message` subclasses for async event communication
- `permissions.py` - `PermissionRequest` dataclass bridging SDK callbacks to UI
- `mcp.py` - MCP server exposing agent control tools to Claude

**Features:**
- `features/worktree/` - Git worktree management for isolated development

**UI components:**
- `widgets/` - Textual widgets with associated styles
- `app.py` - Main app orchestrating widgets and SDK

### Widget Hierarchy

```
ChatApp
├── Horizontal #main
│   ├── ListView #session-picker (hidden by default)
│   ├── AutoHideScroll #chat-view (one per agent, only active visible)
│   │   ├── ChatMessage (user/assistant)
│   │   ├── ToolUseWidget (collapsible tool display)
│   │   ├── TaskWidget (for Task tool - nested content)
│   │   └── ThinkingIndicator (animated spinner)
│   └── Vertical #right-sidebar (hidden when narrow or single agent)
│       ├── AgentSidebar (list of agents with status)
│       └── TodoPanel (todos for active agent)
├── Horizontal #input-wrapper
│   └── Vertical #input-container
│       ├── ImageAttachments (hidden by default)
│       ├── ChatInput (or SelectionPrompt/QuestionPrompt when prompting)
│       └── TextAreaAutoComplete (slash commands, file paths)
└── StatusFooter (git branch, CPU/context bars)
```

### Message Flow (Async Communication)

The SDK runs in async workers (same event loop, not separate threads). Custom `Message` types route events:

```
SDK Worker (async)                   Message Handlers
──────────────                       ────────────────
receive AssistantMessage  ──post──>  on_stream_chunk() -> update ChatMessage
receive ToolUseBlock      ──post──>  on_tool_use_message() -> mount ToolUseWidget
receive ToolResultBlock   ──post──>  on_tool_result_message() -> update widget
receive ResultMessage     ──post──>  on_response_complete() -> cleanup
```

### Permission Flow

When SDK needs tool approval:
1. `can_use_tool` callback creates `PermissionRequest`
2. Request queued to `app.interactions` (for testing)
3. `SelectionPrompt` mounted, replacing input
4. User selects allow/deny/allow-all
5. Callback returns `PermissionResultAllow` or `PermissionResultDeny`

For `AskUserQuestion` tool: `QuestionPrompt` handles multi-question flow.

### Styling

Visual language uses left border bars to indicate content type:
- **Orange** (`#cc7700`) - User messages
- **Blue** (`#334455`) - Assistant messages
- **Gray** (`#333333`) - Tool uses (brightens on hover)
- **Blue-gray** (`#445566`) - Task widgets

Context/CPU bars color-code by threshold (dim → yellow → red).

Copy buttons appear on hover. Collapsibles auto-collapse older tool uses.

## Key SDK Usage

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

client = ClaudeSDKClient(ClaudeAgentOptions(
    permission_mode="default",
    env={"ANTHROPIC_API_KEY": ""},
    can_use_tool=permission_callback,
    resume=session_id,
))
await client.connect()
await client.query("prompt")
async for message in client.receive_response():
    # Handle AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock, ResultMessage
```

## Keybindings

- Enter: Send message
- Ctrl+C (x2): Quit
- Ctrl+L: Clear chat (UI only)
- Ctrl+R: Reverse history search
- Shift+Tab: Toggle auto-edit mode
- Ctrl+N: New agent (hint)
- Ctrl+1-9: Switch to agent by position

## Multi-Agent Commands

- `/agent` - List all agents
- `/agent <name>` - Create new agent in current directory
- `/agent <name> <path>` - Create new agent in specified directory
- `/agent close` - Close current agent
- `/agent close <name>` - Close agent by name
- `/agent close <n>` - Close agent by position

Agent status indicators:
- ○ (dim) - idle
- ● (gray) - busy/working
- ● (orange) - needs input

## Testing

```bash
uv run pytest tests/ -v
```

Tests use `app.interactions` queue to programmatically respond to permission prompts, and `app.completions` queue to wait for response completion. Real SDK required.
