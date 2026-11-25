"""The holds record a closed task's document keeps (GH-839): the intervals the issue spent under
the hold label. Pure: no I/O, and nothing outside the standard library and the domain's one
instant parser (`instant.parse_instant`, ADR-0108: an instant with no offset is unreadable).

Either the intervals, `{intervals: [{start, end}]}` (an empty list is read, no holds), or why they
are not known, `{unknown: unreadable | unpaired}`. This module is the one builder and the one
reader of that value: no caller writes `intervals`, `start`, `end` or `unknown` by hand, and
`measurement` interprets it only through `read`. A hold still open at the merge is clipped to the
merge, never unknown; an instant that cannot be read is `unreadable`, never a guess.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from .instant import parse_instant

HOLDS_KEY: Final = "holds"
HoldUnknownCause = Literal["unreadable", "unpaired"]


@dataclass(frozen=True)
class LabelEvent:
    """One addition or removal of the hold label, at an ISO-8601 instant."""

    at: str
    action: Literal["added", "removed"]


@dataclass(frozen=True)
class HoldUnknown:
    """The holds cannot be read: `cause` says whether the record is unreadable or its label
    events did not pair."""

    cause: HoldUnknownCause


@dataclass(frozen=True)
class Unreadable:
    """The marker a timeline reader hands `patch` when it could not read the whole timeline."""


def _unknown(cause: HoldUnknownCause) -> dict[str, Any]:
    return {"unknown": cause}


def from_events(events: Sequence[LabelEvent], merged_at: str) -> dict[str, Any]:
    """The record of a label timeline. Events are sorted by instant and those after `merged_at`
    are ignored; each addition pairs with the next removal; a hold open at the merge ends at
    `merged_at`. A removal with no open addition is `unpaired`; an addition while one is already
    open is ignored (the hold stays open); an instant that does not parse, `merged_at` included,
    is `unreadable`, and so is one with no offset (ADR-0108)."""
    merged = parse_instant(merged_at)
    if merged is None:
        return _unknown("unreadable")
    timed: list[tuple[datetime, int, LabelEvent]] = []
    for position, event in enumerate(events):
        at = parse_instant(event.at)
        if at is None:
            return _unknown("unreadable")
        timed.append((at, position, event))
    timed.sort(key=lambda item: (item[0], item[1]))
    intervals: list[dict[str, str]] = []
    opened: LabelEvent | None = None
    for at, _, event in timed:
        if at > merged:
            break
        if event.action == "added":
            opened = opened or event
        elif opened is None:
            return _unknown("unpaired")
        else:
            intervals.append({"start": opened.at, "end": event.at})
            opened = None
    if opened is not None:
        intervals.append({"start": opened.at, "end": merged_at})
    return {"intervals": intervals}


def patch(read: Sequence[LabelEvent] | Unreadable, merged_at: str) -> dict[str, Any]:
    """The document patch `{holds: record}` for a merge: `unreadable` when the timeline could not
    be read, else `from_events`."""
    if isinstance(read, Unreadable):
        return {HOLDS_KEY: _unknown("unreadable")}
    return {HOLDS_KEY: from_events(read, merged_at)}


def _interval(record: object) -> tuple[datetime, datetime] | None:
    """`(start, end)` of one stored interval, `None` when it is not a mapping, a bound is missing
    or does not parse, or the end is before the start."""
    if not isinstance(record, Mapping):
        return None
    start, end = parse_instant(record.get("start")), parse_instant(record.get("end"))
    if start is None or end is None or end < start:
        return None
    return start, end


def read(document: Mapping[str, Any]) -> tuple[tuple[datetime, datetime], ...] | HoldUnknown | None:
    """The held intervals of a document as aware `(start, end)` pairs (empty: no holds), a
    `HoldUnknown`, or `None` when the document has no holds key. The whole record is validated: one
    malformed shape or interval makes it `unreadable`, never a dropped interval."""
    if HOLDS_KEY not in document:
        return None
    record = document[HOLDS_KEY]
    if not isinstance(record, Mapping):
        return HoldUnknown("unreadable")
    has_intervals, has_unknown = "intervals" in record, "unknown" in record
    if has_intervals == has_unknown:
        return HoldUnknown("unreadable")
    if has_unknown:
        cause = record["unknown"]
        if cause == "unpaired":
            return HoldUnknown("unpaired")
        return HoldUnknown("unreadable")
    records = record["intervals"]
    if not isinstance(records, list):
        return HoldUnknown("unreadable")
    spans = [_interval(item) for item in records]
    if any(span is None for span in spans):
        return HoldUnknown("unreadable")
    return tuple(span for span in spans if span is not None)
