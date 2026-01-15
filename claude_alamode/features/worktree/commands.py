"""Worktree command handlers extracted from app.py."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.containers import Center
from textual import work

from claude_alamode.features.worktree.git import (
    FinishPhase,
    FinishState,
    ResolutionAction,
    WorktreeStatus,
    clean_gitignored_files,
    cleanup_worktrees,
    determine_resolution_action,
    diagnose_worktree,
    discard_all_changes,
    fast_forward_merge,
    finish_cleanup,
    get_cleanup_fix_prompt,
    get_finish_info,
    get_finish_prompt,
    list_worktrees,
    remove_worktree,
    start_worktree,
)
from claude_alamode.features.worktree.prompts import UncommittedChangesPrompt, WorktreePrompt

if TYPE_CHECKING:
    from claude_alamode.app import ChatApp
    from claude_alamode.agent import AgentSession

# Max retries for worktree cleanup before giving up
MAX_CLEANUP_ATTEMPTS = 3


def handle_worktree_command(app: "ChatApp", command: str) -> None:
    """Handle /worktree commands.

    Args:
        app: The ChatApp instance
        command: Full command string (e.g. "/worktree finish")
    """
    parts = command.split(maxsplit=2)

    if len(parts) == 1:
        _show_worktree_modal(app)
        return

    subcommand = parts[1]
    if subcommand == "finish":
        _handle_finish(app)
    elif subcommand == "cleanup":
        branches = parts[2].split() if len(parts) > 2 else None
        _handle_cleanup(app, branches)
    else:
        _switch_or_create_worktree(app, subcommand)


def _handle_finish(app: "ChatApp") -> None:
    """Handle /worktree finish command.

    Phase-based approach:
    1. Pre-flight: diagnose worktree status
    2. Resolution: handle uncommitted, merge/rebase
    3. Cleanup: remove worktree and branch
    """
    from claude_alamode.widgets import ChatMessage

    agent = app._agent
    if not agent:
        app.notify("No active agent", severity="error")
        return

    success, message, info = get_finish_info(app.sdk_cwd)
    if not success or info is None:
        app.notify(message, severity="error")
        return

    # Phase 1: Pre-flight diagnosis
    status = diagnose_worktree(info)

    # Store state on agent
    agent.finish_state = FinishState(
        info=info,
        phase=FinishPhase.RESOLUTION,
        status=status,
    )

    chat_view = app._chat_view
    if chat_view:
        user_msg = ChatMessage("/worktree finish")
        user_msg.add_class("user-message")
        chat_view.mount(user_msg)

    # Show status summary
    _show_finish_status(app, status)

    # Phase 2: Resolution
    _run_resolution(app, agent)


def _show_finish_status(app: "ChatApp", status: WorktreeStatus) -> None:
    """Show pre-flight status to user."""
    parts = []
    if status.commits_ahead == 0:
        parts.append("no commits")
    else:
        parts.append(f"{status.commits_ahead} commits")
    if status.is_merged:
        parts.append("already merged")
    if status.has_uncommitted:
        parts.append(f"{len(status.uncommitted_files)} uncommitted")
    if status.untracked_gitignored:
        parts.append(f"{len(status.untracked_gitignored)} gitignored")
    if status.untracked_other:
        parts.append(f"{len(status.untracked_other)} untracked")

    app.notify(f"Status: {', '.join(parts)}")


@work(group="finish", exclusive=True, exit_on_error=False)
async def _run_resolution(app: "ChatApp", agent: "AgentSession") -> None:
    """Run Phase 2: Resolution. May invoke Claude or prompt user."""
    from claude_alamode.widgets import ChatInput

    state = agent.finish_state
    if not state or not state.status:
        return

    # Loop to handle actions that can be resolved immediately
    while True:
        action = determine_resolution_action(state.status)

        if action == ResolutionAction.NONE:
            # Nothing to resolve, go to cleanup
            state.phase = FinishPhase.CLEANUP
            _run_cleanup(app, agent)
            return

        if action == ResolutionAction.CLEAN_GITIGNORED:
            # Auto-clean gitignored files (safe)
            app.notify("Cleaning gitignored files...")
            success, error = clean_gitignored_files(state.info.worktree_dir)
            if not success:
                app.notify(f"Failed to clean: {error}", severity="error")
                agent.finish_state = None
                return
            # Re-diagnose and loop
            state.status = diagnose_worktree(state.info)
            continue

        if action == ResolutionAction.PROMPT_UNCOMMITTED:
            # Ask user what to do
            prompt = UncommittedChangesPrompt(
                state.status.uncommitted_files,
                state.status.untracked_other,
            )
            async with app._show_prompt(prompt) as p:
                p.focus()
                choice = await p.wait()

            app.query_one("#input", ChatInput).focus()

            if choice == "abort" or choice is None:
                state.phase = FinishPhase.ABORTED
                agent.finish_state = None
                app.notify("Finish aborted")
                return

            if choice == "discard":
                app.notify("Discarding all changes...")
                success, error = discard_all_changes(state.info.worktree_dir)
                if not success:
                    app.notify(f"Failed to discard: {error}", severity="error")
                    agent.finish_state = None
                    return
                # Re-diagnose and loop
                state.status = diagnose_worktree(state.info)
                continue

            if choice == "commit":
                # Ask Claude to commit
                app._show_thinking()
                app.run_claude("Commit all changes with a descriptive message.")
                # Resolution will continue in on_response_complete_finish
                return

        if action == ResolutionAction.FAST_FORWARD:
            app.notify("Fast-forward merge...")
            success, error = fast_forward_merge(state.info)
            if success:
                state.phase = FinishPhase.CLEANUP
                _run_cleanup(app, agent)
            else:
                # Unexpected - fall back to Claude
                app._show_thinking()
                app.run_claude(f"Fast-forward merge failed: {error}\n\n" + get_finish_prompt(state.info))
            return

        if action == ResolutionAction.REBASE:
            # Claude handles rebase
            app._show_thinking()
            app.run_claude(get_finish_prompt(state.info))
            return

        # Unknown action - shouldn't happen
        return


def _run_cleanup(app: "ChatApp", agent: "AgentSession") -> None:
    """Run Phase 3: Cleanup. Bash only, Claude if it fails."""
    state = agent.finish_state
    if not state:
        return

    success, message = finish_cleanup(state.info)
    if success:
        _finish_complete(app, agent, message)
        return

    # Cleanup failed - ask Claude for help
    state.cleanup_attempts += 1
    state.last_error = message

    if state.cleanup_attempts >= MAX_CLEANUP_ATTEMPTS:
        app.notify(f"Cleanup failed after {MAX_CLEANUP_ATTEMPTS} attempts: {message}", severity="error")
        agent.finish_state = None
        return

    _show_cleanup_failure(agent, message)
    app._show_thinking()
    app.run_claude(get_cleanup_fix_prompt(message, state.info.worktree_dir))


def _show_cleanup_failure(agent: "AgentSession", error: str) -> None:
    """Display cleanup failure in chat."""
    from claude_alamode.widgets import ChatMessage

    state = agent.finish_state
    if not state:
        return

    chat_view = agent.chat_view
    if chat_view:
        msg = ChatMessage(f"[Cleanup attempt {state.cleanup_attempts}/{MAX_CLEANUP_ATTEMPTS} failed]\n\n{error}")
        msg.add_class("user-message")
        chat_view.mount(msg)


def _finish_complete(app: "ChatApp", agent: "AgentSession", warning: str = "") -> None:
    """Handle successful finish completion."""
    state = agent.finish_state
    if not state:
        return

    branch_name = state.info.branch_name
    agent.finish_state = None

    if warning:
        app.notify(f"Cleaned up {branch_name}{warning}", severity="warning")
    else:
        app.notify(f"Cleaned up {branch_name}")

    # Close the worktree agent if it exists
    worktree_agent = next(
        (a for a in app.agents.values() if a.worktree == branch_name),
        None
    )
    if worktree_agent and len(app.agents) > 1:
        # Only switch away if user is still on the agent being closed
        if app.active_agent_id == worktree_agent.id:
            main_agent = next(
                (a for a in app.agents.values() if a.worktree is None),
                None
            )
            if main_agent:
                app._switch_to_agent(main_agent.id)
        app._do_close_agent(worktree_agent.id)


def on_response_complete_finish(app: "ChatApp", agent: "AgentSession") -> None:
    """Called from on_response_complete when finish_state is set.

    Continues the finish process after Claude completes a task.
    """
    state = agent.finish_state
    if not state:
        return

    if state.phase == FinishPhase.RESOLUTION:
        # Re-diagnose to see if Claude fixed things
        state.status = diagnose_worktree(state.info)
        action = determine_resolution_action(state.status)

        if action == ResolutionAction.NONE:
            # Resolution complete, move to cleanup
            state.phase = FinishPhase.CLEANUP
            _run_cleanup(app, agent)
        else:
            # Still needs work - continue resolution
            _run_resolution(app, agent)

    elif state.phase == FinishPhase.CLEANUP:
        # Claude attempted to fix cleanup issue - retry
        _run_cleanup(app, agent)


def _switch_or_create_worktree(app: "ChatApp", feature_name: str) -> None:
    """Switch to existing worktree agent or create new one."""
    # Check if we already have an agent for this worktree
    for agent in app.agents.values():
        if agent.worktree == feature_name:
            app._switch_to_agent(agent.id)
            app.notify(f"Switched to {feature_name}")
            return

    # Check if worktree exists on disk
    existing = [wt for wt in list_worktrees() if wt.branch == feature_name]
    if existing:
        wt = existing[0]
        app._create_new_agent(feature_name, wt.path, worktree=feature_name, auto_resume=True)
    else:
        # Create new worktree
        success, message, new_cwd = start_worktree(feature_name)
        if success and new_cwd:
            app._create_new_agent(feature_name, new_cwd, worktree=feature_name, auto_resume=False)
        else:
            app.notify(message, severity="error")


def _handle_cleanup(app: "ChatApp", branches: list[str] | None) -> None:
    """Handle /worktree cleanup command."""
    results = cleanup_worktrees(branches)

    if not results:
        app.notify("No worktrees to clean up")
        return

    # Check if any need confirmation
    needs_confirm = [(b, msg) for b, _, msg, confirm in results if confirm]
    removed = [b for b, success, _, _ in results if success]
    failed = [(b, msg) for b, success, msg, confirm in results if not success and not confirm]

    # Report results
    for branch in removed:
        app.notify(f"Removed: {branch}")
    for branch, msg in failed:
        app.notify(f"Failed: {branch} - {msg}", severity="error")

    # Prompt for confirmation on dirty/unmerged
    if needs_confirm:
        _run_cleanup_prompt(app, needs_confirm)


@work(group="cleanup_prompt", exclusive=True, exit_on_error=False)
async def _run_cleanup_prompt(app: "ChatApp", needs_confirm: list[tuple[str, str]]) -> None:
    """Show prompt for confirming worktree removal."""
    from claude_alamode.widgets import SelectionPrompt, ChatInput

    branches_to_confirm = [b for b, _ in needs_confirm]
    options = [("all", f"Remove all ({len(needs_confirm)})")]
    options.extend((b, f"Remove {b} ({msg})") for b, msg in needs_confirm)
    options.append(("cancel", "Cancel"))

    async with app._show_prompt(SelectionPrompt("Worktrees with changes or unmerged:", options)) as prompt:
        prompt.focus()
        selected = await prompt.wait()

    if selected and selected != "cancel":
        to_remove = branches_to_confirm if selected == "all" else [selected]
        worktrees = list_worktrees()
        for branch in to_remove:
            wt = next((w for w in worktrees if w.branch == branch), None)
            if wt:
                success, msg = remove_worktree(wt, force=True)
                app.notify(f"Removed: {branch}" if success else msg, severity="error" if not success else "information")
    else:
        app.notify("Cleanup cancelled")

    app.query_one("#input", ChatInput).focus()




def _show_worktree_modal(app: "ChatApp") -> None:
    """Show worktree selection modal."""
    worktrees = [(str(wt.path), wt.branch) for wt in list_worktrees() if not wt.is_main]
    prompt = WorktreePrompt(worktrees)
    container = Center(prompt, id="worktree-modal")
    app.mount(container)
    _wait_for_worktree_selection(app, prompt, container)


@work(group="worktree", exclusive=True, exit_on_error=False)
async def _wait_for_worktree_selection(app: "ChatApp", prompt: WorktreePrompt, container: Center) -> None:
    """Wait for worktree modal selection and act on it."""
    try:
        result = await prompt.wait()
        container.remove()
        if result is None:
            return  # Cancelled

        action, value = result
        if action == "switch":
            # value is the path; find the branch name from worktrees
            worktrees = {str(wt.path): wt.branch for wt in list_worktrees()}
            branch = worktrees.get(value, Path(value).name)
            _switch_or_create_worktree(app, branch)
        elif action == "new":
            _switch_or_create_worktree(app, value)
    except Exception as e:
        app.show_error("Worktree selection failed", e)
