"""A logind inhibitor lock for the lifetime of a run (GH-176).

`systemd-inhibit --what=sleep:shutdown --mode=block ... cat` holds the lock for as long
as its `cat` lives, and `cat` lives until its stdin closes. So the lock is the child
process and the context is the write end of its pipe: leaving the context, or the run
dying by any means, closes the pipe and releases the lock. It stops a graceful sleep or
shutdown while a run is alive; a forced poweroff or a power cut it cannot stop.

Best effort, like the registry write: a machine without systemd, a session without a bus
or a refusal by logind must never become a failed run. `hold` yields `None` when the lock
is held and the reason when it is not; it never raises."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

BINARY = "systemd-inhibit"
# How long to wait for the child to leave once its stdin is closed, before killing it.
RELEASE_TIMEOUT_S = 5.0
# How long to give a child that failed at once to be seen as already gone.
_SETTLE_S = 0.2


@contextmanager
def hold(why: str) -> Iterator[str | None]:
    """Holds the inhibitor while the context is open. Yields `None` if it is held, or
    the reason it is unavailable (the run goes on without it)."""
    found = shutil.which(BINARY)
    if found is None:
        yield f"{BINARY} not found"
        return
    try:
        child = subprocess.Popen(
            [
                found,
                "--what=sleep:shutdown",
                "--mode=block",
                "--who=agentic",
                f"--why={why}",
                "cat",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        yield f"{BINARY} could not start: {exc}"
        return
    try:
        child.wait(timeout=_SETTLE_S)
    except subprocess.TimeoutExpired:
        pass  # still running: the lock is held
    else:
        yield f"{BINARY} exited at once with code {child.returncode}"
        return
    try:
        yield None
    finally:
        _release(child)


def _release(child: subprocess.Popen[bytes]) -> None:
    try:
        if child.stdin is not None:
            child.stdin.close()
    except OSError:
        pass
    try:
        child.wait(timeout=RELEASE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
