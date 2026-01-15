# Multi-Agent Workflows

Run multiple Claude agents simultaneously, each with its own context and working directory.

## Personal Note

ClaudeChic's style is nice, but the agent organization it offers is what makes me (matt) actually reach for it over `claude`, despite it's relative youth.

Coding with Claude is amazing, but I find that when I use it seriously I have many Claude sessions in many terminals spread across many virtual desktops (shout out to Aerospace tiling window manager, btw). It's fun, but chaotic, and my brain starts to melt.

The multi-agent organization and `git worktree` management in this project remove a tremendous administrative mental burden for me.  When I have a new idea I start a new thread in a new worktree immediately.  The overhead of having and starting ideas is zero.  The overhead of completing (or ruthlessly abandoning) those ideas is zero.  I find that ideas and work flow more freely.

## Agents

One traditional `claude` session is bound to one `agent`.  If you want a new agent, you either `/clear` to reset your `claude` session, or you open a new terminal to start a `claude` session in parallel.

In Claude a la mode, you type `/agent some-new-name` and a new agent starts running in your session with access to all the same files.  You can switch back and forth between these agents easily.

### Example: Review

```
/agent reviewer

# Fresh new agents starts

We have lots of changes in this repository.  I want you to review the plan and then the code in depth.  Tell me what you think.
```

## Agent Commands

TODO: make this a table

-  `/agent`: Shows all running agents (but really, just look at the sidebar)
-  `/agent my-agent-name`: Start a new agent in the current directory
-  `/agent my-agent-name /path/to/directory`: Start a new agent in the given directory
-  `/agent close`: Close the current agent

However, it's common to switch between agents or close them by just clicking on them in the sidebar.

Also, it's common to create an agent indirectly through opening a `git worktree`.  See the next session.

## Worktrees

Note from matt: I almost never use agents on their own.  I use them almost
exclusively with worktrees.

[Git worktrees](https://git-scm.com/docs/git-worktree) are a rarely-used feature of git that creates a new git branch and ties it to a new directory in your workspace.  This allows you to have a sequence of directories on your filesystem, all with different copies of your codebase:

```
~/projects/
├── myrepo/            # Main worktree (main branch)
├── myrepo-feature-1/  # Some feature you're working on
├── myrepo-feature-2/  # Some feature you're working on
├── ...
└── myrepo-feature-n/  # Some feature you're working on
```

You can work in each directory safely, editing code, running tests, etc, without worrying about trampling on other ongoing work.

The lifecycle of the directory and the branch are also linked, so as you clean up the worktree the directory is cleaned up as well.

They're great features, and great when paired with concurrent development with Claude, but they're rarely-used enough that few people are familiar with how they work (or at least that was true of the library authors).  And so, we automated them into Claude a la mode.

## Worktree Workflow

Claude a la mode worktree commands automate the process of setting up a worktree, putting an agent in it, doing work, and then automating the merge/rebase/close process back with main.  The process is as follows:

```
# Make a new branch+directory, create a new agent in that directory, move there
/worktree my-feature

# Do work with claude
...

# Ask Claude to commit your code
Commit

# Rebase on upstream and clean up any conflicts, then merge with upstream, then clean up the worktree
/worktree finish
```

Claude a la mode sets up worktrees for you and runs new agents in them.  When you're done, we ask Claude to safely handle the rebase/merge process so you have nice linear history, despite all of the concurrent development you've been doing.

## Concurrent development

Used together, agents and worktrees make it trivial to have many ongoing threads of work, all neatly managed for you.  You can start a new thread any time and then leave it for days.  You can bounce between agents as they're busy or idle as you like.

## Resuming work

When you restart Claude a la mode, your worktrees will be listed in the agent list.  If you click on them we'll resume the last session in that worktree for you.  All of your state is, as always, stored in Claude state in `~/.claude/projects/`.

In practice this means you can start work freely without worrying about
finishing it soon.  It is free to keep agents and worktrees around.
You can close your session and open it up again next week and all your agents
will be ready for you.
