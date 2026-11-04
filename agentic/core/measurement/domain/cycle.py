"""How long a task took, read over its document (GH-470): the cycle (first run started to
closed, or to the last run ended while it is open) and the active time (the union of its
run intervals, so overlapping Developers count once).

Pure domain, a read over `runs[]` and three scalar keys; nothing is recorded and neither
figure enters `scorecard.verdict`. A task missing a bound is `excluded`, never zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from agentic.core.process.domain.task import RoleRun, Status

from . import parallel_factor

CycleClass = Literal["closed", "open", "excluded"]

# The ends that are no delivery of their own: the run built nothing for a pull request to
# carry (a split parent, a bug ruled expected, a plan that is only tracker work).
NO_DELIVERY: frozenset[Status] = frozenset({"split", "discarded", "tracker_only"})


def _runs(document: Mapping[str, Any]) -> list[RoleRun]:
    return list(document.get("runs") or [])


def _moment(value: object) -> datetime | None:
    """An ISO-8601 timestamp as an aware datetime (naive reads as UTC), `None` when it is
    not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def cycle_class(document: Mapping[str, Any]) -> CycleClass:
    """Whether the task enters the time figures, and as what. In order: a task that is no
    delivery, or that its slices closed, is `excluded`; so is one with no run start; a
    `closed_at` makes it `closed`; else it is `open` when some run has ended, and
    `excluded` while the first is still running."""
    status: str = document.get("status", "open")
    if status in NO_DELIVERY or document.get("closed_by_slices"):
        return "excluded"
    runs = _runs(document)
    if not any(run.get("started_at") for run in runs):
        return "excluded"
    if document.get("closed_at"):
        return "closed"
    if any(run.get("ended_at") for run in runs):
        return "open"
    return "excluded"


def cycle_seconds(document: Mapping[str, Any]) -> float | None:
    """From the first `started_at` to `closed_at`, or to the last `ended_at` while open.
    `None` for an `excluded` task, an unreadable bound, or an end before the start."""
    klass = cycle_class(document)
    if klass == "excluded":
        return None
    runs = _runs(document)
    starts = [m for run in runs if (m := _moment(run.get("started_at")))]
    if not starts:
        return None
    if klass == "closed":
        end = _moment(document.get("closed_at"))
    else:
        ends = [m for run in runs if (m := _moment(run.get("ended_at")))]
        end = max(ends) if ends else None
    if end is None:
        return None
    seconds = (end - min(starts)).total_seconds()
    return seconds if seconds >= 0 else None


def active_seconds(document: Mapping[str, Any]) -> float | None:
    """The union of the run intervals that have both bounds, in seconds; `None` when no
    run has both (never zero in its place)."""
    intervals = [i for run in _runs(document) if (i := parallel_factor.interval(run))]
    if not intervals:
        return None
    try:
        return parallel_factor.union_seconds(intervals)
    except TypeError:  # a naive and an aware timestamp in one document cannot be ordered
        return None
