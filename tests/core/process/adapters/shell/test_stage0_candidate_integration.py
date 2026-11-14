"""`ShellStage0Candidate` against real `git` and a real offline `uv` (GH-632, part 2d): the proof
that `settle` repairs what `promote` breaks. A venv built in `<sha>.partial` carries absolute
paths, so after the rename to `<sha>` its `agentic` does not run; `settle` rebuilds it at the
final path. The mirror is a local `git clone --mirror` of this checkout's HEAD, and `uv` runs
offline against the warm cache. Marked `slow`: the affected-only tier leaves it out and the
full tier (GitHub CI) runs it. Without `uv` or a warm cache it fails under `CI` and skips
elsewhere."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.scratch import FilesystemScratch
from agentic.core.process.adapters.shell.stage0_candidate import (
    ShellStage0Candidate,
    SubprocessRunner,
    TimedOut,
)
from agentic.core.process.domain.stage0_rotation import Green, Prepared, Unavailable
from agentic.shared.environment import without_own_toolchain

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[5]
ORIGIN = "https://github.com/acme/system.git"
# Read at import: the autouse fixtures of `conftest` move `XDG_CACHE_HOME` per test, and the
# offline sync needs the cache the machine already warmed.
UV_CACHE = os.environ.get("UV_CACHE_DIR") or str(
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uv"
)


class NoProducts:
    def read(self) -> tuple[Path, ...]:
        return ()


class OfflineRunner:
    """The default runner with `UV_OFFLINE=1` and the warm cache added: the adapter strips every
    `UV_*`."""

    def __init__(self) -> None:
        self._inner = SubprocessRunner()

    def run(
        self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
    ) -> int | TimedOut | OSError:
        return self._inner.run(
            argv, cwd, {**env, "UV_OFFLINE": "1", "UV_CACHE_DIR": UV_CACHE}, timeout_s
        )


def unavailable(what: str, outcome: Unavailable) -> None:
    message = f"{what} unavailable ({outcome.cause}): uv missing or its cache is cold"
    if os.environ.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


def run_agentic_help(stage: Path) -> int:
    env = without_own_toolchain(os.environ)
    return subprocess.run(
        [str(stage / ".venv" / "bin" / "agentic"), "--help"],
        cwd=stage,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=120,
    ).returncode


def test_settle_makes_a_renamed_stage_run_from_its_final_path(tmp_path: Path) -> None:
    rotation = tmp_path / "stages"
    rotation.mkdir()
    git = ["git", "-c", "safe.directory=*"]
    subprocess.run(
        [*git, "clone", "--mirror", "-q", str(ROOT), str(rotation / ".mirror")], check=True
    )
    sha = subprocess.run(
        [*git, "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    scratch = FilesystemScratch(tmp_path / "scratch", "stage0-it", tmp_path)
    candidate = ShellStage0Candidate(rotation, scratch, NoProducts(), ORIGIN, OfflineRunner())

    prepared = candidate.prepare(sha)
    if isinstance(prepared, Unavailable):
        unavailable("prepare", prepared)
    assert prepared == Prepared(sha)
    os.rename(rotation / f"{sha}.partial", rotation / sha)
    stage = rotation / sha

    assert run_agentic_help(stage) != 0  # negative control: the paths still name the partial

    settled = candidate.settle(sha)
    if isinstance(settled, Unavailable):
        unavailable("settle", settled)
    assert settled == Green()
    assert run_agentic_help(stage) == 0
    prefix = subprocess.run(
        [str(stage / ".venv" / "bin" / "python"), "-c", "import sys; print(sys.prefix)"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(prefix).resolve().is_relative_to(stage.resolve())
    origin = subprocess.run(
        [*git, "-C", str(stage), "config", "--get", "remote.origin.url"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert origin == ORIGIN
