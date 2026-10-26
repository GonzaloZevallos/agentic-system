"""Who may start a check suite and with how many cores (GH-361, ADR-0065).

Every check suite assumes it owns the machine, so several gates at once multiply their
workers. The scheduler gives each gate a share of the machine's cores before it starts and
sizes its workers to that share. This module is the rules and nothing else: the ledger of
who holds what is the adapter's, and the core count and the time arrive as arguments, so
the rules are the same on every machine and in every test.

A `Cupo de chequeo` is the share a gate holds. A reserve (a quarter of the cores, at least
one) is held back for the closer runs, `integration` and `land`: a `subtask` gate never
takes cores from it, not even when it is overdue. The queue is ordered by closeness to done
(`land`, `integration`, `subtask`), then by arrival; only its head may take cores. A request
that waited `AGING_S` ranks as `land`, so subtask gates are not starved, but its priority
still decides what it may use.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import total_ordering

# Declared, not measured: seconds of waiting after which a request ranks as `land`.
AGING_S = 600.0

# Default minimum share: below it a pytest-xdist or Playwright run is mostly overhead.
SHARE_MIN = 2

# Declared, not measured: the longest a gate waits for a share before the overdue fallback.
WAIT_MAX_S = 1800.0

_RANK = {"subtask": 0, "integration": 1, "land": 2}


@total_ordering
class CheckPriority(Enum):
    """How close to done a check suite is; the closed set of the four construction sites."""

    SUBTASK = "subtask"
    INTEGRATION = "integration"
    LAND = "land"

    @property
    def rank(self) -> int:
        return _RANK[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CheckPriority):
            return NotImplemented
        return self.rank < other.rank


@dataclass(frozen=True)
class Entry:
    """One line of the ledger. `share` is 0 while the gate waits and `granted_at` is then
    `None`. Times are wall-clock seconds, the one clock every process on the machine shares."""

    product: str
    pid: int
    boot_id: str | None
    token: str
    priority: CheckPriority
    enqueued_at: float
    share: int
    granted_at: float | None
    expires_at: float
    overdue: bool = False


@dataclass(frozen=True)
class Request:
    """The gate asking: what `decide` needs to find it in the queue."""

    priority: CheckPriority
    enqueued_at: float
    pid: int
    token: str


@dataclass(frozen=True)
class Decision:
    granted: bool
    share: int = 0
    overdue: bool = False


WAIT = Decision(granted=False)


@dataclass(frozen=True)
class Limits:
    """The machine's cores and the dials over them. `share_max` and `share_max_subtask`
    are derived when unset: the cores outside the reserve, and half of those."""

    cores: int
    share_min: int = SHARE_MIN
    share_max: int | None = None
    share_max_subtask: int | None = None
    wait_max_s: float = WAIT_MAX_S

    @property
    def reserve(self) -> int:
        """A quarter of the cores, at least one, and never all of them: on a one-core
        machine a reserve would leave a subtask gate nothing, ever."""
        return max(0, min(max(1, self.cores // 4), self.cores - 1))

    @property
    def outside_reserve(self) -> int:
        return self.cores - self.reserve

    def cap(self, priority: CheckPriority) -> int:
        general = max(1, min(self.share_max or self.outside_reserve, self.cores))
        if priority is not CheckPriority.SUBTASK:
            return general
        derived = max(1, self.outside_reserve // 2)
        return min(general, self.share_max_subtask or derived)

    def floor(self, priority: CheckPriority) -> int:
        """The smallest share worth starting with: `share_min`, or the cap when that is
        smaller (a two-core machine caps a subtask gate at one)."""
        return min(self.share_min, self.cap(priority))


def alive(entry: Entry, now: float, is_alive: Callable[[int, str | None], bool]) -> bool:
    """Its holder is still there and its lease has not run out."""
    return now < entry.expires_at and is_alive(entry.pid, entry.boot_id)


def effective_rank(priority: CheckPriority, enqueued_at: float, now: float) -> int:
    if now - enqueued_at >= AGING_S:
        return CheckPriority.LAND.rank
    return priority.rank


def _booked(entries: Sequence[Entry], token: str) -> int:
    """Cores the others hold. An overdue `subtask` share is booked beyond the bound, never
    out of the reserve, so it does not count against what a closer run may take."""
    return sum(
        e.share
        for e in entries
        if e.token != token and not (e.overdue and e.priority is CheckPriority.SUBTASK)
    )


def _usable(entries: Sequence[Entry], request: Request, limits: Limits) -> int:
    free = max(0, limits.cores - _booked(entries, request.token))
    if request.priority is CheckPriority.SUBTASK:
        return max(0, free - limits.reserve)
    return free


def _normal_share(entries: Sequence[Entry], request: Request, limits: Limits) -> int:
    """The share the request would get if it may start; 0 when the cores it may use are
    below the floor."""
    share = min(limits.cap(request.priority), _usable(entries, request, limits))
    return share if share >= limits.floor(request.priority) else 0


def _is_head(entries: Sequence[Entry], request: Request, now: float) -> bool:
    def key(rank: int, enqueued_at: float, pid: int, token: str) -> tuple[int, float, int, str]:
        return (-rank, enqueued_at, pid, token)

    mine = key(
        effective_rank(request.priority, request.enqueued_at, now),
        request.enqueued_at,
        request.pid,
        request.token,
    )
    return all(
        mine < key(effective_rank(e.priority, e.enqueued_at, now), e.enqueued_at, e.pid, e.token)
        for e in entries
        if e.share == 0 and e.token != request.token
    )


def decide(entries: Sequence[Entry], request: Request, now: float, limits: Limits) -> Decision:
    """Grant the request a share or make it wait. Only the head of the queue may be
    granted, so a closer run that arrives later goes first and a smaller request behind a
    larger one never overtakes it."""
    if not _is_head(entries, request, now):
        return WAIT
    share = _normal_share(entries, request, limits)
    return Decision(granted=True, share=share) if share else WAIT


def overdue_decision(
    entries: Sequence[Entry], request: Request, now: float, limits: Limits
) -> Decision:
    """The fallback once the wait ran out: one try with the normal share and no regard for
    the queue; failing that, the minimum share, marked overdue. It is booked beyond the
    bound, so it is the one entry allowed to push the total over the cores."""
    share = _normal_share(entries, request, limits)
    if share:
        return Decision(granted=True, share=share)
    return Decision(granted=True, share=limits.floor(request.priority), overdue=True)
