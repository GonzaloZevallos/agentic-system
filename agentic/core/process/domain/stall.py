"""The stalled attempt (GH-299, ADR-0056): a role attempt whose CLI hung, holding its
concurrency slot with nothing left to wait for.

One pure verdict over one snapshot: the adapter and the runner only feed it and obey it.
The runner keeps one mark of activity per attempt, `last_activity`, and the attempt is
stalled when it is older than the threshold. What renews the mark: every message of the
stream (the runner does that, it is not a snapshot fact), the output of a delivery hook,
a hook still in flight and, only while a main-thread tool call is pending, the CPU or
the state R/D of a marked child of the attempt. A fact that could not be read (`None`)
counts as activity: nothing is killed without evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# How often the watchdog looks, and the smallest threshold the configuration accepts
# (`Configuration.stall_timeout_s` states the same floor): well above the longest
# legitimate silence of a tool call with a child that is not working.
CHECK_EVERY_S = 30
STALL_FLOOR_S = 600

# The kind of the last message seen, derived from its type and nothing of its content.
LastEvent = Literal["assistant", "user", "system", "result", "stream_event", "none"]


@dataclass(frozen=True)
class ChildActivity:
    """What the marked children of an attempt (the CLI excluded) were doing at a scan:
    the CPU time they have spent in all, and whether any is running or in disk wait."""

    cpu_ticks: int
    running: bool


@dataclass(frozen=True)
class Stall:
    """How a stalled attempt is told: the silence in seconds and the last event seen."""

    seconds: int
    last_event: LastEvent


@dataclass(frozen=True)
class StallSnapshot:
    """The facts of one tick, on one monotonic clock. `hooks_in_flight` and
    `last_hook_exit` are the delivery hooks of this attempt alone."""

    now: float
    last_activity: float
    pending_tools: int
    hooks_in_flight: int
    last_hook_exit: float | None
    child: ChildActivity | None
    previous_child: ChildActivity | None
    timeout_s: float


@dataclass(frozen=True)
class Verdict:
    last_activity: float
    stalled: bool


def judge(snapshot: StallSnapshot) -> Verdict:
    """The renewed `last_activity` and whether the attempt is stalled."""
    last = snapshot.last_activity
    if snapshot.last_hook_exit is not None:
        last = max(last, snapshot.last_hook_exit)
    if snapshot.hooks_in_flight > 0 or (
        snapshot.pending_tools > 0 and _child_is_working(snapshot.child, snapshot.previous_child)
    ):
        last = snapshot.now
    return Verdict(last_activity=last, stalled=snapshot.now - last > snapshot.timeout_s)


def _child_is_working(child: ChildActivity | None, previous: ChildActivity | None) -> bool:
    if child is None or previous is None:
        return True
    return child.running or child.cpu_ticks != previous.cpu_ticks
