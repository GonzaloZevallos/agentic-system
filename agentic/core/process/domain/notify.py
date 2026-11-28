"""The pure core of the email notifier (GH-1056): what is owed a notice, never how it is sent.

Nothing calls this module yet: the poll use case that does is a later part of GH-1056.
`digest_event` decides the daily digest owed from `now`, `tz` and the record. `advance_read_at`
moves the activity window past what a digest consumed, never splitting a group of equal instants.
`release_event` decides, from facts a caller loads (`SplitFacts`), who announces a landing: a task
with a known parent is `covered`, and only a closed root lists its merged leaves (ADR-0106). It
imports no clock, no mail and no environment: `now` and `tz` are parameters, and every instant it
returns or keeps in a key is UTC. The epochs of a task's `pause_log` are read only through `rules`
(`current_epoch`, `previous_epoch`, `escalation_at`, `pause_kind`), never directly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import Enum
from typing import Literal

from . import rules
from .task import TaskState

__all__ = [
    "DEFAULT_DIGEST_HOUR",
    "NOTIFY_HORIZON",
    "NOTIFY_RETRY_AFTER",
    "SENDS_PER_ROUND",
    "DigestEvent",
    "DigestKey",
    "EventKey",
    "Judged",
    "NotifiedRecord",
    "NotifyKey",
    "NotifyKinds",
    "Outcome",
    "PartFact",
    "ReleaseEvent",
    "SplitFacts",
    "WaitingEvent",
    "advance_read_at",
    "digest_event",
    "digest_hour",
    "digest_instant",
    "due",
    "notify_kinds",
    "pending",
    "prune",
    "release_event",
    "retry_due",
    "row_settlement",
    "start",
    "waiting_event",
]

# How far back a notice is still owed, and how long a key is remembered.
NOTIFY_HORIZON = timedelta(days=7)
# How long after a failed round the next one may try again.
NOTIFY_RETRY_AFTER = timedelta(minutes=15)
# The most notices one round sends.
SENDS_PER_ROUND = 5
# The local hour of the daily digest when the setting is missing or invalid (`digest_hour`).
DEFAULT_DIGEST_HOUR = 20
_LAST_HOUR = 23
# One or two ASCII digits: a unicode digit or a sign is not an hour.
_HOUR_TEXT = re.compile(r"[0-9]{1,2}")

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


def _check_utc(name: str, instant: datetime) -> None:
    if instant.utcoffset() != timedelta(0):
        raise ValueError(f"{name} is not an aware UTC datetime: {instant!r}")


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
        _check_utc("instant", self.instant)


@dataclass(frozen=True)
class DigestKey:
    """The daily digest of one product for one local date. `digest_event` builds it; nothing calls
    that yet."""

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


class Judged(Enum):
    """The marker of an Activity row that was judged and owes nothing. A cursor marker only:
    `row_settlement` returns it and `advance_read_at` counts it as settled; it is never a key of
    `NotifiedRecord.keys` and never saved."""

    JUDGED = "judged"


@dataclass(frozen=True)
class NotifyKinds:
    """Which event kinds are enabled. Built by the caller; no text is parsed here."""

    waiting: bool
    release: bool
    digest: bool


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


def _unrecorded_due(
    key: NotifyKey, instant: datetime, record: NotifiedRecord, now: datetime
) -> bool:
    """The only composition of `due` and "its key is not yet recorded": `pending` and
    `digest_event` both read it."""
    return due(instant, record, now) and key not in record.keys


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


def advance_read_at(
    read_at: datetime | None,
    candidates: Sequence[tuple[datetime, NotifyKey | Judged | None]],
    *,
    cut_at: datetime | None,
) -> datetime | None:
    """Where the activity read resumes after a digest consumed `candidates` (`(instant, key)`, at
    most `limit` of them; a key means the row is settled in `record.keys`, for a digest only
    `sent`; `Judged.JUDGED` also counts as settled). `cut_at` is the instant of the `limit + 1`-th
    row when the read returned one. The result is the greatest keyed instant strictly before the
    earliest unkeyed one, else `read_at`; it depends only on the set of candidates. A group of
    equal instants is advanced past whole or not at all: a cut inside one leaves the whole group
    out, unless every remaining row shares `cut_at` (a single group, counted as "50+"). Rows at or
    before `read_at` are stale (`since` is exclusive). Rejects, never normalizes, a naive or
    non-UTC instant, a `cut_at` not after `read_at` and a `cut_at` earlier than a candidate.

    Caller precondition: a row older than `NOTIFY_HORIZON` (not `due`) is passed with a key or not
    passed at all, otherwise an unkeyed row stops the window for good. Accepted loss: a single
    group larger than the limit is consumed trimmed."""
    if read_at is not None:
        _check_utc("read_at", read_at)
    if cut_at is not None:
        _check_utc("cut_at", cut_at)
    for instant, _ in candidates:
        _check_utc("candidate instant", instant)
    if read_at is not None and cut_at is not None and cut_at <= read_at:
        raise ValueError(f"cut_at {cut_at!r} is not after read_at {read_at!r}")
    rows = [row for row in candidates if read_at is None or row[0] > read_at]
    if not rows:
        return read_at
    if cut_at is not None:
        if cut_at < max(instant for instant, _ in rows):
            raise ValueError(f"cut_at {cut_at!r} is earlier than a candidate")
        if any(instant != cut_at for instant, _ in rows):
            rows = [row for row in rows if row[0] < cut_at]
    unsettled = [instant for instant, settled in rows if settled is None]
    stop = min(unsettled) if unsettled else None
    advanced = [
        instant
        for instant, settled in rows
        if settled is not None and (stop is None or instant < stop)
    ]
    return max(advanced) if advanced else read_at


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
    `None`."""
    if _unrecorded_due(event.key, event.key.instant, record, now):
        return event.action
    return None


def notify_kinds(events: tuple[str, ...] | None) -> tuple[NotifyKinds, bool]:
    """The enabled kinds from the raw `events` setting, and whether any name was unknown.
    `None` enables waiting, release and digest; `()` none. Names are exact and case-sensitive;
    a repeated name changes nothing."""
    if events is None:
        return NotifyKinds(waiting=True, release=True, digest=True), False
    names = set(events)
    kinds = NotifyKinds(
        waiting="waiting" in names, release="release" in names, digest="digest" in names
    )
    return kinds, bool(names - {"waiting", "release", "digest"})


def digest_hour(raw: object) -> int:
    """The local hour of the digest from a raw setting: an `int` (not a bool) in 0..23, or a `str`
    of one or two ASCII digits with that value. Anything else reads as `DEFAULT_DIGEST_HOUR`, never
    normalized, and this never raises."""
    if isinstance(raw, bool):
        return DEFAULT_DIGEST_HOUR
    if isinstance(raw, str):
        if _HOUR_TEXT.fullmatch(raw) is None:
            return DEFAULT_DIGEST_HOUR
        raw = int(raw)
    if isinstance(raw, int) and 0 <= raw <= _LAST_HOUR:
        return raw
    return DEFAULT_DIGEST_HOUR


@dataclass(frozen=True)
class DigestEvent:
    """What `digest_event` found: the `key` of the digest and the UTC `instant` it is due.
    `key.local_date` is the local date of the `now` the computation used, never the local date of
    the resulting `instant`."""

    key: DigestKey
    instant: datetime


def digest_instant(now: datetime, tz: tzinfo, hour: int) -> datetime:
    """The UTC instant of the digest of the local date of `now` in `tz` at `hour` local, fold 0.
    Raises `ValueError` for a `now` that is not aware UTC or an `hour` outside 0..23."""
    if now.utcoffset() != timedelta(0):
        raise ValueError(f"now is not an aware UTC datetime: {now!r}")
    if not 0 <= hour <= _LAST_HOUR:
        raise ValueError(f"hour is not in 0..{_LAST_HOUR}: {hour!r}")
    local_date = now.astimezone(tz).date()
    return datetime.combine(local_date, time(hour), tzinfo=tz).replace(fold=0).astimezone(UTC)


def digest_event(
    now: datetime, tz: tzinfo, hour: int, record: NotifiedRecord, product: str
) -> DigestEvent | None:
    """The digest of `product` owed at `now`: today's (the local date of `now` in `tz`) at `hour`
    local, fold 0, converted to UTC before any comparison. `None` until that instant, when `due`
    does not hold (the activation or the horizon is not behind it) or when any outcome is already
    recorded for its key. Raises `ValueError` for a `now` that is not aware UTC or an `hour`
    outside 0..23.

    Only today's local date is considered: a poller down from the digest hour until past local
    midnight loses that day's digest. A digest is never unbuildable, covered or superseded.
    Nothing calls it yet."""
    instant = digest_instant(now, tz, hour)
    key = DigestKey(product, now.astimezone(tz).date())
    if instant <= now and _unrecorded_due(key, instant, record, now):
        return DigestEvent(key, instant)
    return None


def row_settlement(
    record: NotifiedRecord,
    *,
    instant: datetime,
    digest: DigestEvent | None,
    release: EventKey | None,
    judged: bool,
) -> NotifyKey | Judged | None:
    """The only rule of what settles an Activity row for the cursor (`advance_read_at`).

    | digest     | result                                                                     |
    |------------|----------------------------------------------------------------------------|
    | not `None` | `digest.key` iff the record holds it `sent` and `instant <= digest.instant`, |
    |            | else `None`; `release` and `judged` are ignored                            |
    | `None`     | `release` when recorded under any `Outcome`; else `JUDGED` if `judged`;    |
    |            | else `None`                                                                |

    Kinds: digest on (release on or off) -> the digest rule, `digest` being only the digest sent
    in this very round (its key already `sent`); digest off, release on -> the release/`JUDGED`
    rule; both off -> no Activity is read and this is not called. `JUDGED` is a cursor marker
    only: it is never written to the record."""
    if digest is not None:
        sent = record.keys.get(digest.key) == "sent" and instant <= digest.instant
        return digest.key if sent else None
    if release is not None and release in record.keys:
        return release
    return Judged.JUDGED if judged else None
