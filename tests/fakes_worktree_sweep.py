"""Fakes the worktree sweep tests and the rate-limit site table share: a run lock, a workspace
and a tracker with scripted failures, a stepping clock and a `world` of ports over them."""

from __future__ import annotations

import errno
from pathlib import Path

from agentic.core.process.application.ports import (
    GitError,
    IssueState,
    LockHeld,
    LockRecord,
)
from tests.fakes import MemoryTracker, MemoryWorkspace, memory_ports

CLOSED = IssueState("CLOSED", "COMPLETED", None, None, ())
OPEN = IssueState("OPEN", "", None, None, ())
DEAD = LockRecord(4242, "host", "2026-11-16T08:00:00Z", "agentic run")


class Lock:
    """A run lock: `held` names the tasks a live process holds, `dead` the ones whose dead
    record `acquire` takes over; every acquire and release is recorded."""

    def __init__(self, held: set[str] | None = None, dead: set[str] | None = None) -> None:
        self.held = held or set()
        self.dead = dead or set()
        self.events: list[tuple[str, str]] = []
        self.failure: OSError | None = None

    def acquire(self, task_id: str, command: str) -> LockRecord | None:
        self.events.append(("acquire", task_id))
        if self.failure is not None:
            raise self.failure
        if task_id in self.held:
            raise LockHeld(DEAD, f"{task_id} is running")
        return DEAD if task_id in self.dead else None

    def release(self, task_id: str) -> None:
        self.events.append(("release", task_id))


class Workspace(MemoryWorkspace):
    """The fake workspace with what a test scripts about a checkout: `unreadable` paths
    raise from `is_committed`; `names` is what is on disk."""

    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.stale_checkouts = list(names)
        self.unreadable: set[Path] = set()
        self.full_for: set[str] = set()
        self.full_errno = errno.EDQUOT

    def remove_checkout(self, name: str) -> None:
        if name in self.full_for:
            raise OSError(self.full_errno, "disk full")
        super().remove_checkout(name)

    def is_committed(self, path: Path) -> bool:
        if path in self.unreadable:
            raise GitError(f"git status in {path} failed")
        return super().is_committed(path)


class Tracker(MemoryTracker):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[int] = []
        self.failure: Exception | None = None

    def issue_state(self, number: int) -> IssueState:
        self.reads.append(number)
        if self.failure is not None:
            raise self.failure
        return super().issue_state(number)


class Clock:
    """A monotonic clock that moves `step` on each read."""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def world(*names: str, closed: tuple[int, ...] = (), opened: tuple[int, ...] = ()):
    workspace = Workspace(list(names))
    tracker = Tracker()
    for number in closed:
        tracker.states[number] = CLOSED
    for number in opened:
        tracker.states[number] = OPEN
    return memory_ports(workspace=workspace, tracker=tracker), workspace, tracker
