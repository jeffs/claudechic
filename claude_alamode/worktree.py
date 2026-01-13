"""Git worktree management for isolated feature work."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorktreeInfo:
    """Info about an existing worktree."""
    path: Path
    branch: str
    is_main: bool


@dataclass
class FinishInfo:
    """Info needed to finish a worktree."""
    branch_name: str
    base_branch: str
    worktree_dir: Path
    main_dir: Path


def get_repo_name() -> str:
    """Get the current repository name."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True
    )
    return Path(result.stdout.strip()).name


def _is_main_worktree(worktree_path: Path) -> bool:
    """Check if a worktree is the main one (not a linked worktree).

    Main worktrees have .git as a directory; linked worktrees have .git as a file
    pointing to the main repo's .git/worktrees/<name>.
    """
    git_path = worktree_path / ".git"
    return git_path.is_dir()


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
                is_main = _is_main_worktree(current_path)
                worktrees.append(WorktreeInfo(current_path, current_branch, is_main))
            current_path = None
            current_branch = None

    # Handle last entry if no trailing newline
    if current_path and current_branch:
        is_main = _is_main_worktree(current_path)
        worktrees.append(WorktreeInfo(current_path, current_branch, is_main))

    return worktrees


def get_main_worktree() -> tuple[Path, str] | None:
    """Find the main worktree (non-feature) path and its branch."""
    for wt in list_worktrees():
        if wt.is_main:
            return wt.path, wt.branch
    return None


def start_worktree(feature_name: str) -> tuple[bool, str, Path | None]:
    """Create a worktree for the given feature.

    Returns (success, message, worktree_path).
    """
    try:
        repo_name = get_repo_name()

        # Find main worktree to put new worktree next to it
        main_wt = get_main_worktree()
        if main_wt:
            parent_dir = main_wt[0].parent
        else:
            parent_dir = Path.cwd().parent

        worktree_dir = parent_dir / f"{repo_name}-{feature_name}"

        if worktree_dir.exists():
            return False, f"Directory {worktree_dir} already exists", None

        # Create the worktree with a new branch
        subprocess.run(
            ["git", "worktree", "add", "-b", feature_name, str(worktree_dir), "HEAD"],
            check=True, capture_output=True, text=True
        )

        return True, f"Created worktree at {worktree_dir}", worktree_dir

    except subprocess.CalledProcessError as e:
        return False, f"Git error: {e.stderr}", None
    except Exception as e:
        return False, f"Error: {e}", None


def get_finish_info(cwd: Path | None = None) -> tuple[bool, str, FinishInfo | None]:
    """Get info needed to finish a worktree.

    Args:
        cwd: Current working directory (SDK's cwd). If None, uses Path.cwd().

    Returns (success, message, FinishInfo or None).
    """
    if cwd is None:
        cwd = Path.cwd()
    cwd = cwd.resolve()
    worktrees = list_worktrees()
    current_wt = next((wt for wt in worktrees if wt.path.resolve() == cwd), None)

    if current_wt is None or current_wt.is_main:
        return False, "Not in a feature worktree. Switch to a worktree first.", None

    main_wt = get_main_worktree()
    if main_wt is None:
        return False, "Cannot find main worktree.", None

    main_dir, base_branch = main_wt
    return True, "Ready to finish worktree", FinishInfo(
        branch_name=current_wt.branch,
        base_branch=base_branch,
        worktree_dir=current_wt.path,
        main_dir=main_dir,
    )


def get_finish_prompt(info: FinishInfo) -> str:
    """Generate the prompt for Claude to rebase and merge a feature branch."""
    return f"""Rebase and merge this feature branch:

Branch: {info.branch_name}
Base branch: {info.base_branch}
Worktree dir: {info.worktree_dir}
Main dir: {info.main_dir}

Steps:
1. Check for uncommitted changes in the worktree (fail if any)
2. Rebase {info.branch_name} onto {info.base_branch} (resolve any conflicts)
3. In the main dir ({info.main_dir}), merge {info.branch_name}:
   cd {info.main_dir} && git merge {info.branch_name}

Do NOT remove the worktree or delete the branch - the app will handle cleanup."""


def get_cleanup_fix_prompt(error: str, worktree_dir: Path) -> str:
    """Generate prompt for Claude to fix a cleanup failure."""
    return f"""The worktree cleanup failed with this error:

{error}

Worktree dir: {worktree_dir}

Please fix this issue (e.g., remove untracked files, resolve uncommitted changes) so the worktree can be removed cleanly."""


def finish_cleanup(info: FinishInfo) -> tuple[bool, str]:
    """Attempt to clean up a finished worktree.

    Returns (success, error_message). On success, error_message is empty.
    """
    # Try worktree removal
    result = subprocess.run(
        ["git", "worktree", "remove", str(info.worktree_dir)],
        cwd=info.main_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, result.stderr.strip()

    # Try branch deletion (less critical)
    result = subprocess.run(
        ["git", "branch", "-d", info.branch_name],
        cwd=info.main_dir, capture_output=True, text=True
    )
    branch_warning = "" if result.returncode == 0 else f" (branch not deleted: {result.stderr.strip()})"

    return True, branch_warning


def has_uncommitted_changes(worktree_path: Path) -> bool:
    """Check if a worktree has uncommitted changes."""
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "status", "--porcelain"],
        capture_output=True, text=True, check=True
    )
    return bool(result.stdout.strip())


def is_branch_merged(branch: str, into_branch: str = "main") -> bool:
    """Check if branch is merged into another branch."""
    result = subprocess.run(
        ["git", "branch", "--merged", into_branch],
        capture_output=True, text=True, check=True
    )
    merged = [b.strip().lstrip("* ") for b in result.stdout.strip().split("\n")]
    return branch in merged


def remove_worktree(worktree: WorktreeInfo, force: bool = False) -> tuple[bool, str]:
    """Remove a worktree and its branch. Returns (success, message)."""
    try:
        cmd = ["git", "worktree", "remove", str(worktree.path)]
        if force:
            cmd.append("--force")
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        delete_flag = "-D" if force else "-d"
        subprocess.run(
            ["git", "branch", delete_flag, worktree.branch],
            check=True, capture_output=True, text=True
        )
        return True, f"Removed {worktree.branch}"
    except subprocess.CalledProcessError as e:
        return False, f"Failed to remove {worktree.branch}: {e.stderr}"


def cleanup_worktrees(branches: list[str] | None = None) -> list[tuple[str, bool, str, bool]]:
    """Clean up worktrees.

    Args:
        branches: Specific branches to remove. If None, removes all safe worktrees.

    Returns:
        List of (branch_name, success, message, needs_confirmation).
        needs_confirmation=True means the branch has changes or is unmerged.
    """
    worktrees = list_worktrees()
    main_wt = get_main_worktree()
    main_branch = main_wt[1] if main_wt else "main"

    if branches is None:
        branches = [wt.branch for wt in worktrees if not wt.is_main]

    results = []
    for branch in branches:
        wt = next((w for w in worktrees if w.branch == branch), None)
        if wt is None:
            results.append((branch, False, f"No worktree for branch '{branch}'", False))
            continue
        if wt.is_main:
            results.append((branch, False, "Cannot remove main worktree", False))
            continue

        merged = is_branch_merged(branch, main_branch)
        dirty = has_uncommitted_changes(wt.path)

        if dirty or not merged:
            reason = []
            if dirty:
                reason.append("has uncommitted changes")
            if not merged:
                reason.append("not merged")
            results.append((branch, False, ", ".join(reason), True))
        else:
            success, msg = remove_worktree(wt)
            results.append((branch, success, msg, False))

    return results
