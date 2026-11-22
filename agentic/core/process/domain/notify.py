"""The pure core of the email notifier (GH-1056): what is owed a notice, never how it is sent.

Nothing calls this module yet: the poll use case that does is a later part of GH-1056, and the
daily digest computation is a follow-up one. It imports no clock, no mail and no environment:
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
    "WaitingEvent",
    "due",
    "pending",
    "prune",
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
    if (at := rules.escalation_at(current)) is not None and rules.pause_kind(current) == "human":
        action: Literal["send", "superseded"] = "superseded" if issue_closed else "send"
        return WaitingEvent(EventKey("waiting", product, issue, at), action)
    if not any(entry["kind"] == "escalation" for entry in current):
        previous = rules.previous_epoch(state)
        if (at := rules.escalation_at(previous)) is not None and (
            rules.pause_kind(previous) == "human"
        ):
            return WaitingEvent(EventKey("waiting", product, issue, at), "superseded")
    return None


def pending(
    event: WaitingEvent, record: NotifiedRecord, now: datetime
) -> Literal["send", "superseded"] | None:
    """What `event` still owes: its action when it is `due` and its key is not yet recorded, else
    `None`. The only composition of these rules."""
    if due(event.key.instant, record, now) and event.key not in record.keys:
        return event.action
    return None
