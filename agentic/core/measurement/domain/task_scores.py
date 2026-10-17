"""The scores of one finished task, the identity they are written under and the time they
are stamped with (GH-273, ADR-0037 addendum).

Langfuse widgets aggregate score values only, so the numbers a task's document records
become scores on a carrier trace of their own. Pure: documents and times in, values out.
The live path (at finish and at close) and the backfill call these same functions, so a
number is never computed twice in two ways; nothing here reads the clock, because a score
is stamped with the task's own recorded times, never with the moment of writing.

Identity is deterministic because Langfuse cannot be read back under `events_only`: the
only way a second write is not a duplicate is that it carries the same ids. It includes
the start of the run that produced it, so a fresh re-run or another eval arm never
overwrites another run's scores."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from agentic.core.process.domain.landing import FOLLOWUP_TITLE_PREFIX

from . import metrics

ScoreDataType = Literal["NUMERIC", "CATEGORICAL", "BOOLEAN"]

SCORE_NAMES: tuple[str, ...] = (
    "cost_usd",
    "cycle_h",
    "active_h",
    "returns",
    "failed_cost_usd",
    "delivered",
    "origin",
    "type",
)
"""The closed list of score names; no other name can be produced."""

SCOREABLE_STATUSES: frozenset[str] = frozenset({"ready_for_review", "discarded"})
UNKNOWN = "unknown"
"""What `release` and `baseline` read when the document records none (the close's own)."""

_SECONDS_PER_HOUR = 3600.0
_IDENTITY_PREFIX = "agentic-task"


@dataclass(frozen=True)
class Score:
    """One score. `value` is a number for `NUMERIC`, `1.0`/`0.0` for `BOOLEAN` and the
    category for `CATEGORICAL`. `id` is empty until the score is given an identity."""

    name: str
    value: float | str
    data_type: ScoreDataType
    id: str = ""

    def __post_init__(self) -> None:
        if self.name not in SCORE_NAMES:
            raise ValueError(f"{self.name!r} is not a task score")


@dataclass(frozen=True)
class TaskTime:
    """The span a task occupies, both ends aware."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class Dimensions:
    """What a widget filters by, read from the document."""

    release: str
    baseline: str
    arm: str


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Identity:
    """The ids of one task incarnation, derived by `identity` alone."""

    trace_id: str
    span_id: str

    def score_id(self, name: str) -> str:
        return _digest("agentic-score", self.trace_id, name)[:32]

    def event_id(self, score_id: str) -> str:
        return _digest("agentic-event", score_id)[:32]


def identity(product: str, task_id: str, start: datetime) -> Identity:
    """The ids of the carrier of the run of `task_id` in `product` that began at `start`.
    The same instant in any offset is the same identity."""
    seed = _digest(_IDENTITY_PREFIX, product, task_id, start.astimezone(UTC).isoformat())
    return Identity(trace_id=seed[:32], span_id=_digest("agentic-span", seed)[:16])


def _parse(value: object) -> datetime | None:
    """An ISO-8601 string as an aware datetime (naive reads as UTC); `None` when it is
    not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _runs(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(document.get("runs") or [])


def task_time(
    document: Mapping[str, Any], pr_created_at: str | None, issue_created_at: str | None
) -> TaskTime | None:
    """The task's own time: the earliest `started_at` and the latest `ended_at` of its
    runs; a document whose runs carry none (recorded before 2026-09-18) takes the pull
    request's `created_at`, then the issue's, for both ends. `None` when nothing is known;
    never the current time."""
    runs = _runs(document)
    starts = [t for r in runs if (t := _parse(r.get("started_at")))]
    ends = [t for r in runs if (t := _parse(r.get("ended_at")))]
    if starts:
        return TaskTime(min(starts), max(ends or starts))
    if ends:
        return TaskTime(min(ends), max(ends))
    for fallback in (pr_created_at, issue_created_at):
        if (moment := _parse(fallback)) is not None:
            return TaskTime(moment, moment)
    return None


def scoreable(document: Mapping[str, Any]) -> bool:
    """Whether the document is a finished invocation: `ready_for_review` or `discarded`.
    A paused or crashed one writes nothing."""
    return document.get("status") in SCOREABLE_STATUSES


def dimensions(document: Mapping[str, Any]) -> Dimensions:
    """Release, baseline and arm as the document records them, never as the writing
    process sees them."""
    version = document.get("system_version")
    baseline = document.get("system_baseline")
    return Dimensions(
        release=version if isinstance(version, str) and version else UNKNOWN,
        baseline=baseline if isinstance(baseline, str) and baseline else UNKNOWN,
        arm=metrics.arm_label(document.get("experiment") or {}),
    )


def _active_hours(document: Mapping[str, Any]) -> float | None:
    """Run time without queue wait, in hours; `None` when there is no run or any run lacks
    a readable timestamp (never `0` in its place)."""
    runs = _runs(document)
    if not runs:
        return None
    total = 0.0
    for run in runs:
        started, ended = _parse(run.get("started_at")), _parse(run.get("ended_at"))
        if started is None or ended is None:
            return None
        total += (ended - started).total_seconds() - float(run.get("queue_wait_s") or 0.0)
    return total / _SECONDS_PER_HOUR


def _cycle_hours(issue_created_at: str | None, pr_merged_at: str | None) -> float | None:
    created, merged = _parse(issue_created_at), _parse(pr_merged_at)
    if created is None or merged is None:
        return None
    return (merged - created).total_seconds() / _SECONDS_PER_HOUR


def task_scores(
    document: Mapping[str, Any],
    *,
    pr_merged_at: str | None = None,
    issue_created_at: str | None = None,
    identity: Identity | None = None,
) -> list[Score]:
    """The scores the document and the two tracker reads support, in `SCORE_NAMES` order.
    A score whose input is missing is left out, never written as `0` or `false`. With an
    `identity` each score carries its id."""
    values: dict[str, tuple[float | str | None, ScoreDataType]] = {
        "cost_usd": (float(document.get("cost_usd") or 0.0), "NUMERIC"),
        "cycle_h": (_cycle_hours(issue_created_at, pr_merged_at), "NUMERIC"),
        "active_h": (_active_hours(document), "NUMERIC"),
        "returns": (metrics.changes_requested_total(document), "NUMERIC"),
        "failed_cost_usd": (
            sum(float(f.get("cost_usd") or 0.0) for f in document.get("failures") or []),
            "NUMERIC",
        ),
        "delivered": (_delivered(document), "BOOLEAN"),
        "origin": (_origin(document), "CATEGORICAL"),
        "type": (document.get("type") or None, "CATEGORICAL"),
    }
    return [
        Score(
            name=name,
            value=value,
            data_type=values[name][1],
            id=identity.score_id(name) if identity else "",
        )
        for name in SCORE_NAMES
        if (value := values[name][0]) is not None
    ]


def _delivered(document: Mapping[str, Any]) -> float | None:
    outcome = document.get("delivery_outcome")
    if outcome not in get_args(metrics.DeliveryOutcome):
        return None
    return 0.0 if outcome == "closed" else 1.0


def _origin(document: Mapping[str, Any]) -> str | None:
    title = document.get("title")
    if not title:
        return None
    return "followup" if str(title).startswith(FOLLOWUP_TITLE_PREFIX) else "original"
