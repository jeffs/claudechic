"""Git worktree management for isolated feature work."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorktreeState:
    """State for an active worktree session."""
    original_dir: Path
    worktree_dir: Path
    branch_name: str
    base_branch: str


_active_worktree: WorktreeState | None = None


def get_repo_name() -> str:
    """Get the current repository name."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True
    )
    return Path(result.stdout.strip()).name


def get_current_branch() -> str:
    """Get the current git branch."""
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def start_worktree(feature_name: str) -> tuple[bool, str, Path | None]:
    """
    Create a worktree for the given feature.

    Returns (success, message, worktree_path).
    """
    global _active_worktree

    if _active_worktree is not None:
        return False, f"Already in worktree for '{_active_worktree.branch_name}'. Run /worktree finish first.", None

    try:
        original_dir = Path.cwd()
        repo_name = get_repo_name()
        base_branch = get_current_branch()
        worktree_dir = original_dir.parent / f"{repo_name}-{feature_name}"

        if worktree_dir.exists():
            return False, f"Directory {worktree_dir} already exists", None

        # Create the worktree with a new branch
        subprocess.run(
            ["git", "worktree", "add", "-b", feature_name, str(worktree_dir), "HEAD"],
            check=True, capture_output=True, text=True
        )

        _active_worktree = WorktreeState(
            original_dir=original_dir,
            worktree_dir=worktree_dir,
            branch_name=feature_name,
            base_branch=base_branch,
        )

        return True, f"Created worktree at {worktree_dir}\nBranch: {feature_name}\nBase: {base_branch}", worktree_dir

    except subprocess.CalledProcessError as e:
        return False, f"Git error: {e.stderr}", None
    except Exception as e:
        return False, f"Error: {e}", None


def finish_worktree() -> tuple[bool, str, Path | None]:
    """
    Finish worktree: rebase, merge back, cleanup.

    Returns (success, message, original_path).
    """
    global _active_worktree

    if _active_worktree is None:
        return False, "No active worktree. Run /worktree start <name> first.", None

    state = _active_worktree
    messages = []

    try:
        # Check for uncommitted changes (run in worktree dir)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, check=True,
            cwd=state.worktree_dir
        )
        if result.stdout.strip():
            return False, "Uncommitted changes in worktree. Commit or stash first.", None

        # Fetch and rebase onto base branch
        subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True, cwd=state.worktree_dir)

        # Try to rebase - first check if remote branch exists
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"origin/{state.base_branch}"],
            capture_output=True, text=True, cwd=state.worktree_dir
        )
        if result.returncode == 0:
            rebase_result = subprocess.run(
                ["git", "rebase", f"origin/{state.base_branch}"],
                capture_output=True, text=True, cwd=state.worktree_dir
            )
            if rebase_result.returncode != 0:
                # Abort rebase and report
                subprocess.run(["git", "rebase", "--abort"], capture_output=True, cwd=state.worktree_dir)
                return False, f"Rebase conflict. Resolve manually:\n{rebase_result.stderr}", None
            messages.append("Rebased onto origin/" + state.base_branch)

        # Merge with fast-forward (in original dir)
        merge_result = subprocess.run(
            ["git", "merge", "--ff-only", state.branch_name],
            capture_output=True, text=True, cwd=state.original_dir
        )
        if merge_result.returncode != 0:
            return False, f"Fast-forward merge failed (branch diverged?):\n{merge_result.stderr}", None
        messages.append(f"Merged {state.branch_name} into {state.base_branch}")

        # Cleanup worktree and branch
        subprocess.run(
            ["git", "worktree", "remove", str(state.worktree_dir)],
            capture_output=True, text=True, check=True, cwd=state.original_dir
        )
        subprocess.run(
            ["git", "branch", "-d", state.branch_name],
            capture_output=True, text=True, check=True, cwd=state.original_dir
        )
        messages.append(f"Cleaned up worktree and branch")

        original_dir = state.original_dir
        _active_worktree = None
        return True, "\n".join(messages), original_dir

    except subprocess.CalledProcessError as e:
        return False, f"Git error: {e.stderr}", None
    except Exception as e:
        return False, f"Error: {e}", None


def get_worktree_status() -> str:
    """Get current worktree status."""
    if _active_worktree is None:
        return "No active worktree"
    return (
        f"Active worktree: {_active_worktree.branch_name}\n"
        f"Directory: {_active_worktree.worktree_dir}\n"
        f"Base branch: {_active_worktree.base_branch}"
    )


@dataclass
class WorktreeInfo:
    """Info about an existing worktree."""
    path: Path
    branch: str
    is_main: bool


def list_worktrees() -> list[WorktreeInfo]:
    """List all git worktrees for this repo."""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True, text=True, check=True
    )

    worktrees = []
    current_path = None
    current_branch = None

    for line in result.stdout.strip().split("\n"):
        if line.startswith("worktree "):
            current_path = Path(line[9:])
        elif line.startswith("branch refs/heads/"):
            current_branch = line[18:]
        elif line == "":
            if current_path and current_branch:
                # Main worktree is the one without a hyphenated name pattern
                main_repo = get_repo_name()
                is_main = current_path.name == main_repo
                worktrees.append(WorktreeInfo(current_path, current_branch, is_main))
            current_path = None
            current_branch = None

    # Handle last entry if no trailing newline
    if current_path and current_branch:
        main_repo = get_repo_name()
        is_main = current_path.name == main_repo
        worktrees.append(WorktreeInfo(current_path, current_branch, is_main))

    return worktrees
