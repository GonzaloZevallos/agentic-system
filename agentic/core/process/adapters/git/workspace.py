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
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agentic.shared.configuration import LOGS_DIR, STATE_DIR, STATE_FILE

from ...application.ports import Checkout, Upstream, UpstreamMerge
from ...application.ports import GitError as GitError  # re-exported: see ports.py
from ...application.ports import LeaseLost as LeaseLost  # re-exported: see ports.py
from ...domain import rules
from ...domain.added_lines import AddedLines
from ...domain.changes import ChangedPath, ChangeStatus
from ...domain.ci import Containment
from ...domain.task import DependencyState, MergeOutcome

WORKTREES = Path(".agentic/worktrees")
# The glob covers SQLite's journal next to the file. `LOGS_DIR`: every invocation's log
# (README.md, 14; issue #33), through this same mechanism rather than the product's own
# `.gitignore`, which is the product's business.
EXCLUDES = (f"{WORKTREES}/", f"{STATE_FILE}*", f"{STATE_DIR}/", f"{LOGS_DIR}/")

# How long `git fetch` may take before its whole process group is killed (GH-180): the
# network blinking must cost a run a fallback and a note, never a hang.
FETCH_TIMEOUT_S: float = 30
# At most this many left-out local commits are named in a note.
_LEFT_OUT_SHOWN = 10


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
            self._git("branch", branch, start())
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

    def full_head(self, path: Path) -> str:
        return self._git("rev-parse", "HEAD", cwd=path)

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

    def merge_upstream_into(self, checkout: Path, upstream_sha: str) -> UpstreamMerge:
        outcome = self.merge_keeping_conflicts(checkout, upstream_sha)
        if outcome.reason is not None and not outcome.conflicted_paths:
            raise GitError(f"git merge {upstream_sha} was refused: {outcome.reason}")
        return UpstreamMerge(
            clean=outcome.reason is None,
            conflicted=outcome.conflicted_paths,
            head=self.full_head(checkout),
        )

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

    def commit_paths(self, path: Path, relatives: Sequence[str], message: str) -> str | None:
        self._git("add", "--", *relatives, cwd=path)
        if not self._git("status", "--porcelain", "--", *relatives, cwd=path):
            return None
        # With a pathspec `git commit` takes those paths only, whatever else is staged.
        self._git("commit", "-q", "-m", message, "--", *relatives, cwd=path)
        return self.head(path)

    def push(self, branch: str, *, lease: str | None = None) -> None:
        if lease is None:
            self._git("push", "-u", "origin", branch)
            return
        # The sha is the caller's observation, never re-read here: re-reading would move
        # the lease to whatever the remote holds now and defeat it.
        self._git("push", "-u", f"--force-with-lease=refs/heads/{branch}:{lease}", "origin", branch)

    def push_commit(self, checkout: Path, sha: str, branch: str, *, lease: str) -> None:
        # Always leased, on the caller's sha, never one read here. `LC_ALL=C` so the one
        # sentence that tells a lost lease from any other refusal is git's own English.
        result = subprocess.run(
            [
                "git",
                "push",
                f"--force-with-lease=refs/heads/{branch}:{lease}",
                "origin",
                f"{sha}:refs/heads/{branch}",
            ],
            cwd=checkout,
            env={**os.environ, **self.identity, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        message = f"git push {sha} to {branch} failed: {result.stderr.strip()}"
        if "(stale info)" in result.stderr:
            raise LeaseLost(message)
        raise GitError(message)

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

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        # `-M` explicit: the list of a rename (the new path) does not depend on `diff.renames`.
        out = self._git("diff", "--name-only", "-M", f"{base}...{head}")
        return tuple(line for line in out.splitlines() if line)

    def changed_entries(self, base: str, head: str) -> tuple[ChangedPath, ...]:
        # No `-z`, as `changed_paths`: both quote an unusual name the same way, so the two
        # lists name the same paths. A rename has `old` and `new`; the new one is last.
        out = self._git("diff", "--name-status", "-M", f"{base}...{head}")
        entries = []
        for line in out.splitlines():
            if not line:
                continue
            fields = line.split("\t")
            code = fields[0]
            letter = code[:1]
            status = _CHANGE_STATUS.get(letter)
            scored = letter in ("R", "C") and code[1:].isdigit()
            if len(fields) < 2 or status is None or (code != letter and not scored):
                raise GitError(f"git diff {base}...{head}: unexpected status {line!r}")
            entries.append(ChangedPath(fields[-1], status))
        return tuple(entries)

    def merge_base(self, a: str, b: str) -> str:
        return self._git("merge-base", a, b)

    def paths_at(self, revision: str, directory: str) -> tuple[str, ...]:
        # `-z` so a name is never quoted; the entries are `<mode> <type> <sha>\t<path>`, and
        # a sub-directory is a `tree` entry, not one of the blobs asked for.
        out = self._git_bytes("ls-tree", "-z", revision, "--", f"{directory.rstrip('/')}/")
        found = []
        for entry in out.split(b"\0"):
            meta, _, name = entry.partition(b"\t")
            if name and meta.split(b" ")[1:2] == [b"blob"]:
                found.append(name.decode("utf-8", errors="replace"))
        return tuple(sorted(found))

    def added_lines(self, base: str, head: str) -> AddedLines:
        # The names come from `--name-status -z` (never quoted) and the lines from one
        # patch; with no rename detection and no pathspec both list the same files in the
        # same order, so the k-th `diff --git` section of the patch is the k-th name.
        listed = self._git_bytes("diff", "--name-status", "--no-renames", "-z", base, head)
        fields = [f.decode("utf-8", errors="replace") for f in listed.split(b"\0") if f]
        entries = list(zip(fields[0::2], fields[1::2], strict=True))
        patch = self._git_bytes(
            "diff", "--unified=0", "--no-renames", "--no-color", "--no-ext-diff", base, head
        ).decode("utf-8", errors="replace")
        sections = _patch_sections(patch)
        if len(sections) != len(entries):
            raise GitError(
                f"git diff {base} {head}: {len(sections)} patches for {len(entries)} paths"
            )
        lines: dict[str, tuple[tuple[int, str], ...]] = {}
        whole: set[str] = set()
        for (status, path), section in zip(entries, sections, strict=True):
            if any(row.startswith(("Binary files ", "GIT binary patch")) for row in section):
                continue
            if status.startswith("D"):
                continue
            if status.startswith("A"):
                whole.add(path)
            added = _added_in(section)
            if added:
                lines[path] = added
        return AddedLines(lines, frozenset(whole))

    def fetch_commit(self, sha: str) -> bool:
        if self._run("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0:
            return True
        try:
            self._run_bounded("fetch", "--quiet", "origin", sha)
        except (OSError, subprocess.SubprocessError):
            return False
        return self._run("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0

    def detached_checkout(self, name: str, sha: str) -> Path:
        path = self.product_dir / WORKTREES / name
        self.exclude_system_files()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "--detach", str(path), sha)
        return path

    def remove_checkout(self, name: str) -> None:
        path = self.product_dir / WORKTREES / name
        self._run("worktree", "prune")
        self._run("worktree", "remove", "--force", str(path))
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        self._run("worktree", "prune")

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

    def _git_bytes(self, *args: str) -> bytes:
        """The raw stdout: the verbs that read file names and file text decode it themselves,
        as UTF-8 with replacement, never as the machine's locale."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.product_dir,
            env={**os.environ, **self.identity},
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitError(f"git {' '.join(args)} failed: {stderr}")
        return result.stdout

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        result = self._run(*args, cwd=cwd)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()


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


_CHANGE_STATUS: dict[str, ChangeStatus] = {
    "A": "A",
    "M": "M",
    "D": "D",
    "R": "R",
    "C": "C",
    "T": "T",
}


def _patch_sections(patch: str) -> list[list[str]]:
    """The rows of each file's part of a `--unified=0` patch. A row that starts with
    `diff --git ` is always a header: every row of content starts with `+`, `-` or `\\`.
    Split on `\\n` only, never `splitlines`, which also breaks on separators inside a line."""
    sections: list[list[str]] = []
    for row in patch.split("\n"):
        if row.startswith("diff --git "):
            sections.append([])
        elif sections:
            sections[-1].append(row)
    return sections


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _added_in(section: list[str]) -> tuple[tuple[int, str], ...]:
    added: list[tuple[int, str]] = []
    number = 0
    for row in section:
        hunk = _HUNK.match(row)
        if hunk:
            number = int(hunk.group(1))
        elif number and row.startswith("+"):
            added.append((number, row[1:]))
            number += 1
    return tuple(added)


def _key(branch: str) -> str:
    # Branch names with slashes are valid config subsections as they are.
    return branch
