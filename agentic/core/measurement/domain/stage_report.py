"""The stage report of the scores backfill (GH-752): one line per task with its hours by stage,
then a total block. Pure; the stage seconds come from `stages.stage_split` and enter here as a
`StageSplit`, and `hours` is the only place they are divided into hours.

A document whose tracker read failed, or that was never read, is `Unread` and carries a cause
of its own; it is never a `StageUnknown` and never yields a score."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, get_args

from .stages import (
    OTHER_ORDER,
    CycleStage,
    StageSplit,
    StageUnknown,
    StageUnknownCause,
)

UnreadReason = Literal["unreadable", "not_finished", "no_time", "gh_failed"]

_STAGES: tuple[CycleStage, ...] = get_args(CycleStage)
_SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class Unread:
    """The document or a tracker read it needs did not yield an outcome: `reason` says which."""

    reason: UnreadReason


@dataclass(frozen=True)
class StageRow:
    """One document of the backfill: its outcome and its active hours (`None` when unknown)."""

    task_id: str
    outcome: StageSplit | StageUnknown | Unread
    active_h: float | None


def hours(split: StageSplit) -> dict[CycleStage, float]:
    """The hours of each stage of a split, in `CycleStage` order."""
    return {stage: split.seconds[stage] / _SECONDS_PER_HOUR for stage in _STAGES}


def _natural(task_id: str) -> list[tuple[int, int, str]]:
    """`GH-9` before `GH-10`: digit runs compare as numbers."""
    parts = re.split(r"(\d+)", task_id)
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts]


def _percent(part: float, whole: float) -> str:
    return f"{part / whole * 100:.1f}%" if whole > 0 else "n/a"


def _line(row: StageRow) -> str:
    outcome = row.outcome
    if isinstance(outcome, Unread):
        return f"{row.task_id} | unread:{outcome.reason}"
    if isinstance(outcome, StageUnknown):
        return f"{row.task_id} | unknown:{outcome.cause}"
    shown = " ".join(f"{value:.3f}" for value in hours(outcome).values())
    share = _percent(outcome.seconds["other"], sum(outcome.seconds.values()))
    return f"{row.task_id} | {shown} | other {share}"


def _counts(names: Iterable[str], order: tuple[str, ...]) -> str:
    seen = list(names)
    return " ".join(f"{name} {seen.count(name)}" for name in order)


def _queue_over_active(splits: list[tuple[StageSplit, float | None]]) -> tuple[int, int]:
    """`(n, m)`: of the splits with a known `active_h` (`m`), those whose queue hours exceed it
    (`n`). Both sides are hours."""
    known = [(split, active) for split, active in splits if active is not None]
    return sum(1 for split, active in known if hours(split)["queue"] > active), len(known)


def stage_lines(rows: Iterable[StageRow]) -> list[str]:
    """The report: a header of the ten stages, one line per row in natural order of task id,
    then the total block. An empty input prints the header and the block at zero."""
    ordered = sorted(rows, key=lambda row: _natural(row.task_id))
    outcomes = [row.outcome for row in ordered]
    splits = [(row.outcome, row.active_h) for row in ordered if isinstance(row.outcome, StageSplit)]
    cycle = sum(sum(split.seconds.values()) for split, _ in splits)
    share = " ".join(
        f"{cause} {_percent(sum(split.other_by_cause[cause] for split, _ in splits), cycle)}"
        for cause in OTHER_ORDER
    )
    over, known = _queue_over_active(splits)
    legacy = sum(1 for split, _ in splits if split.other_by_cause["legacy_role"] > 0)
    return [
        "task | " + " ".join(_STAGES),
        *(_line(row) for row in ordered),
        "unknown "
        + _counts(
            (o.cause for o in outcomes if isinstance(o, StageUnknown)),
            get_args(StageUnknownCause),
        ),
        "unread "
        + _counts((o.reason for o in outcomes if isinstance(o, Unread)), get_args(UnreadReason)),
        f"other share {share if cycle > 0 else 'n/a'}",
        f"legacy_role {legacy}",
        f"queue>active_h (unclipped) {over}/{known}",
    ]
