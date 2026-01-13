# Claude à la Mode

A stylish terminal UI for Claude Code, built with Textual and wrapping the `claude-agent-sdk`.

## Run

```bash
uv run alamode
uv run alamode --resume     # Resume most recent session
uv run alamode -s <uuid>    # Resume specific session
```

Requires Claude Code to be logged in with a Max/Pro subscription (`claude /login`).

## File Map

```
claude_alamode/
├── __init__.py        # Package entry, exports ChatApp
├── __main__.py        # CLI entry point
├── app.py             # ChatApp - main application, event handlers
├── formatting.py      # Tool formatting, diff rendering (pure functions)
├── messages.py        # Custom Textual Message types for SDK events
├── permissions.py     # PermissionRequest dataclass for tool approval
├── sessions.py        # Session file loading and listing (pure functions)
├── styles.tcss        # Textual CSS - visual styling
└── widgets/
    ├── __init__.py    # Re-exports all widgets
    ├── chat.py        # ChatMessage, ChatInput, ThinkingIndicator
    ├── header.py      # CPUBar, ContextBar, ContextHeader
    ├── prompts.py     # SelectionPrompt, QuestionPrompt, SessionItem
    └── tools.py       # ToolUseWidget, TaskWidget

tests/
├── conftest.py        # Shared fixtures (wait_for)
└── test_app.py        # E2E tests
```

## Architecture

### Module Boundaries

**Pure functions (no UI dependencies):**
- `formatting.py` - Tool header formatting, diff rendering, language detection
- `sessions.py` - Session file I/O, listing, filtering

**Internal protocol:**
- `messages.py` - Custom `Message` subclasses for thread communication
- `permissions.py` - `PermissionRequest` dataclass bridging SDK callbacks to UI

**UI components:**
- `widgets/` - Textual widgets with associated styles
- `app.py` - Main app orchestrating widgets and SDK

### Widget Hierarchy

```
ChatApp
├── ContextHeader (custom Header)
│   └── HeaderIndicators
│       ├── CPUBar
│       └── ContextBar
├── Horizontal #main
│   ├── ListView #session-picker (hidden by default)
│   └── VerticalScroll #chat-view
│       ├── ChatMessage (user/assistant)
│       ├── ToolUseWidget (collapsible tool display)
│       │   └── Collapsible with diff or markdown content
│       ├── TaskWidget (for Task tool - contains nested content)
│       │   └── #task-content with ChatMessages and ToolUseWidgets
│       └── ThinkingIndicator (animated spinner)
├── Horizontal #input-wrapper
│   ├── ChatInput (or SelectionPrompt/QuestionPrompt when prompting)
└── Footer
```

### Message Flow (Thread Communication)

The SDK runs in a background worker. Custom `Message` types communicate to the main thread:

```
SDK Worker Thread                    Main Thread (UI)
─────────────────                    ────────────────
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
- Shift+Tab: Toggle auto-edit mode

## Testing

```bash
uv run pytest tests/ -v
```

Tests use `app.interactions` queue to programmatically respond to permission prompts, and `app.completions` queue to wait for response completion. Real SDK required.
