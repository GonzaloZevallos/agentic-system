"""Whether a commit is in the history of the commit this system runs (GH-1067).

`GitAncestry` reads the stage0 checkout it is given: `git cat-file -e <sha>^{commit}` (the
object must exist) and then `git merge-base --is-ancestor <sha> HEAD`. It never fetches and
never writes. Every doubt (a malformed sha, a missing object, a git error, a timeout) is
`None`, never `False`: `False` is only "present and not an ancestor"."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from .master_window import GIT_READ_TIMEOUT_S

_SHA = re.compile(r"[0-9a-f]{7,40}")

Runner = Callable[..., subprocess.CompletedProcess[str]]


class GitAncestry:
    """`FormatFixReads.contains` over the git CLI; `run` is `subprocess.run`, injected so a
    test can fail or stall one call."""

    def __init__(self, repo: Path, run: Runner = subprocess.run) -> None:
        self.repo = repo
        self._run = run

    def contains(self, sha: str) -> bool | None:
        if _SHA.fullmatch(sha) is None:
            return None
        present = self._call(["git", "cat-file", "-e", f"{sha}^{{commit}}"])
        if present is None or present.returncode != 0:
            return None
        done = self._call(["git", "merge-base", "--is-ancestor", sha, "HEAD"])
        if done is None or done.returncode not in (0, 1):
            return None
        return done.returncode == 0

    def _call(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        """One git subprocess under the read timeout; None when it could not run."""
        try:
            return self._run(
                list(argv),
                cwd=self.repo,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                check=False,
                timeout=GIT_READ_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return None
