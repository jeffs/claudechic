# Claude à la Mode

A stylish terminal UI for Claude Code.

![Claude à la Mode screenshot](images/screenshot.png)

## What is this?

Claude à la Mode wraps the [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview) in a [Textual](https://textual.textualize.io/) interface. You get the same Claude Code agent, but with a more visually polished and hackable experience:

-  **Focused** - designed to remove clutter and focus attention
-  **Hackable** - easily extensible with Python Code
-  **Open Source** - all UI code available
-  **Multi-Agent** - run several Claude agents in parallel
-  **Just Claude** - uses the same Claude Code agent you trust

This project *does not* re-implement the Claude agent logic (we trust Anthropic with that).  It only provides a different skin on top of that experience.
Also, by putting a layer around the Agent SDK we're able to provide some nice features, like multi-agent management.

## Installation

```bash
uv tool install claude-alamode
```

Requires Claude Code with a Max/Pro subscription:

```bash
claude /login
```

## Run

```bash
claude-alamode
```

## FAQ

??? question "Does this replace Claude Code?"

    It replaces the `claude` CLI, but it wraps the same underlying `claude-agent-sdk` that that CLI uses.

    Additionally, you need `claude` to log in and for advanced configuration.

??? question "Can I use my existing Claude Code sessions?"

    Yes. This stores and loads all Claude state in exactly the way `claude` does.

??? question "Does it work with MCP servers?"

    Yes. This stores and loads all Claude state in exactly the way `claude` does.

??? question "Does it work with my Hooks and Skills?"

    Yes. This stores and loads all Claude state in exactly the way `claude` does.

??? question "How does this relate to OpenCode?"

    OpenCode is generally more impressive.  It supports many different models and is way more mature.

    OpenCode designed their own Agent logic.  This just reuses Claude Code's logic.  This project is much more thin.
