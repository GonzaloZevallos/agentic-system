"""`Workspace` over the git CLI in the product checkout (ADR-0011).

Layout, all under the product root and outside version control:

    .agentic/worktrees/<task>/            branch agentic/<task>, from the product's HEAD
    .agentic/worktrees/<task>.<subtask>/  branch agentic/<task>.<subtask>, from the task branch

Every operation is idempotent: asking for a checkout that exists returns it. The
worktrees and the checkpointer's file (ADR-0003) are excluded through
`.git/info/exclude`, never through the product's own `.gitignore`, which is the
product's business.

The commits are the system's, so they carry the system's identity, not whatever the
machine or the product has in `git config` (which may be nothing: a fresh box or a
background job). The identity travels as environment (`Configuration.git_identity`),
the same the roles that commit receive, so there is one way the system signs.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from agentic.shared.configuration import STATE_FILE

from ...application.ports import Checkout
from ...domain import rules

WORKTREES = Path(".agentic/worktrees")
# The glob covers SQLite's journal next to the file.
EXCLUDES = (f"{WORKTREES}/", f"{STATE_FILE}*")


class GitError(RuntimeError):
    pass


class GitWorkspace:
    def __init__(self, product_dir: Path, identity: Mapping[str, str]):
        # `identity` is `Configuration.git_identity`, handed in by the composition root:
        # the one place that decides how the system signs, no default of its own here.
        self.product_dir = product_dir
        self.identity = dict(identity)

    # -- checkouts --------------------------------------------------------------

    def task_checkout(self, task_id: str) -> Checkout:
        return self._checkout(
            rules.task_branch(task_id), WORKTREES / task_id, start=self._product_head()
        )

    def subtask_checkout(self, task_id: str, subtask_id: str) -> Checkout:
        task = self.task_checkout(task_id)
        return self._checkout(
            rules.subtask_branch(task_id, subtask_id),
            WORKTREES / f"{task_id}.{subtask_id}",
            start=task.branch,
        )

    def _checkout(self, branch: str, relative: Path, start: str) -> Checkout:
        path = self.product_dir / relative
        self.exclude_system_files()
        if not self._branch_exists(branch):
            self._git("branch", branch, start)
            base = self._git("rev-parse", "--short", branch)
            self._note_base(branch, base)
        if not path.is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
            self._git("worktree", "add", str(path), branch)
        return Checkout(branch=branch, path=path, base=self._base_of(branch))

    def head(self, path: Path) -> str:
        return self._git("rev-parse", "--short", "HEAD", cwd=path)

    def is_clean(self, path: Path) -> bool:
        return not self._git("status", "--porcelain", cwd=path)

    def merge(self, path: Path, branch: str) -> None:
        self._git("merge", "--no-edit", "-q", branch, cwd=path)

    def commit_all(self, path: Path, message: str) -> str | None:
        self._git("add", "-A", cwd=path)
        if not self._git("status", "--porcelain", cwd=path):
            return None
        self._git("commit", "-q", "-m", message, cwd=path)
        return self.head(path)

    def push(self, branch: str) -> None:
        self._git("push", "-u", "origin", branch)

    def cleanup(self, task_id: str) -> None:
        root = self.product_dir / WORKTREES
        if not root.is_dir():
            return
        for path in root.iterdir():
            if path.name == task_id or path.name.startswith(f"{task_id}."):
                self._git("worktree", "remove", "--force", str(path))
        self._git("worktree", "prune")

    def branch_head(self, branch: str) -> str | None:
        if not self._branch_exists(branch):
            return None
        return self._git("rev-parse", "--short", branch)

    def base_of(self, branch: str) -> str | None:
        result = self._run("config", "--get", f"branch.{_key(branch)}.agenticBase")
        return result.stdout.strip() or None

    def worktrees(self, task_id: str) -> list[Path]:
        root = self.product_dir / WORKTREES
        if not root.is_dir():
            return []
        return sorted(
            path
            for path in root.iterdir()
            if path.is_dir() and (path.name == task_id or path.name.startswith(f"{task_id}."))
        )

    def prune(self) -> None:
        self._git("worktree", "prune")

    def exclude_system_files(self) -> None:
        """Keep what the system writes in the product out of its `git status`. Outside a
        repository there is nothing to exclude from."""
        found = self._run("rev-parse", "--git-common-dir")
        if found.returncode != 0:
            return
        common = Path(found.stdout.strip())
        if not common.is_absolute():
            common = self.product_dir / common
        exclude = common / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
        missing = [entry for entry in EXCLUDES if entry not in lines]
        if missing:
            exclude.write_text("\n".join([*lines, *missing]) + "\n", encoding="utf-8")

    # -- plumbing ---------------------------------------------------------------

    def _product_head(self) -> str:
        return self._git("rev-parse", "HEAD")

    def _branch_exists(self, branch: str) -> bool:
        result = self._run("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        return result.returncode == 0

    def _note_base(self, branch: str, base: str) -> None:
        # The commit a branch started from, kept in git's config: a review diffs
        # from it, and a resumed run must not lose it.
        self._git("config", f"branch.{_key(branch)}.agenticBase", base)

    def _base_of(self, branch: str) -> str:
        return self.base_of(branch) or self._git("rev-parse", "--short", branch)

    def _run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.product_dir,
            env={**os.environ, **self.identity},
            capture_output=True,
            text=True,
        )

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        result = self._run(*args, cwd=cwd)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()


def _key(branch: str) -> str:
    # Branch names with slashes are valid config subsections as they are.
    return branch
