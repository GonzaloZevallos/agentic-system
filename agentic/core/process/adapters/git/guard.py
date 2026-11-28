"""The one check that a git command run for a task acts on that task's checkout (GH-1142).

A task folder under `.agentic/worktrees` whose `.git` file is gone is no checkout: a git
command run with it as its cwd walks up and finds the product checkout's `.git`, and writes
there. That is how GH-1107's integrate step left two merges on the product's master
(ADR-0002: the system never writes in the product checkout). Every git call whose path is
under the worktrees root, the system's and the Developer's alike, asks `require_checkout`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ...application.ports import GitError
from ...domain.worktree_sweep import task_of


class ForeignCheckout(GitError):
    """A task folder whose `.git` is not a regular file: `missing`, or `not_a_file` (a
    directory, which git would read as a repository of its own)."""

    def __init__(self, root: Path, reason: Literal["missing", "not_a_file"]):
        self.root = root
        self.reason = reason
        super().__init__(
            f"the task checkout {root} has no valid .git file ({reason}): "
            "git run from it would act on the product checkout"
        )


def require_checkout(path: Path, worktrees_root: Path) -> None:
    """Raise `ForeignCheckout` when `path` is inside a task folder under `worktrees_root`
    whose `.git` is not a regular file. A path outside `worktrees_root`, the root itself and
    a folder `task_of` does not recognise (`integration-<hex>`, ...) are never denied."""
    root = worktrees_root.resolve()
    try:
        parts = path.resolve().relative_to(root).parts
    except ValueError:
        return
    if not parts or task_of(parts[0]) is None:
        return
    folder = root / parts[0]
    gitfile = folder / ".git"
    if gitfile.is_file():
        return
    raise ForeignCheckout(folder, "not_a_file" if gitfile.exists() else "missing")
