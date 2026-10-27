"""How many Developer sessions of a task ran at once, read over its document (GH-364).

The factor is the Developer time summed over the wall time the task's subtask work
occupied: 1.0 is strictly one after another, 2.0 is two at a time throughout. A read over
`runs[]`, nothing recorded: no span attribute, metric, label or document key is added
(ADR-0037). Pure domain -- `eval compare` hands each task's runs here.

It is not the figure measured from session transcripts (1.26, issue #364): that one counts
tool waits inside a session, this one counts the recorded attempts, clock started after the
concurrency slot was acquired. Waits for a slot, pauses between invocations and any gap with
no Developer or review running are outside every interval, so none of them is counted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import DEVELOPER
from agentic.core.process.domain.task import INTEGRATION, RoleRun

Interval = tuple[datetime, datetime]


def _interval(run: RoleRun) -> Interval | None:
    """The run's `[started_at, ended_at]`, or `None` when it lacks either bound or one does
    not parse (a run recorded before the fields existed, or a hand-edited document)."""
    started, ended = run.get("started_at"), run.get("ended_at")
    if not started or not ended:
        return None
    try:
        return datetime.fromisoformat(started), datetime.fromisoformat(ended)
    except ValueError:
        return None


def _is_developer(run: RoleRun) -> bool:
    # `Developer` is the name of the subtask Developer and the integrator alike.
    return run.get("role") == DEVELOPER.name


def _is_subtask_review(run: RoleRun) -> bool:
    """A review of one subtask, not the integration's: `review_section` names the section."""
    section = run.get("section", "")
    return section.startswith(rules.REVIEW_PREFIX) and section != rules.review_section(INTEGRATION)


def _union_seconds(intervals: Iterable[Interval]) -> float:
    total = 0.0
    reach: datetime | None = None
    for start, end in sorted(intervals):
        if reach is None or start > reach:
            total += (end - start).total_seconds()
            reach = end
        elif end > reach:
            total += (end - reach).total_seconds()
            reach = end
    return total


def busy_seconds(document: Mapping[str, Any]) -> tuple[float, float]:
    """`(numerator, denominator)` of the task's factor, in seconds.

    The numerator sums the Developer runs' own durations (the integrator included); the
    denominator is the length of the union of those intervals and the subtask reviews'.
    Runs without both timestamps are ignored on both sides."""
    runs: list[RoleRun] = list(document.get("runs") or [])
    developers = [i for r in runs if _is_developer(r) and (i := _interval(r))]
    reviews = [i for r in runs if _is_subtask_review(r) and (i := _interval(r))]
    numerator = sum((end - start).total_seconds() for start, end in developers)
    return numerator, _union_seconds([*developers, *reviews])


def aggregate(documents: Iterable[Mapping[str, Any]]) -> float | None:
    """The factor over a set of tasks: the sum of numerators over the sum of denominators,
    so a long task weighs more than a short one. `None` when no task has any time."""
    numerator = denominator = 0.0
    for document in documents:
        n, d = busy_seconds(document)
        numerator += n
        denominator += d
    return numerator / denominator if denominator else None


def parallel_factor(document: Mapping[str, Any]) -> float | None:
    """The factor of one task, `None` when its denominator is zero."""
    return aggregate([document])
