"""The only place that starts `gh` (GH-266, ADR-0050).

`run_gh` is what the tracker and the inbox call; `bounded_run` is their default `run`: a
`subprocess.run` that cannot wait forever. A `gh` call that stalls (the network, GitHub,
a credential helper nobody answers) used to hold the poller's round, and the registry
lock, for good; now it ends as `TrackerTimedOut` and the next round retries.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from ...application.ports import TrackerTimedOut

# Declared, not measured: a healthy call takes about a second. One constant to change if a
# real incident shows 60 s too long (no Configuration field, like FETCH_TIMEOUT_S).
GH_TIMEOUT_S: float = 60
# How long the wait for a killed `gh` to close its pipes and exit may take.
GH_DRAIN_S: float = 2

Run = Callable[..., subprocess.CompletedProcess[str]]


def print_line(line: str) -> None:
    """The default `log`: stdout, flushed, the stream the poller's round lines use, so a
    `--detach` log file gets it too."""
    print(line, flush=True)


def _kill_group(proc: subprocess.Popen[str]) -> None:
    # `start_new_session` made the child its own group leader: `gh` and whatever it
    # started (a credential helper, a pager) die together.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


def bounded_run(
    args: Sequence[str],
    *,
    cwd: Path | str | None,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    timeout_s: float = GH_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run` that gives up after `timeout_s` seconds by raising the
    `TimeoutExpired` of that wait, having killed the child's whole process group first.
    Writes nothing: the line belongs to `run_gh`. Its own session and no stdin, so a
    prompt never waits on a terminal nobody is at."""
    pipe = subprocess.PIPE if capture_output else None
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=pipe,
        stderr=pipe,
        text=text,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as expired:
        _kill_group(proc)
        try:
            expired.output, expired.stderr = proc.communicate(timeout=GH_DRAIN_S)
        except subprocess.TimeoutExpired:
            # Something that left the group still holds the pipes: stop reading them.
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=GH_DRAIN_S)
        raise expired from None
    except BaseException:
        _kill_group(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=GH_DRAIN_S)
        raise
    returncode = proc.returncode
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def run_gh(
    run: Run,
    args: Sequence[str],
    *,
    cwd: Path,
    not_installed: str,
    failed: Callable[[str], str],
    log: Callable[[str], None] = print_line,
) -> str:
    """One `gh <args>` call through `run`, its stdout stripped. Every failure is a
    `ValueError`: `not_installed` when there is no `gh`, `failed(stderr)` when it exits
    non-zero (each caller keeps its own wording), `TrackerTimedOut` when `run` raised
    `TimeoutExpired`, after exactly one line through `log` that names the call and the
    bound and nothing `gh` printed."""
    try:
        result = run(["gh", *args], cwd=cwd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise ValueError(not_installed) from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(failed(exc.stderr.strip())) from exc
    except subprocess.TimeoutExpired as exc:
        call = f"gh {' '.join(args[:2])} timed out after {exc.timeout:g} s"
        log(f"agentic: {call}; this read fails and the next round retries")
        raise TrackerTimedOut(call) from exc
    return result.stdout.strip()


__all__ = ["GH_DRAIN_S", "GH_TIMEOUT_S", "bounded_run", "print_line", "run_gh"]
