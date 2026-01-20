# Claude Chic

A stylish terminal UI that wraps the [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview) in a [Textual](https://textual.textualize.io/) interface.

You get the same Claude Code agent, but with a more visually polished and hackable experience:

-  **Pretty** - designed to remove clutter and focus attention
-  **Hackable** - easily extensible with Python Code
-  **Multi-Agent** - run several Claude agents in parallel
-  **Open Source** - all UI code available
-  **Just Claude** - uses the same Claude Code agent you trust

This project *does not* re-implement the Claude agent logic (we trust Anthropic with that).  It only provides a different skin on top of that experience.
Also, by putting a layer around the Agent SDK we're able to provide some nice features, like multi-agent and git management.

<div class="video-container">
<iframe src="https://www.youtube-nocookie.com/embed/AIEDqdSPuEo" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
</div>

## Installation

```bash
uv tool install claudechic --upgrade
```

Use Claude to log in with your subscription:

```bash
claude /login
```

## Run

```bash
claudechic
```

## FAQ

??? question "Does this replace Claude Code?"

    It replaces the `claude` CLI, but it wraps the same underlying `claude-agent-sdk` that the `claude` CLI uses.

    Additionally, you need `claude` to log in and for advanced configuration.

??? question "Can I use my existing Claude Code sessions?"

    Yes. This stores and loads all Claude state in exactly the way `claude` does.

??? question "Does it work with MCP servers?"

    Yes. This stores and loads all Claude state in exactly the way `claude` does.

??? question "Does it work with my Hooks and Skills?"

    Yes. This stores and loads all Claude state in exactly the way `claude` does.

??? question "How does this relate to OpenCode?"

    OpenCode is more impressive.  OpenCode supports many different models and is way more mature.

    OpenCode designed their own Agent logic.  Claude Chic reuses Claude Code's agent logic, which some people prefer.  This project is generally more thin.  You should try both.  You should try lots of things.

??? question "How do you make money?"

    This is an open source hobby project.  We do not currently have commercial aspirations
