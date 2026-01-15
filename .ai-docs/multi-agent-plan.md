# Multi-Agent Support Plan

## Overview

Enable a single alamode instance to manage multiple Claude agents simultaneously, with a sidebar showing agent status and quick switching between them.

## Core Concepts

### AgentSession

Per-agent state extracted into a dataclass:

```python
@dataclass
class AgentSession:
    id: str                          # Unique ID (UUID)
    name: str                        # Display name
    cwd: Path                        # Working directory
    worktree: str | None             # Worktree branch name (if applicable)
    client: ClaudeSDKClient | None
    session_id: str | None           # SDK session ID for resume

    # Status: gray=busy, primary=needs_input, dim=idle
    status: Literal["idle", "busy", "needs_input"]

    # UI state (per-agent)
    current_response: ChatMessage | None
    pending_tools: dict[str, ToolUseWidget | TaskWidget]
    active_tasks: dict[str, TaskWidget]
    recent_tools: list[ToolUseWidget | TaskWidget]
```

### Async Workers

Workers decorated with `@work` run async in the main event loop (not separate threads):

- `run_claude(prompt)` worker captures the `AgentSession` at call time
- Worker posts messages with `agent_id` to route to correct session
- Direct method calls work fine (no need for `call_from_thread()`)

This is clean because everything runs in the same async event loop.

### Widget Hierarchy (Updated)

```
ChatApp
├── ContextHeader
├── Horizontal #main
│   ├── ListView #session-picker (hidden by default)
│   ├── VerticalScroll #chat-view-{agent_id} (one per agent, only active visible)
│   └── Vertical #right-sidebar
│       ├── AgentSidebar (list of agents with status)
│       └── TodoPanel (todos for active agent)
├── Horizontal #input-wrapper
│   └── ChatInput
└── Footer
```

The right sidebar stacks agents list above todos, keeping related controls together.

## Implementation Phases

### Phase 1: AgentSession Extraction

**Files:** `agent.py` (new), `app.py`

1. Create `AgentSession` dataclass in `agent.py`
2. Create factory function `create_agent_session(name, cwd, worktree=None)`
3. Refactor `ChatApp`:
   - Add `self.agents: dict[str, AgentSession] = {}`
   - Add `self.active_agent_id: str | None = None`
   - Move per-agent state into `AgentSession`
   - Create single agent on startup (backward compatible)

**Test:** App works exactly as before with single agent.

### Phase 2: Message-Based Updates

**Files:** `app.py`

1. Worker captures `agent_id` at start of `run_claude()`
2. Worker posts messages with `agent_id` to route to correct session
3. Message handlers look up session from `agent_id`
4. All runs in main async event loop (no threads)

**Test:** Still single agent, messages route correctly.

### Phase 3: Multi-Container UI

**Files:** `app.py`, `styles.tcss`

1. Change `#chat-view` to `#chat-container` (holds multiple views)
2. Create `VerticalScroll` per agent with id `#chat-view-{agent_id}`
3. Add `_switch_agent(agent_id)` method:
   - Hide all chat views
   - Show selected agent's view
   - Update `active_agent_id`
   - Focus input

**Test:** Can programmatically switch between views.

### Phase 4: Agent Sidebar

**Files:** `widgets/agents.py` (new), `widgets/__init__.py`, `app.py`, `styles.tcss`

1. Create `AgentItem(Static)`:
   - Shows agent name + status indicator
   - Status colors: dim=idle, gray=busy, orange=needs_input
   - Clickable to switch

2. Create `AgentSidebar(Widget)`:
   - Contains `AgentItem` widgets
   - Methods: `add_agent()`, `remove_agent()`, `update_status()`

3. Mount sidebar in `#main`
4. Handle `AgentItem` clicks to switch agents

**Styles:**
```tcss
#agent-sidebar {
    width: 20;
    border-right: solid #333333;
}
AgentItem {
    padding: 0 1;
}
AgentItem.active {
    background: #222222;
}
AgentItem .status-idle { color: dim; }
AgentItem .status-busy { color: #666666; }
AgentItem .status-needs-input { color: #cc7700; }
```

### Phase 5: Agent Lifecycle

**Files:** `app.py`

1. **New agent command:** `/agent [name] [path]`
   - Creates new `AgentSession`
   - Connects SDK client
   - Adds to sidebar
   - Switches to new agent

2. **Resume into list:** `/resume <session_id>`
   - If session exists in another agent, switch to it
   - Otherwise, create new agent from session

3. **Close agent:** `/agent close [name]`
   - Disconnects client
   - Removes from sidebar
   - Switches to another agent (or shows empty state)

4. **Worktree integration:**
   - `/worktree <name>` creates agent if new worktree
   - Multiple agents can share same worktree

### Phase 6: Status & Notifications

**Files:** `app.py`, `widgets/agents.py`

1. Update status on events:
   - `run_claude()` start → `busy`
   - Permission prompt shown → `needs_input`
   - `ResponseComplete` → `idle`

2. Toast notification when non-active agent needs input:
   - "Agent '{name}' needs input"
   - Clicking notification switches to agent

3. Keyboard shortcuts:
   - `Ctrl+1..9` - Switch to agent by position
   - `Ctrl+N` - New agent prompt

## File Changes Summary

| File | Change |
|------|--------|
| `agent.py` | NEW - AgentSession dataclass |
| `messages.py` | Add agent_id to all messages |
| `widgets/agents.py` | NEW - AgentSidebar, AgentItem |
| `widgets/__init__.py` | Export new widgets |
| `app.py` | Multi-agent orchestration |
| `styles.tcss` | Sidebar and agent item styles |

## Open Questions

1. **Max agents?** Should we limit concurrent agents? (Memory/performance)
2. **Agent persistence option?** User asked for no persistence, but maybe optional `.alamode-agents.json`?
3. **Visual density?** Sidebar could show abbreviated status or full status line

## Not in Scope (Future)

- Agent-to-agent communication
- Parallel task distribution
- Agent templates/presets
