"""`Workspace` over the git CLI in the product checkout (ADR-0011).

Layout, all under the product root and outside version control:

    .agentic/worktrees/<task>/            branch agentic/<task>, from the product's fetched upstream
    .agentic/worktrees/<task>.<subtask>/  branch agentic/<task>.<subtask>, from the task branch

Every operation is idempotent: asking for a checkout that exists returns it. The
worktrees and the checkpointer's file (ADR-0003) are excluded through
`.git/info/exclude`, never through the product's own `.gitignore`, which is the
product's business.

The commits are the system's, so they carry the system's identity, not whatever the
machine or the product has in `git config` (which may be nothing: a fresh box or a
background job). The identity travels as environment (`Configuration.git_identity`),
the same the roles that commit receive, so there is one way the system signs.

The one place that touches the network is `upstream()` (GH-180): a new task branch starts
from the fetched tip of the checkout's current branch, never from a stale local `HEAD`, and
never by pulling, checking out, resetting or fast-forwarding the human's checkout
(ADR-0002): `git fetch` writes only remote-tracking refs and objects.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from agentic.shared.configuration import LOGS_DIR, STATE_DIR, STATE_FILE

from ...application.ports import AddedAdr, AddedLine, Checkout, Upstream, UpstreamMerge
from ...application.ports import GitError as GitError  # re-exported: see ports.py
from ...application.ports import UnstageablePath as UnstageablePath
from ...domain import rules
from ...domain.ci import Containment
from ...domain.scratch import KEPT_DIR, LEGACY_SCRATCH_DIR
from ...domain.task import DependencyState, MergeOutcome

WORKTREES = Path(".agentic/worktrees")
# The glob covers SQLite's journal next to the file. `LOGS_DIR`: every invocation's log
# (README.md, 14; issue #33), through this same mechanism rather than the product's own
# `.gitignore`, which is the product's business.
# The knowledge index a role reads is written here on every attempt and never committed
# (ADR-0001 of the knowledge module); `core.knowledge`'s builder names the same directory.
INDEX_DIR = ".agentic/index"
TASKS_DIR = ".agentic/tasks"
# The scratch an older run left in the product (it lives outside every repository now,
# GH-397), and the evidence a task keeps (GH-391).
EXCLUDES = (
    f"{WORKTREES}/",
    f"{STATE_FILE}*",
    f"{STATE_DIR}/",
    f"{LOGS_DIR}/",
    f"{INDEX_DIR}/",
    f"{LEGACY_SCRATCH_DIR}/",
    f"{KEPT_DIR}/",
    # The task documents (GH-420): the system keeps them on the machine and never stages
    # them, so a product that does not track them never sees them as untracked either.
    f"{TASKS_DIR}/*.md",
)

# How long `git fetch` may take before its whole process group is killed (GH-180): the
# network blinking must cost a run a fallback and a note, never a hang.
FETCH_TIMEOUT_S: float = 30
# How long one `git merge-tree` may take (GH-255). Declared, not measured: it bounds one
# serial poll round, so a hung probe costs a conflict left unread, never a hung poller.
MERGE_TREE_TIMEOUT_S: float = 30
# How long one of the system's own `merge` / `worktree add` waits on a ref lock another
# git process holds, in milliseconds (GH-364): a Developer committing in a sibling worktree
# of the same `.git` holds `packed-refs` or a ref for a moment. Passed as `-c` options of
# the call itself, never written to the product's `.git/config`. Declared, not measured.
LOCK_TIMEOUT_MS = 5000
# How many times one of those calls is retried when git still reports a lock, waiting
# `LOCK_BACKOFF_S * 2**attempt` seconds before each retry.
LOCK_RETRIES = 4
# The two-letter codes `git status --porcelain` gives a path with an unresolved merge conflict.
UNMERGED_CODES = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD"})
LOCK_BACKOFF_S = 0.25
_LOCK_OPTIONS = (
    "-c",
    f"core.filesRefLockTimeout={LOCK_TIMEOUT_MS}",
    "-c",
    f"core.packedRefsTimeout={LOCK_TIMEOUT_MS}",
)
_LOCK_ERROR = re.compile(r"Unable to create '[^']*\.lock': File exists|cannot lock ref")
# At most this many left-out local commits are named in a note.
_LEFT_OUT_SHOWN = 10


# Prefix of the line that names a commit in `git log --format`: a control character no
# path or patch line starts with.
_COMMIT = "\x01"
# Output with every name verbatim and not quoted.
_PLAIN = ("-c", "core.quotepath=off")
_NUMBER = re.compile("[0-9]{4}")


# Where the tree a checkout's setup ran on is recorded, in the worktree's own git dir
# (ADR-0065); the Bash denial of the run-check tool names it.
SETUP_TREE_FILE = "agentic-setup-tree"
_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


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
            rules.task_branch(task_id), WORKTREES / task_id, start=self._new_task_base
        )

    def subtask_checkout(self, task_id: str, subtask_id: str) -> Checkout:
        task = self.task_checkout(task_id)
        return self._checkout(
            rules.subtask_branch(task_id, subtask_id),
            WORKTREES / f"{task_id}.{subtask_id}",
            start=lambda: task.branch,
        )

    def _checkout(self, branch: str, relative: Path, start: Callable[[], str]) -> Checkout:
        path = self.product_dir / relative
        self.exclude_system_files()
        if not self._branch_exists(branch):
            self._git_locked("branch", branch, start())
            base = self._git("rev-parse", "--short", branch)
            self._note_base(branch, base)
        # The worktree on disk and not the branch decides `created`: the directory is
        # what the product's setup prepares, and a branch may exist with no worktree
        # after a `cleanup` or a resume.
        created = not path.is_dir()
        if created:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._git_locked("worktree", "add", str(path), branch)
        return Checkout(branch=branch, path=path, base=self._base_of(branch), created=created)

    def head(self, path: Path) -> str:
        return self._git("rev-parse", "--short", "HEAD", cwd=path)

    def full_head(self, path: Path) -> str:
        return self._git("rev-parse", "HEAD", cwd=path)

    def tree(self, path: Path) -> str:
        return self._git("rev-parse", "HEAD^{tree}", cwd=path)

    def record_setup_tree(self, path: Path) -> None:
        # Best effort: a record that could not be written is a record that is missing, and
        # a missing record already means "no pass is stored" (ADR-0065). It must never
        # fail the checkout whose setup just passed.
        try:
            self._setup_tree_file(path).write_text(self.tree(path) + "\n", encoding="utf-8")
        except (OSError, GitError):
            return

    def setup_tree(self, path: Path) -> str | None:
        try:
            recorded = self._setup_tree_file(path).read_text(encoding="utf-8").strip()
        except (OSError, GitError):
            return None
        # Only an object id reaches git below: a record is a file under the checkout's git
        # dir, but nothing here trusts it to be one.
        if not _OBJECT_ID.fullmatch(recorded):
            return None
        kind = self._run("cat-file", "-t", recorded, cwd=path)
        return recorded if kind.returncode == 0 and kind.stdout.strip() == "tree" else None

    def _setup_tree_file(self, path: Path) -> Path:
        located = Path(self._git("rev-parse", "--git-path", SETUP_TREE_FILE, cwd=path))
        return located if located.is_absolute() else path / located

    def tree_changes(self, path: Path, old: str, new: str) -> tuple[str, ...]:
        # Two dots, and trees: `base...head` needs commits and a merge base, and what is
        # asked is only what differs between two contents.
        result = self._run("diff", "--name-only", "--no-renames", "-z", old, new, cwd=path)
        if result.returncode != 0:
            raise GitError(f"git diff {old} {new} failed: {result.stderr.strip()}")
        return tuple(name for name in result.stdout.split("\0") if name)

    def is_clean(self, path: Path) -> bool:
        return not self._git("status", "--porcelain", cwd=path)

    def differs_outside(self, path: Path, verified: str, head: str, excluded: str) -> bool:
        result = self._run("diff", "--quiet", verified, head, "--", ".", f":!{excluded}", cwd=path)
        # `--quiet` exits 0 for no difference and 1 for one; anything else is git failing.
        if result.returncode not in (0, 1):
            raise GitError(f"git diff {verified} {head} failed: {result.stderr.strip()}")
        return result.returncode == 1

    def restore_clean(self, path: Path) -> None:
        self._git("checkout", "--", ".", cwd=path)
        self._git("clean", "-fd", cwd=path)

    def is_committed(self, path: Path) -> bool:
        # The same flags `system_version` uses, so the two never disagree about a tree.
        return not self._git("status", "--porcelain", "--untracked-files=no", cwd=path)

    def merge(self, path: Path, branch: str) -> str | None:
        """A conflict is not an error of the system: the merge is undone so the
        checkout stays clean, and the reason goes back for a role to resolve by hand."""
        result = self._run_locked("merge", "--no-edit", "-q", branch, cwd=path)
        if result.returncode == 0:
            return None
        self._run("merge", "--abort", cwd=path)
        return (result.stdout + result.stderr).strip() or f"`git merge {branch}` failed"

    def build_slice(self, path: Path, master: str, heads: Sequence[str], branch: str) -> str | None:
        """See `Workspace.build_slice`. `checkout -B` puts `branch` on `master` in the
        checkout at `path`, and each head is merged in the order given. A merge that does
        not go through is aborted, the checkout goes back to where it was and `branch` is
        deleted: a slice that cannot be built leaves nothing behind, not a half-merged
        tree and not a branch that looks like one that was."""
        symbolic = self._run("symbolic-ref", "-q", "--short", "HEAD", cwd=path).stdout.strip()
        if symbolic == branch:  # the checkout of an earlier round: leave the branch, detached
            symbolic = ""
        origin = symbolic or self._git("rev-parse", "HEAD", cwd=path)
        self._git("checkout", "-q", "-B", branch, master, cwd=path)
        for head in heads:
            if self._run("merge", "--no-edit", "-q", head, cwd=path).returncode == 0:
                continue
            self._run("merge", "--abort", cwd=path)
            self._run("checkout", "-q", *(() if symbolic else ("--detach",)), origin, cwd=path)
            self._run("branch", "-D", branch, cwd=path)
            return None
        return self._git("rev-parse", "HEAD", cwd=path)

    def integration_paths(
        self, path: Path, heads: Sequence[str], task_head: str, *, master: str | None = None
    ) -> frozenset[str] | None:
        """See `Workspace.integration_paths`: every head merged, in a temporary worktree
        that is always removed, onto the merge-base of `task_head` and `master`; the paths
        that tree and `task_head` differ in are what the integrator did beyond the units."""
        tip = master or self.upstream().sha
        if tip is None:
            return None
        base = self._run("merge-base", task_head, tip, cwd=path)
        if base.returncode != 0 or not base.stdout.strip():
            return None
        name = f"integration-{uuid.uuid4().hex[:12]}"
        scratch = self.detached_checkout(name, base.stdout.strip())
        try:
            for head in heads:
                if self._run("merge", "--no-edit", "-q", head, cwd=scratch).returncode != 0:
                    return None
            out = self._git("diff", "--name-only", "--no-renames", "HEAD", task_head, cwd=scratch)
            return frozenset(line for line in out.splitlines() if line)
        finally:
            self.remove_checkout(name)

    def merge_keeping_conflicts(self, path: Path, branch: str) -> MergeOutcome:
        """Like `merge`, but never `merge --abort`: the checkout is left exactly as git
        left it, conflicted or refused, for the dependency-merge path in `develop.py`
        to hand a Developer (`Workspace.merge`'s own docstring, and this task's own
        addendum to ADR-0004, has the why the two stay separate)."""
        result = self._run_locked("merge", "--no-edit", "-q", branch, cwd=path)
        if result.returncode == 0:
            return MergeOutcome(reason=None)
        conflicted = tuple(
            line
            for line in self._git("diff", "--name-only", "--diff-filter=U", cwd=path).splitlines()
            if line
        )
        reason = (result.stdout + result.stderr).strip() or f"`git merge {branch}` failed"
        return MergeOutcome(reason=reason, conflicted_paths=conflicted)

    def merge_upstream_into(self, checkout: Path, upstream_sha: str) -> UpstreamMerge:
        outcome = self.merge_keeping_conflicts(checkout, upstream_sha)
        if outcome.reason is not None and not outcome.conflicted_paths:
            raise GitError(f"git merge {upstream_sha} was refused: {outcome.reason}")
        return UpstreamMerge(
            clean=outcome.reason is None,
            conflicted=outcome.conflicted_paths,
            head=self.full_head(checkout),
        )

    def merge_head(self, path: Path) -> str | None:
        found = self._run("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=path)
        sha = found.stdout.strip()
        return sha if found.returncode == 0 and sha else None

    def merge_in_progress(self, path: Path) -> bool:
        if self.merge_head(path) is not None:
            return True
        # Raw output: `_git` strips it, and the first column of a line is part of its code.
        status = self._run("status", "--porcelain", cwd=path)
        if status.returncode != 0:
            raise GitError(f"git status --porcelain failed: {status.stderr.strip()}")
        return any(line[:2] in UNMERGED_CODES for line in status.stdout.splitlines())

    def abort_merge(self, path: Path) -> None:
        if self.merge_head(path) is None:
            return
        self._git("merge", "--abort", cwd=path)

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
        keep_out = [f":(exclude){prefix}" for prefix in rules.UNSTAGEABLE]
        self._git("add", "-A", "--", ".", *keep_out, cwd=path)
        # Something force-added earlier is still in the index: refuse, never commit it.
        staged = self._git("diff", "--cached", "--name-only", cwd=path).splitlines()
        if refused := [relative for relative in staged if rules.unstageable(relative)]:
            raise UnstageablePath(refused)
        if not self._has_staged_changes(path):
            return None
        self._git("commit", "-q", "-m", message, cwd=path)
        return self.head(path)

    def commit_paths(self, path: Path, relatives: Sequence[str], message: str) -> str | None:
        if refused := [relative for relative in relatives if rules.unstageable(relative)]:
            raise UnstageablePath(refused)
        self._git("add", "--", *relatives, cwd=path)
        if not self._has_staged_changes(path, relatives):
            return None
        # With a pathspec `git commit` takes those paths only, whatever else is staged.
        self._git("commit", "-q", "-m", message, "--", *relatives, cwd=path)
        return self.head(path)

    def _has_staged_changes(self, path: Path, relatives: Sequence[str] = ()) -> bool:
        """`git diff --cached --quiet`: exit 0 is nothing staged, 1 is something; any other
        exit is a git failure and raises."""
        scope = ["--", *relatives] if relatives else []
        result = self._run("diff", "--cached", "--quiet", *scope, cwd=path)
        if result.returncode not in (0, 1):
            raise GitError(f"git diff --cached --quiet failed: {result.stderr.strip()}")
        return result.returncode == 1

    def push(self, branch: str, *, lease: str | None = None) -> None:
        if lease is None:
            self._git("push", "-u", "origin", branch)
            return
        # The sha is the caller's observation, never re-read here: re-reading would move
        # the lease to whatever the remote holds now and defeat it.
        self._git("push", "-u", f"--force-with-lease=refs/heads/{branch}:{lease}", "origin", branch)

    def remote_tip(self, path: Path, branch: str) -> str | None:
        listed = self._git("ls-remote", "--heads", "origin", f"refs/heads/{branch}", cwd=path)
        if not listed:
            return None
        observed = listed.split()[0]
        private = f"refs/agentic/remote-tips/{branch}"
        self._git("fetch", "origin", f"+refs/heads/{branch}:{private}", cwd=path)
        fetched = self._git("rev-parse", private, cwd=path)
        if fetched != observed:
            raise GitError(
                f"remote {branch} moved between ls-remote ({observed}) and fetch ({fetched})"
            )
        return fetched

    def publish_ci_ref(self, path: Path, ref: str) -> None:
        # No lease: the ref is the task's own and the run holds the task's lock.
        self._git("push", "--force", "origin", f"HEAD:refs/heads/{ref}", cwd=path)

    def remote_refs(self, prefix: str) -> tuple[str, ...]:
        listed = self._git("ls-remote", "--heads", "origin", f"refs/heads/{prefix}*")
        heads = "refs/heads/"
        return tuple(line.split()[1][len(heads) :] for line in listed.splitlines() if line.strip())

    def delete_remote_ref(self, ref: str) -> None:
        self._git("push", "origin", "--delete", ref)

    def show(self, ref: str, relative: str) -> str | None:
        result = self._run("show", f"{ref}:{relative}")
        return result.stdout if result.returncode == 0 else None

    def commit_date(self, ref: str) -> str | None:
        result = self._run("show", "-s", "--format=%cI", f"{ref}^{{commit}}")
        text = result.stdout.strip()
        if result.returncode != 0 or not text:
            return None
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
        return moment.astimezone(UTC).isoformat(timespec="milliseconds")

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

    def create_branch(self, name: str, sha: str) -> None:
        target = self._git("rev-parse", "--verify", f"{sha}^{{commit}}")
        if self._branch_exists(name):
            if self._git("rev-parse", f"refs/heads/{name}") == target:
                return  # the idempotent case: a retried restore made this ref already
            raise GitError(f"git branch {name} {sha} failed: {name} already points elsewhere")
        self._git("branch", name, target)  # never `-f`: a ref is created, not moved

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

    def contains(self, head: str, master: str) -> Containment:
        """See `Workspace.contains`: both shas are fetched first, and only `merge-base
        --is-ancestor`'s own two exits mean something -- 0 is `yes`, 1 is `no`. Anything
        else is `unknown`, which the caller counts as stale, never as fresh."""
        try:
            if not (self.fetch_commit(head) and self.fetch_commit(master)):
                return "unknown"
            result = self._run("merge-base", "--is-ancestor", master, head)
        except (GitError, OSError, subprocess.SubprocessError):
            return "unknown"
        return {0: "yes", 1: "no"}.get(result.returncode, "unknown")

    def merge_base(self, left: str, right: str) -> str:
        if not (self.fetch_commit(left) and self.fetch_commit(right)):
            raise GitError(f"git merge-base {left} {right} failed: a commit is not available")
        return self._git("merge-base", left, right)

    def merge_conflicts(self, head: str, master: str) -> bool | None:
        """See `Workspace.merge_conflicts`: both shas are fetched first, then `git
        merge-tree --write-tree` merges them in the object database alone -- no worktree,
        branch or index is touched. Only its own two exits mean something: 0 is clean,
        1 is a conflict. Anything else is `None`, which the caller counts as unread.
        Every git call here is bounded (`fetch_commit` is not: its `cat-file` has no
        timeout), so a stuck git costs a conflict left unread, never a hung poller."""
        try:
            if not (self._commit_bounded(head) and self._commit_bounded(master)):
                return None
            result = self._run_timed("merge-tree", "--write-tree", "--quiet", master, head)
        except (GitError, OSError, subprocess.SubprocessError):
            return None
        return {0: False, 1: True}.get(result.returncode)

    def _commit_bounded(self, sha: str) -> bool:
        """`fetch_commit` with every git call under a timeout: a timeout raises."""
        exists = ("cat-file", "-e", f"{sha}^{{commit}}")
        if self._run_timed(*exists).returncode == 0:
            return True
        self._run_bounded("fetch", "--quiet", "origin", sha)
        return self._run_timed(*exists).returncode == 0

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        out = self._git("diff", "--name-only", f"{base}...{head}")
        return tuple(line for line in out.splitlines() if line)

    def first_parent_commits(self, older: str, newer: str) -> tuple[rules.Commit, ...]:
        result = self._run(
            "rev-list", "--first-parent", "--parents", "--reverse", f"{older}..{newer}"
        )
        if result.returncode != 0:
            raise GitError(f"git rev-list {older}..{newer} failed: {result.stderr.strip()}")
        lines = (line.split() for line in result.stdout.splitlines() if line.strip())
        return tuple(rules.Commit(sha, tuple(parents)) for sha, *parents in lines)

    def gain_paths(self, older: str, newer: str) -> tuple[str, ...]:
        # Two-dot, `--no-renames` (a rename lists both sides, whatever `diff.renames` the
        # machine has) and `-z` with `core.quotepath` off (a name comes back as it is). The
        # output is not stripped: a name may end in a blank.
        result = self._run(
            *_PLAIN, "diff", "--name-only", "--no-renames", "-z", f"{older}..{newer}"
        )
        if result.returncode != 0:
            raise GitError(f"git diff {older}..{newer} failed: {result.stderr.strip()}")
        return tuple(path for path in result.stdout.split("\0") if path)

    def added_adrs(self, master: str, head: str) -> tuple[AddedAdr, ...]:
        # `--no-renames`: a file renamed into `docs/adr` is an addition of its new path
        # whatever `diff.renames` the machine has. The marker is a control character no
        # path holds; `core.quotepath` off keeps non-ASCII names as they are.
        out = self._git(
            *_PLAIN,
            "log",
            "--no-merges",
            "--no-renames",
            "--diff-filter=A",
            "--name-only",
            f"--format={_COMMIT}%H",
            f"{master}..{head}",
            "--",
            "docs/adr",
        )
        added: list[AddedAdr] = []
        commit = ""
        for line in out.splitlines():
            if line.startswith(_COMMIT):
                commit = line[len(_COMMIT) :]
            elif line:
                added.append(AddedAdr(line, commit))
        return tuple(added)

    def added_lines(self, master: str, head: str, numbers: Sequence[str]) -> tuple[AddedLine, ...]:
        for number in numbers:
            if not _NUMBER.fullmatch(number):
                raise ValueError(f"an ADR number is exactly four digits, not {number!r}")
        found: list[AddedLine] = []
        for index, number in enumerate(numbers):
            out = self._git(
                *_PLAIN,
                "log",
                "--no-merges",
                f"-G{number}",
                "-p",
                "--unified=0",
                f"--format={_COMMIT}%H",
                f"{master}..{head}",
            )
            # A line holding an earlier number came out of that number's own read.
            earlier = numbers[:index]
            found.extend(
                line
                for line in _patch_additions(out)
                if number in line.text and not any(other in line.text for other in earlier)
            )
        return tuple(found)

    def tree_names(self, ref: str, directory: str) -> tuple[str, ...]:
        out = self._git(*_PLAIN, "ls-tree", "--name-only", "-z", ref, "--", f"{directory}/")
        return tuple(name.rpartition("/")[2] for name in out.split("\0") if name)

    def fetch_commit(self, sha: str) -> bool:
        if self._run("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0:
            return True
        try:
            self._run_bounded("fetch", "--quiet", "origin", sha)
        except (OSError, subprocess.SubprocessError):
            return False
        return self._run("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0

    def checkout_path(self, name: str) -> Path:
        return self.product_dir / WORKTREES / name

    def detached_checkout(self, name: str, sha: str) -> Path:
        path = self.checkout_path(name)
        self.exclude_system_files()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git_locked("worktree", "add", "--detach", str(path), sha)
        return path

    def remove_checkout(self, name: str) -> None:
        path = self.checkout_path(name)
        self._run("worktree", "prune")
        self._run("worktree", "remove", "--force", str(path))
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        self._run("worktree", "prune")

    def checkout_names(self, prefix: str) -> list[str]:
        root = self.product_dir / WORKTREES
        if not root.is_dir():
            return []
        return sorted(
            path.name for path in root.iterdir() if path.is_dir() and path.name.startswith(prefix)
        )

    def _run_bounded(self, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=self.product_dir,
            env={**os.environ, **self.identity, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT_S,
        )

    def _run_timed(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.product_dir,
            env={**os.environ, **self.identity, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=MERGE_TREE_TIMEOUT_S,
        )

    def overlay(self, checkout: Path, sha: str, paths: Sequence[str]) -> None:
        """`git archive <sha> -- <paths>` extracted into `checkout` (GH-318). Only the named
        paths, as that commit holds them, and only inside the checkout: a path that is empty,
        absolute or climbs out is refused before git runs (an empty list would archive the
        whole tree), and the extraction filter refuses a member that lands or links outside."""
        if not paths:
            raise GitError("git archive needs at least one path to overlay")
        for relative in paths:
            parts = PurePosixPath(relative).parts
            if not relative or relative.startswith("/") or ".." in parts:
                raise GitError(f"overlay path {relative!r} leaves the checkout")
        result = subprocess.run(
            ["git", "archive", "--format=tar", sha, "--", *paths],
            cwd=self.product_dir,
            env={**os.environ, **self.identity},
            capture_output=True,
        )
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitError(f"git archive {sha} -- {' '.join(paths)} failed: {message}")
        try:
            with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
                archive.extractall(checkout, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise GitError(f"overlaying {' '.join(paths)} from {sha} failed: {exc}") from exc

    def upstream(self) -> Upstream:
        """See `Workspace.upstream`. The only caller of `_fetch`; every failure of git, of
        the network or of the machine (no `git` at all) comes back as a note."""
        try:
            return self._upstream()
        except (GitError, OSError, subprocess.SubprocessError) as exc:
            return Upstream("HEAD", None, f"could not resolve an upstream, no base ({exc})")

    def _upstream(self) -> Upstream:
        head = self._git("rev-parse", "HEAD")
        branch = self._run("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
        if not branch:
            return Upstream("HEAD", head, "detached HEAD has no upstream: starting from HEAD")
        found = self._git(
            "for-each-ref",
            "--format=%(upstream)|%(upstream:remotename)",
            f"refs/heads/{branch}",
        )
        ref, _, remote = found.partition("|")
        if not ref or not remote:
            return Upstream("HEAD", head, f"{branch} has no upstream: starting from HEAD")
        if remote == ".":
            return Upstream("HEAD", head, f"{branch} tracks a local branch: starting from HEAD")
        failure = self._fetch(remote)
        tip = self._run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").stdout.strip()
        if failure is None and tip:
            return Upstream(ref, tip, _left_out_note(self._left_out(ref), None))
        reason = failure or f"{ref} is not there after the fetch"
        # A failed fetch learnt nothing new about the remote: the last one we did is used
        # only when HEAD is behind it or diverged from it; a HEAD ahead of it, or no last
        # fetch at all, stays as it is today.
        if tip and self._run("merge-base", "--is-ancestor", tip, head).returncode != 0:
            return Upstream(
                ref, tip, _left_out_note(self._left_out(ref), f"fetch failed: {reason}")
            )
        return Upstream("HEAD", head, f"fetch failed ({reason}): starting from HEAD")

    def _left_out(self, ref: str) -> list[str]:
        """The short shas HEAD has that `ref` lacks: the commits a base from `ref` leaves out."""
        out = self._git("rev-list", "--abbrev-commit", f"{ref}..HEAD")
        return [line for line in out.splitlines() if line]

    def _fetch(self, remote: str) -> str | None:
        """`git fetch <remote>`, bounded: None when it worked, else why it did not. A
        branch ref locked by a concurrent fetch (another run, the human's own) is retried
        once. Never `pull`: only remote-tracking refs and objects are written."""
        code, stderr = self._fetch_once(remote)
        if code != 0 and code is not None and "cannot lock ref" in stderr:
            code, stderr = self._fetch_once(remote)
        if code == 0:
            return None
        if code is None:
            return f"timed out after {FETCH_TIMEOUT_S:g} s"
        first = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
        return first or f"git fetch exited {code}"

    def _fetch_once(self, remote: str) -> tuple[int | None, str]:
        """(exit code, stderr); the code is None when it ran out of time. Its own session
        and no stdin, so a credential prompt can never wait on a terminal nobody is at,
        and the whole process group (git and the ssh or helper it started) dies on time."""
        proc = subprocess.Popen(
            ["git", "fetch", "--quiet", remote],
            cwd=self.product_dir,
            env={**os.environ, **self.identity, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            _, stderr = proc.communicate(timeout=FETCH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            return None, ""
        return proc.returncode, stderr

    def _new_task_base(self) -> str:
        """Where a task branch that does not exist yet starts (called only then): the
        fetched upstream, its note on stderr, which a detached run's log keeps."""
        found = self.upstream()
        if found.note:
            print(f"agentic: {found.note}", file=sys.stderr)
        return found.sha or self._product_head()

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

    def write_index(self, path: Path, files: dict[str, str]) -> None:
        """Write the knowledge index into the checkout at `path`: every file of `files`
        (keys relative to `path`, all under `.agentic/index/`) atomically, the new ones
        before the stale ones are deleted, so a reader never finds a listed entry
        missing. A file already holding the same content is left alone."""
        root = path / INDEX_DIR
        wanted: dict[Path, str] = {}
        for relative, text in files.items():
            target = (path / relative).resolve()
            if not target.is_relative_to(root.resolve()) or Path(relative).is_absolute():
                raise ValueError(f"{relative}: an index file lives under {INDEX_DIR}/")
            wanted[path / relative] = text
        for target, text in wanted.items():
            if target.is_file() and target.read_text(encoding="utf-8") == text:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, target)
        if not root.is_dir():
            return
        for stale in sorted(root.rglob("*"), reverse=True):
            if stale.is_dir():
                with contextlib.suppress(OSError):  # not empty: it holds a listed file
                    stale.rmdir()
            elif stale not in wanted:
                stale.unlink()

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

    def _run_locked(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        """`_run` for the system's own `merge` and `worktree add` (GH-364): the call waits
        on a ref lock through `-c` options of its own, and a lock error that survives the
        wait is retried a bounded number of times with a growing pause. Any other outcome
        comes back at once, as it would from `_run`."""
        for attempt in range(LOCK_RETRIES + 1):
            result = self._run(*_LOCK_OPTIONS, *args, cwd=cwd)
            if result.returncode == 0 or not _LOCK_ERROR.search(result.stderr):
                return result
            if attempt < LOCK_RETRIES:
                time.sleep(LOCK_BACKOFF_S * 2**attempt)
        return result

    def _git_locked(self, *args: str, cwd: Path | None = None) -> str:
        result = self._run_locked(*args, cwd=cwd)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        result = self._run(*args, cwd=cwd)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()


def _patch_additions(log: str) -> list[AddedLine]:
    """The added lines of a `git log -p --format=<_COMMIT>%H` output, with their file and
    commit. A file's header (between `diff --git` and its first `@@`) is where its path
    is read; after that every line is a hunk line, so an added `++ x` is not a header."""
    lines: list[AddedLine] = []
    commit = path = ""
    in_header = False
    for raw in log.splitlines():
        if raw.startswith(_COMMIT):
            # Also inside a header: a binary file never reaches its `@@`.
            commit = raw[len(_COMMIT) :]
            in_header = False
        elif in_header:
            if raw.startswith("+++ "):
                target = raw[4:].split("\t")[0]
                path = target[2:] if target.startswith("b/") else target
            elif raw.startswith("@@"):
                in_header = False
        elif raw.startswith("diff --git "):
            in_header = True
        elif raw.startswith("+"):
            lines.append(AddedLine(path, commit, raw[1:]))
    return lines


def _left_out_note(left_out: list[str], failure: str | None) -> str | None:
    """What a base from the upstream leaves behind, by short sha, so a human who wonders
    where a local commit went finds it named; None when there is nothing to say."""
    parts = [failure] if failure else []
    if left_out:
        shown = ", ".join(left_out[:_LEFT_OUT_SHOWN])
        more = len(left_out) - _LEFT_OUT_SHOWN
        extra = f" and {more} more" if more > 0 else ""
        parts.append(f"local commits not on the upstream are left out of the base: {shown}{extra}")
    return "; ".join(parts) or None


def _key(branch: str) -> str:
    # Branch names with slashes are valid config subsections as they are.
    return branch
