"""The pure core of the email notifier (GH-1056): what is owed a notice, never how it is sent.

Nothing calls this module yet: the poll use case that does is a later part of GH-1056, and the
daily digest computation is a follow-up one. `release_event` decides, from facts a caller loads
(`SplitFacts`), who announces a landing: a task with a known parent is `covered`, and only a closed
root lists its merged leaves (ADR-0106). It imports no clock, no mail and no environment:
`now` and `tz` are parameters, and every instant it returns or keeps in a key is UTC. The epochs
of a task's `pause_log` are read only through `rules` (`current_epoch`, `previous_epoch`,
`escalation_at`, `pause_kind`), never directly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Literal

from . import rules
from .task import TaskState

__all__ = [
    "DEFAULT_DIGEST_HOUR",
    "NOTIFY_HORIZON",
    "NOTIFY_RETRY_AFTER",
    "SENDS_PER_ROUND",
    "DigestKey",
    "EventKey",
    "NotifiedRecord",
    "NotifyKey",
    "Outcome",
    "PartFact",
    "ReleaseEvent",
    "SplitFacts",
    "WaitingEvent",
    "due",
    "pending",
    "prune",
    "release_event",
    "retry_due",
    "start",
    "waiting_event",
]

# How far back a notice is still owed, and how long a key is remembered.
NOTIFY_HORIZON = timedelta(days=7)
# How long after a failed round the next one may try again.
NOTIFY_RETRY_AFTER = timedelta(minutes=15)
# The most notices one round sends.
SENDS_PER_ROUND = 5
# The local hour of the daily digest; read by the digest computation of the follow-up issue.
DEFAULT_DIGEST_HOUR = 20

# Restates `knowledge.domain.stack.SLUG`, which this domain may not import; a test compares them.
_PRODUCT_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

Outcome = Literal["sent", "superseded", "covered", "unbuildable"]
"""What became of a key. `sent`: the delivery, after a successful send. `superseded`:
`waiting_event`, when the pause it announces is already over. `covered`: `release_event`, when a
later notice already tells the same. `unbuildable`: the message building of an event, never of a
digest."""


def _check_product(product: str) -> None:
    if not isinstance(product, str) or _PRODUCT_SLUG.fullmatch(product) is None:
        raise ValueError(f"product is not a slug: {product!r}")


@dataclass(frozen=True)
class EventKey:
    """One event of one issue: `waiting` for a pause that needs a human, `release` for a landing,
    at the UTC `instant` that began it. Rejects, never normalizes, a bad value."""

    kind: Literal["waiting", "release"]
    product: str
    issue: int
    instant: datetime

    def __post_init__(self) -> None:
        if self.kind not in ("waiting", "release"):
            raise ValueError(f"unknown kind: {self.kind!r}")
        _check_product(self.product)
        if isinstance(self.issue, bool) or not isinstance(self.issue, int) or self.issue <= 0:
            raise ValueError(f"issue is not a positive integer: {self.issue!r}")
        if self.instant.utcoffset() != timedelta(0):
            raise ValueError(f"instant is not an aware UTC datetime: {self.instant!r}")


@dataclass(frozen=True)
class DigestKey:
    """The daily digest of one product for one local date. Nothing builds it yet: the follow-up
    issue does."""

    product: str
    local_date: date

    def __post_init__(self) -> None:
        _check_product(self.product)
        if isinstance(self.local_date, datetime) or not isinstance(self.local_date, date):
            raise ValueError(f"local_date is not a plain date: {self.local_date!r}")


NotifyKey = EventKey | DigestKey


@dataclass(frozen=True)
class NotifiedRecord:
    """What the notifier remembers, with no free text: `started_at` is the activation (nothing is
    due before it is set), `failed_at` the last failed round, `activity_read_at` where the
    activity read stopped, `keys` every key already settled and how."""

    started_at: datetime | None
    failed_at: datetime | None
    activity_read_at: datetime | None
    keys: Mapping[NotifyKey, Outcome]


@dataclass(frozen=True)
class WaitingEvent:
    """What `waiting_event` found: the key and whether to `send` the notice or mark it
    `superseded`."""

    key: EventKey
    action: Literal["send", "superseded"]


def _check_issue(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} is not a positive integer: {value!r}")


@dataclass(frozen=True)
class PartFact:
    """One part of a split, as read: its `issue`, whether it is `merged`, its `pr` when it has one
    and the issue it is a part `parent_of`. `merged` is asserted only from a completed read: a
    caller that could not read a part passes `split=None` to `release_event` instead of building
    this. Rejects, never normalizes, a bad value."""

    issue: int
    merged: bool
    pr: int | None
    parent_of: int

    def __post_init__(self) -> None:
        _check_issue("issue", self.issue)
        _check_issue("parent_of", self.parent_of)
        if self.pr is not None:
            _check_issue("pr", self.pr)
        if not isinstance(self.merged, bool):
            raise ValueError(f"merged is not a bool: {self.merged!r}")


_MAX_PARENTS = 2
_MAX_DEPTH = 2


@dataclass(frozen=True)
class SplitFacts:
    """The split tree around one task, as a caller read it: `parents` (open ancestors, at most
    two), `split_closed_at` (when the task's own split closed) and `parts` (the task's descendants,
    at most two levels). Rejects an incoherent tree at construction and never normalizes."""

    parents: tuple[int, ...]
    split_closed_at: datetime | None
    parts: tuple[PartFact, ...]

    def __post_init__(self) -> None:
        if len(self.parents) > _MAX_PARENTS:
            raise ValueError(f"more than {_MAX_PARENTS} parents: {self.parents!r}")
        for parent in self.parents:
            _check_issue("parent", parent)
        closed = self.split_closed_at
        if closed is not None and closed.utcoffset() != timedelta(0):
            raise ValueError(f"split_closed_at is not an aware UTC datetime: {closed!r}")
        if not self.parents and not self.parts and closed is not None:
            raise ValueError("split_closed_at is set with no parents and no parts")
        if self.parts:
            self._check_tree()

    def _check_tree(self) -> None:
        parent_of: dict[int, int] = {}
        for part in self.parts:
            if part.issue == part.parent_of:
                raise ValueError(f"part {part.issue} names itself")
            if part.issue in parent_of:
                raise ValueError(f"part {part.issue} appears twice")
            parent_of[part.issue] = part.parent_of
        if len(set(parent_of.values()) - parent_of.keys()) != 1:
            raise ValueError("parts do not hang from exactly one external parent_of")
        for issue in parent_of:
            seen = {issue}
            node = parent_of[issue]
            while node in parent_of:
                if node in seen:
                    raise ValueError(f"parts form a cycle at {node}")
                seen.add(node)
                node = parent_of[node]
            if len(seen) > _MAX_DEPTH:
                raise ValueError(f"tree is deeper than {_MAX_DEPTH} levels at {issue}")

    @property
    def external(self) -> int | None:
        """The one issue the parts hang from, or `None` with no parts."""
        issues = {part.issue for part in self.parts}
        names = {part.parent_of for part in self.parts} - issues
        return next(iter(names), None)


def start(record: NotifiedRecord, now: datetime) -> NotifiedRecord:
    """`record` activated at `now` when it never was; otherwise `record` itself."""
    if record.started_at is not None:
        return record
    return replace(record, started_at=now)


def _cutoff(now: datetime) -> datetime:
    """The only boundary of the horizon: `due` and `prune` both read it."""
    return now - NOTIFY_HORIZON


def due(instant: datetime, record: NotifiedRecord, now: datetime) -> bool:
    """Whether an event at `instant` is still owed: only with `started_at` set, and strictly after
    both the activation and the horizon, so old history never sends anything."""
    if record.started_at is None:
        return False
    return instant > max(record.started_at, _cutoff(now))


def prune(record: NotifiedRecord, now: datetime, tz: tzinfo) -> tuple[NotifiedRecord, int]:
    """`record` without the keys older than the horizon, and how many were dropped. An `EventKey`
    is kept from the cutoff instant on; a `DigestKey` from the cutoff's local date in `tz` on."""
    cutoff = _cutoff(now)
    cutoff_date = cutoff.astimezone(tz).date()
    kept: dict[NotifyKey, Outcome] = {}
    for key, outcome in record.keys.items():
        old = key.instant < cutoff if isinstance(key, EventKey) else key.local_date < cutoff_date
        if not old:
            kept[key] = outcome
    return replace(record, keys=kept), len(record.keys) - len(kept)


def retry_due(record: NotifiedRecord, now: datetime) -> bool:
    """Whether a round may run: without a failed one, or once `NOTIFY_RETRY_AFTER` has passed
    since it. A gate on the round, not on events, so it is not part of `pending`."""
    return record.failed_at is None or now - record.failed_at >= NOTIFY_RETRY_AFTER


def waiting_event(
    state: TaskState, product: str, issue: int, *, issue_closed: bool
) -> WaitingEvent | None:
    """The waiting event of a task, keyed by the `at` of its last escalation. A human pause that
    is current is `send`, or `superseded` when the issue is closed. An epoch with no escalation
    after a resume is `superseded` when the epoch it closed ended on a human escalation. Looks
    back exactly one epoch, and an external epoch (an exhausted format retake included) gives
    nothing. `superseded` is final for its key within the horizon: a reopened issue without a new
    escalation sends nothing."""
    current = rules.current_epoch(state)
    if (at := rules.escalation_at(current)) is not None:
        if rules.pause_kind(current) != "human":
            return None
        action: Literal["send", "superseded"] = "superseded" if issue_closed else "send"
        return WaitingEvent(EventKey("waiting", product, issue, at), action)
    previous = rules.previous_epoch(state)
    if (at := rules.escalation_at(previous)) is not None and rules.pause_kind(previous) == "human":
        return WaitingEvent(EventKey("waiting", product, issue, at), "superseded")
    return None


@dataclass(frozen=True)
class ReleaseEvent:
    """What `release_event` found: the key, and whether to `send` the notice (listing `leaves`,
    the merged leaf parts of a root, none for a task with no parent) or mark it `covered` by
    another one."""

    key: EventKey
    action: Literal["send", "covered"]
    leaves: tuple[PartFact, ...]


def release_event(
    state: TaskState,
    product: str,
    issue: int,
    split: SplitFacts | None,
    *,
    auto_merged_at: datetime | None,
) -> ReleaseEvent | None:
    """The release event of a task. `split=None` (facts that could not be read) gives `None`: it
    is never read as no parent. A task with a known parent is `covered` at its own close
    (`split_closed_at` when it has parts of its own, else `auto_merged_at`), an intermediate part
    included. A root emits `send` only once `split_closed_at` is set, listing its merged leaves,
    and nothing when it has none. A task with no parent and no parts emits `send` when it was
    auto-merged and touches UI or copy. Never calls `due`: `pending` does."""
    if split is None:
        return None
    if split.parts and split.external != issue:
        raise ValueError(f"parts hang from {split.external}, not from issue {issue}")
    if split.parents:
        instant = split.split_closed_at if split.parts else auto_merged_at
        if instant is None:
            return None
        return ReleaseEvent(EventKey("release", product, issue, instant), "covered", ())
    if split.parts:
        closed = split.split_closed_at
        if closed is None:
            return None
        named = {part.parent_of for part in split.parts}
        leaves = tuple(
            sorted(
                (part for part in split.parts if part.issue not in named and part.merged),
                key=lambda part: part.issue,
            )
        )
        if not leaves:
            return None
        return ReleaseEvent(EventKey("release", product, issue, closed), "send", leaves)
    if auto_merged_at is not None and rules.touches_person(state):
        return ReleaseEvent(EventKey("release", product, issue, auto_merged_at), "send", ())
    return None


def pending(
    event: WaitingEvent | ReleaseEvent, record: NotifiedRecord, now: datetime
) -> Literal["send", "superseded", "covered"] | None:
    """What `event` still owes: its action when it is `due` and its key is not yet recorded, else
    `None`. The only composition of these rules."""
    if due(event.key.instant, record, now) and event.key not in record.keys:
        return event.action
    return None
