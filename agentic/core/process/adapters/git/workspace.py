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
from collections.abc import Mapping, Sequence
from pathlib import Path

from agentic.shared.configuration import LOGS_DIR, STATE_DIR, STATE_FILE

from ...application.ports import Checkout
from ...application.ports import GitError as GitError  # re-exported: see ports.py
from ...domain import rules
from ...domain.task import DependencyState, MergeOutcome

WORKTREES = Path(".agentic/worktrees")
# The glob covers SQLite's journal next to the file. `LOGS_DIR`: every invocation's log
# (README.md, 14; issue #33), through this same mechanism rather than the product's own
# `.gitignore`, which is the product's business.
EXCLUDES = (f"{WORKTREES}/", f"{STATE_FILE}*", f"{STATE_DIR}/", f"{LOGS_DIR}/")


class GitWorkspace:
    def __init__(self, product_dir: Path, identity: Mapping[str, str]):
        # `identity` is `Configuration.git_identity`, handed in by the composition root:
        # the one place that decides how the system signs, no default of its own here.
        self.product_dir = product_dir
        self.identity = dict(identity)

    # -- the repository ---------------------------------------------------------

    def repository_at(self, path: Path) -> Path | None:
        # `-C` and not `cwd=path`: a directory that is not there would raise out of
        # Popen instead of coming back as a failed command, and "is it a repository"
        # has to answer for a path that may not exist at all.
        # Absolute before anything: `-C` is read from the process's directory, and so
        # would a relative answer be, which are not the same place.
        target = path.resolve()
        try:
            result = self._run("-C", str(target), "rev-parse", "--git-common-dir")
        except OSError:
            # No git on the machine, or a `product_dir` that is not there: both are "no
            # repository" and neither is this question's to report. `default_ports` asks
            # it on every path now, so raising here would crash the composition root
            # before the caller ever gets to say what is actually wrong.
            return None
        if result.returncode != 0:
            return None
        common = Path(result.stdout.strip())
        # git answers relative to the checkout when the command ran inside it.
        return common if common.is_absolute() else (target / common).resolve()

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
        # The worktree on disk and not the branch decides `created`: the directory is
        # what the product's setup prepares, and a branch may exist with no worktree
        # after a `cleanup` or a resume.
        created = not path.is_dir()
        if created:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._git("worktree", "add", str(path), branch)
        return Checkout(branch=branch, path=path, base=self._base_of(branch), created=created)

    def head(self, path: Path) -> str:
        return self._git("rev-parse", "--short", "HEAD", cwd=path)

    def is_clean(self, path: Path) -> bool:
        return not self._git("status", "--porcelain", cwd=path)

    def is_committed(self, path: Path) -> bool:
        # The same flags `system_version` uses, so the two never disagree about a tree.
        return not self._git("status", "--porcelain", "--untracked-files=no", cwd=path)

    def merge(self, path: Path, branch: str) -> str | None:
        """A conflict is not an error of the system: the merge is undone so the
        checkout stays clean, and the reason goes back for a role to resolve by hand."""
        result = self._run("merge", "--no-edit", "-q", branch, cwd=path)
        if result.returncode == 0:
            return None
        self._run("merge", "--abort", cwd=path)
        return (result.stdout + result.stderr).strip() or f"`git merge {branch}` failed"

    def merge_keeping_conflicts(self, path: Path, branch: str) -> MergeOutcome:
        """Like `merge`, but never `merge --abort`: the checkout is left exactly as git
        left it, conflicted or refused, for the dependency-merge path in `develop.py`
        to hand a Developer (`Workspace.merge`'s own docstring, and this task's own
        addendum to ADR-0004, has the why the two stay separate)."""
        result = self._run("merge", "--no-edit", "-q", branch, cwd=path)
        if result.returncode == 0:
            return MergeOutcome(reason=None)
        conflicted = tuple(
            line
            for line in self._git("diff", "--name-only", "--diff-filter=U", cwd=path).splitlines()
            if line
        )
        reason = (result.stdout + result.stderr).strip() or f"`git merge {branch}` failed"
        return MergeOutcome(reason=reason, conflicted_paths=conflicted)

    def dependency_state(self, path: Path, branch: str, paths: Sequence[str]) -> DependencyState:
        ancestor = self._run("merge-base", "--is-ancestor", branch, "HEAD", cwd=path)
        merge_head = self._run("rev-parse", "--verify", "-q", "MERGE_HEAD", cwd=path)
        contained = ancestor.returncode == 0
        in_progress = merge_head.returncode == 0
        marked = tuple(p for p in paths if self._has_markers(path, p))
        evidence = ""
        if paths:
            evidence = self._run("diff", branch, "HEAD", "--", *paths, cwd=path).stdout
        return DependencyState(contained, in_progress, marked, evidence)

    def _has_markers(self, root: Path, relative: str) -> bool:
        target = root / relative
        if not target.is_file():
            return False
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return rules.has_conflict_markers(text)

    def commit_all(self, path: Path, message: str) -> str | None:
        self._git("add", "-A", cwd=path)
        if not self._git("status", "--porcelain", cwd=path):
            return None
        self._git("commit", "-q", "-m", message, cwd=path)
        return self.head(path)

    def push(self, branch: str, *, replace: bool = False) -> None:
        if replace:
            remote = self._git("ls-remote", "--heads", "origin", f"refs/heads/{branch}").split()
            if remote:
                lease = f"--force-with-lease=refs/heads/{branch}:{remote[0]}"
                self._git("push", "-u", lease, "origin", branch)
                return
        self._git("push", "-u", "origin", branch)

    def show(self, ref: str, relative: str) -> str | None:
        result = self._run("show", f"{ref}:{relative}")
        return result.stdout if result.returncode == 0 else None

    def cleanup(self, task_id: str) -> None:
        root = self.product_dir / WORKTREES
        if not root.is_dir():
            return
        for path in root.iterdir():
            if path.name == task_id or path.name.startswith(f"{task_id}."):
                self._git("worktree", "remove", "--force", str(path))
        self._git("worktree", "prune")

    def discard(self, path: Path) -> None:
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

    def branches(self, prefix: str) -> list[str]:
        result = self._run("branch", "--list", f"{prefix}*", "--format=%(refname:short)")
        return [line for line in result.stdout.splitlines() if line]

    def rename_branch(self, old: str, new: str) -> None:
        if not self._branch_exists(old) and self._branch_exists(new):
            return  # the idempotent case: a previous, interrupted archive did this already
        self._git("branch", "-m", old, new)

    def is_ignored(self, root: Path, relative: str) -> bool:
        result = self._run("check-ignore", "-q", relative, cwd=root)
        return result.returncode == 0

    def is_ancestor(self, path: Path, sha: str, ref: str = "HEAD") -> bool:
        """False, never `GitError`, for a `sha` this checkout has no object for (the
        launch predicate's own "ante la duda, no lanza", GH-137): `cat-file -e` is
        asked first, and `merge-base --is-ancestor` only runs once it confirms the
        object exists, so an unknown git object never reaches it. `merge-base
        --is-ancestor` itself only ever means 0 (is an ancestor) or 1 (is not); any
        other exit is a real git failure, raised as `GitError` instead of read as
        "launch anyway"."""
        exists = self._run("cat-file", "-e", f"{sha}^{{commit}}", cwd=path)
        if exists.returncode != 0:
            return False
        result = self._run("merge-base", "--is-ancestor", sha, ref, cwd=path)
        if result.returncode not in (0, 1):
            raise GitError(
                f"git merge-base --is-ancestor {sha} {ref} failed: {result.stderr.strip()}"
            )
        return result.returncode == 0

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
