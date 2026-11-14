"""`Stage0Candidate` over `git` and `uv` (GH-632, part 2d): builds `<root>/<sha>.partial` from the
mirror, smokes a stage, and `settle`s a promoted one. Every child goes through one injected
`Runner` and one environment (the system's own toolchain and `AGENTIC_*` removed, `TMPDIR` in
the scratch), with a timeout and a process-group kill. Nothing a child writes is captured,
logged or printed: a cause comes only from an exit code, an exception type or a timeout.

`settle` exists because a venv carries absolute paths (hatchling's editable `.pth` and the
shebangs): the one built in `<sha>.partial` does not work once `promote` renames it, so the
venv is rebuilt at the final path and run before `current` moves.

The smoke covers the stage's code and its own `.env`, not the unit's `Environment=` or
`EnvironmentFile=`."""

from __future__ import annotations

import errno
import os
import re
import shutil
import signal
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentic.shared.environment import without_own_configuration, without_own_toolchain

from ...application.ports import Scratch
from ...domain.stage0_rotation import CandidateCause, Green, Prepared, Red, Unavailable

CLONE_TIMEOUT_S = 600
RESOLVE_TIMEOUT_S = 60
"""The longest a local git step (checkout, set-url) may take."""
UV_SYNC_TIMEOUT_S = 900
HELP_TIMEOUT_S = 60
POLL_SMOKE_TIMEOUT_S = 300

_KILL_GRACE_S = 5.0
_PREFIX = re.compile(r"[0-9a-f]{7,40}")
_SHA40 = re.compile(r"[0-9a-f]{40}")
_FULL_DISK = (errno.ENOSPC, errno.EDQUOT)


@dataclass(frozen=True)
class TimedOut:
    """The child ran out of time and its process group was killed."""


class Runner(Protocol):
    def run(
        self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
    ) -> int | TimedOut | OSError:
        """Runs `argv` and returns its exit code, `TimedOut`, or the `OSError` that kept it
        from starting. Never returns, logs or prints what the child wrote."""
        ...


class SubprocessRunner:
    def run(
        self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float
    ) -> int | TimedOut | OSError:
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            return exc
        try:
            return process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self._kill_group(process)
            return TimedOut()

    @staticmethod
    def _kill_group(process: subprocess.Popen[bytes]) -> None:
        for signum, grace in ((signal.SIGTERM, _KILL_GRACE_S), (signal.SIGKILL, None)):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass  # it ended between the timeout and the kill
            try:
                process.wait(timeout=grace)
                return
            except subprocess.TimeoutExpired:
                continue


@dataclass(frozen=True)
class _Step:
    """One child: what to run, where, for how long, and the cause a failure of it has."""

    argv: tuple[str, ...]
    cwd: Path
    timeout_s: float
    cause: CandidateCause
    timeout_cause: CandidateCause


def _sync(stage: Path) -> _Step:
    return _Step(("uv", "sync", "--locked"), stage, UV_SYNC_TIMEOUT_S, "uv", "uv")


def _help(stage: Path) -> _Step:
    argv = (str(stage / ".venv" / "bin" / "agentic"), "--help")
    return _Step(argv, stage, HELP_TIMEOUT_S, "uv", "smoke_timeout")


def _poll(stage: Path, product: Path) -> _Step:
    argv = (
        str(stage / ".venv" / "bin" / "agentic"),
        "poll",
        "--product-dir",
        str(product),
        "--dry-run",
        "--once",
    )
    return _Step(argv, stage, POLL_SMOKE_TIMEOUT_S, "uv", "smoke_timeout")


def _git(cwd: Path, timeout_s: float, *args: str) -> _Step:
    return _Step(("git", *args), cwd, timeout_s, "clone", "clone")


class ShellStage0Candidate:
    def __init__(
        self,
        root: Path,
        scratch: Scratch,
        products: tuple[Path, ...],
        origin_url: str,
        runner: Runner | None = None,
    ) -> None:
        self._root = root
        self._scratch = scratch
        self._products = products
        self._origin_url = origin_url
        self._runner: Runner = runner if runner is not None else SubprocessRunner()

    def prepare(self, sha: str) -> Prepared | Unavailable:
        if _PREFIX.fullmatch(sha) is None:
            return Unavailable("clone")
        partial = self._root / f"{sha}.partial"
        steps = (
            _git(
                self._root,
                CLONE_TIMEOUT_S,
                "clone",
                "--no-checkout",
                str(self._root / ".mirror"),
                str(partial),
            ),
            _git(partial, RESOLVE_TIMEOUT_S, "checkout", "--detach", sha),
            _git(partial, RESOLVE_TIMEOUT_S, "remote", "set-url", "origin", self._origin_url),
        )
        failed = self._first_failure(steps)
        if failed is not None:
            return failed
        try:
            resolved = (partial / ".git" / "HEAD").read_text().strip()
            if _SHA40.fullmatch(resolved) is None:
                return Unavailable("clone")
            if resolved != sha:
                partial = partial.rename(self._root / f"{resolved}.partial")
        except OSError as exc:
            return Unavailable(_cause_of(exc, "clone"))
        failed = self._first_failure((_sync(partial),))
        return failed if failed is not None else Prepared(resolved)

    def smoke(self, stage: str) -> Green | Red | Unavailable:
        directory = self._root / stage
        steps = (_help(directory), *(_poll(directory, p) for p in self._products))
        for step in steps:
            result = self._run(step)
            if isinstance(result, Unavailable):
                return result
            if result != 0:
                return Red()
        return Green()

    def settle(self, sha: str) -> Green | Unavailable:
        stage = self._root / sha
        try:
            shutil.rmtree(stage / ".venv", ignore_errors=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return Unavailable(_cause_of(exc, "uv"))
        failed = self._first_failure((_sync(stage), _help(stage)))
        return failed if failed is not None else Green()

    def _first_failure(self, steps: Sequence[_Step]) -> Unavailable | None:
        """The first step that did not exit 0, as `Unavailable` of its cause, else None."""
        for step in steps:
            result = self._run(step)
            if isinstance(result, Unavailable):
                return result
            if result != 0:
                return Unavailable(step.cause)
        return None

    def _run(self, step: _Step) -> int | Unavailable:
        """The exit code of `step`, or the `Unavailable` of a timeout or an `OSError` (the
        scratch directory that cannot be made included)."""
        env = without_own_configuration(without_own_toolchain(os.environ))
        try:
            with self._scratch.open(None, "c", uuid.uuid4().hex, "stage0-rotate") as tmpdir:
                env["TMPDIR"] = str(tmpdir)
                result = self._runner.run(step.argv, step.cwd, env, step.timeout_s)
        except OSError as exc:
            result = exc
        if isinstance(result, TimedOut):
            return Unavailable(step.timeout_cause)
        if isinstance(result, OSError):
            return Unavailable(_cause_of(result, step.cause))
        return result


def _cause_of(error: OSError, step_cause: CandidateCause) -> CandidateCause:
    return "disk" if error.errno in _FULL_DISK else step_cause
