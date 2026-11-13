"""`MasterWindow` over a local mirror of the product's remote (GH-632, part 2c).

The mirror lives at `<root>/.mirror` (a `git clone --mirror`, built in `<root>/.mirror.clone`
and renamed, so a crash never leaves a half clone under the name that is trusted; later reads
`git fetch --prune`). Both names belong to this adapter: the rotation root's stages and
candidates are `<sha40>` and `<sha40>.partial`, never these.

The history is `git rev-list --first-parent <branch> -n 10`. Per commit the names of its CI
come from `stack_ci` (the stack as that commit declares it; part 2d wires the knowledge
context's parser in, because Process does not import Knowledge) and its CI read from the
injected `CommitCiReader`. A failure on one commit is that commit's `unknown` CI or `None`
ancestry; only a window-level failure (clone, fetch, rev-list, an empty history) is
`Unreadable`. Every git call runs under a timeout, and a timeout counts as a failure."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from ...application.ports import CommitCiReader
from ...domain import stage0_window
from ...domain.ci import CiUnreadable
from ...domain.rules import same_commit
from ...domain.stage0_rotation import CiState, Unreadable, WindowEntry

MIRROR_FETCH_TIMEOUT_S = 300
"""The longest a clone or a fetch of the mirror may take."""

GIT_READ_TIMEOUT_S = 30
"""The longest a local read (`rev-list`, `rev-parse`, `merge-base`) may take."""

WINDOW_SIZE = 10

MIRROR = ".mirror"
MIRROR_CLONE = ".mirror.clone"

Runner = Callable[..., subprocess.CompletedProcess[str]]


class GitMasterWindow:
    """`MasterWindow` over the git CLI; `run` is `subprocess.run`, injected so a test can fail
    or stall one call."""

    def __init__(
        self,
        root: Path,
        remote_url: str,
        ci: CommitCiReader,
        stack_ci: Callable[[str], tuple[str, ...]],
        branch: str = "master",
        run: Runner = subprocess.run,
    ) -> None:
        self.root = root
        self.remote_url = remote_url
        self.ci = ci
        self.stack_ci = stack_ci
        self.branch = branch
        self._run = run

    @property
    def mirror(self) -> Path:
        return self.root / MIRROR

    def entries(self, current: str | None) -> list[WindowEntry] | Unreadable:
        if not self._sync():
            return Unreadable()
        history = self._git("rev-list", "--first-parent", self.branch, "-n", str(WINDOW_SIZE))
        if history is None or history.returncode != 0:
            return Unreadable()
        shas = history.stdout.split()
        if not shas:
            return Unreadable()
        resolvable = current is None or self._resolves(current)
        return [
            WindowEntry(sha, self._ci_of(sha), self._descends(current, sha, resolvable))
            for sha in shas
        ]

    def _sync(self) -> bool:
        """Brings the mirror up to date: a fetch, or the first clone. False when it failed."""
        try:
            if self.mirror.exists():
                return _ok(self._git("fetch", "--prune", timeout=MIRROR_FETCH_TIMEOUT_S))
            partial = self.root / MIRROR_CLONE
            shutil.rmtree(partial, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            cloned = self._call(
                ["git", "clone", "--mirror", self.remote_url, str(partial)],
                cwd=self.root,
                timeout=MIRROR_FETCH_TIMEOUT_S,
            )
            if not _ok(cloned):
                shutil.rmtree(partial, ignore_errors=True)
                return False
            partial.rename(self.mirror)
            return True
        except OSError:
            return False

    def _ci_of(self, sha: str) -> CiState:
        """The CI state of `sha`: `unknown` when its stack or its CI read fails or says nothing.
        Both callables are injected and promise not to fail, but one commit's failure must
        never take the window down, whatever it raises."""
        try:
            names = self.stack_ci(sha)
        except Exception:  # an injected callable: any failure is this commit's unknown
            return "unknown"
        try:
            read = self.ci.read(sha)
        except Exception:  # an injected reader: any failure is this commit's unknown
            read = CiUnreadable("gh_unavailable")
        return stage0_window.ci_state(read, frozenset(names))

    def _resolves(self, current: str) -> bool:
        return _ok(self._git("rev-parse", "--verify", "--quiet", f"{current}^{{commit}}"))

    def _descends(self, current: str | None, sha: str, resolvable: bool) -> bool | None:
        """Whether `sha` strictly descends from `current`; None when git could not say."""
        if current is None:
            return True
        if not resolvable:
            return None
        done = self._git("merge-base", "--is-ancestor", current, sha)
        if done is None or done.returncode not in (0, 1):
            return None
        return done.returncode == 0 and not same_commit(sha, current)

    def _git(
        self, *args: str, timeout: int = GIT_READ_TIMEOUT_S
    ) -> subprocess.CompletedProcess[str] | None:
        return self._call(["git", *args], cwd=self.mirror, timeout=timeout)

    def _call(
        self, argv: Sequence[str], *, cwd: Path, timeout: int
    ) -> subprocess.CompletedProcess[str] | None:
        """One git subprocess under `timeout`; None when it could not run or timed out."""
        try:
            return self._run(
                list(argv),
                cwd=cwd,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return None


def _ok(done: subprocess.CompletedProcess[str] | None) -> bool:
    return done is not None and done.returncode == 0
