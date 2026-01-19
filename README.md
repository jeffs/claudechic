# Claude Chic

A stylish terminal UI for [Claude Code](https://docs.anthropic.com/en/docs/claude-code), built with [Textual](https://textual.textualize.io/).

## Start

```bash
uvx claudechic /welcome
```

## Install

With `uv`
```bash
uv tool install claudechic
```

With `pip`

```bash
pip install claudechic
```

Requires Claude Code to be logged in (`claude /login`).

## Features

-  Styled version of the `claude` CLI
-  Run multiple agents concurrently
-  Manage Git Worktrees
-  Hackable in Python with Textual

Built on the [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview)
