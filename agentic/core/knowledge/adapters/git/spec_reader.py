"""`SpecReader` over git (GH-594): a spec as a commit of the product repo holds it, read with
plumbing only and never from the worktree.

Every git call goes through `_run`, the one place a subprocess starts: an argv list, no shell,
the product directory as `cwd`, a timeout and a scrubbed environment. A refusal carries its
reason alone (`SpecUnreadable`): git's output, the path and the ids never leave this module.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from ....process.application.ports import SpecUnreadable
from ....process.domain.spec_delivery import SPEC_MAX_BYTES, spec_commit_ok, spec_path_ok

SPEC_GIT_TIMEOUT_S = 30.0

_REGULAR_FILE_MODES = frozenset({"100644", "100755"})


def _git_env(environ: Mapping[str, str]) -> dict[str, str]:
    """`environ` without any `GIT_*` key (a `GIT_DIR` or an index of the caller must not decide
    what is read), then the four settings that keep git inside the repo's own objects."""
    env = {key: value for key, value in environ.items() if not key.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """The only git subprocess of this module. Raises `SpecUnreadable("git")` on a timeout or
    an `OSError` and nothing else: every exit code returns, for the caller to read."""
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            shell=False,
            capture_output=True,
            timeout=SPEC_GIT_TIMEOUT_S,
            env=_git_env(os.environ),
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        raise SpecUnreadable("git") from None


class _Entry(NamedTuple):
    mode: str
    kind: str
    sha: str
    size: int | None


def _entries(raw: bytes) -> list[_Entry]:
    """The records of `git ls-tree -l -z`: `<mode> <kind> <sha> <size>\\t<path>`, the size padded
    with spaces and `-` for a tree or a gitlink. `SpecUnreadable("git")` on a malformed record."""
    entries: list[_Entry] = []
    try:
        for record in raw.decode("ascii").split("\0"):
            if not record:
                continue
            left, _, _path = record.partition("\t")
            mode, kind, sha, size = left.split()
            entries.append(_Entry(mode, kind, sha, None if size == "-" else int(size)))
    except ValueError:
        raise SpecUnreadable("git") from None
    return entries


class GitSpecReader:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    def read(self, commit: str, path: str) -> str:
        if not spec_path_ok(path):
            raise SpecUnreadable("path")
        if not spec_commit_ok(commit):
            raise SpecUnreadable("commit")
        self._require_commit(commit)
        blob = self._blob(commit, path)
        content = self._content(blob)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            raise SpecUnreadable("encoding") from None

    def _require_commit(self, commit: str) -> None:
        result = _run(
            ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{commit}^{{commit}}"],
            self.product_dir,
        )
        if result.returncode == 1:
            raise SpecUnreadable("commit")
        if result.returncode != 0:
            raise SpecUnreadable("git")
        if result.stdout.decode("ascii", errors="replace").strip() != commit:
            raise SpecUnreadable("commit")

    def _blob(self, commit: str, path: str) -> str:
        result = _run(["git", "ls-tree", "-l", "-z", commit, "--", path], self.product_dir)
        if result.returncode != 0:
            raise SpecUnreadable("git")
        entries = _entries(result.stdout)
        if not entries:
            raise SpecUnreadable("missing")
        if (
            len(entries) != 1
            or entries[0].kind != "blob"
            or entries[0].mode not in _REGULAR_FILE_MODES
        ):
            raise SpecUnreadable("not_blob")
        entry = entries[0]
        if entry.size is None:
            raise SpecUnreadable("git")
        if entry.size > SPEC_MAX_BYTES:
            raise SpecUnreadable("oversize")
        return entry.sha

    def _content(self, blob: str) -> bytes:
        result = _run(["git", "cat-file", "blob", blob], self.product_dir)
        if result.returncode != 0:
            raise SpecUnreadable("git")
        if len(result.stdout) > SPEC_MAX_BYTES:
            raise SpecUnreadable("oversize")
        return result.stdout
