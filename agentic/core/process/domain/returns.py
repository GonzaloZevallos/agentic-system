"""Two questions about the task document's history, each answered in one place.

`is_return` says whether a `change_history` entry is a real return: a role sending work
back. The landing reopens a stage too (`by: "landing"`), and that is the system moving a
pull request, never a return. `finding_origin` says which review raised an `important`
row: its owner's, the integration review, or a re-review after the landing's pass.

Pure: facts in, an answer out. No reader compares `entry.get("by")` to a literal itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from .rules import review_section
from .task import INTEGRATION, FindingRecord, TaskState

LANDING = "landing"

Origin = Literal["owner_review", "integration_review", "landing_review"]


def is_return(entry: Mapping[str, object]) -> bool:
    """Whether `entry` is a real return: it names a pair and the landing did not write it."""
    return bool(entry.get("pair")) and entry.get("by") != LANDING


def finding_origin(row: FindingRecord, state: TaskState) -> Origin:
    """Which review raised `row`. A review that ran at or after the first landing step
    written once the pull request existed is the landing's re-review; before that, the
    integration review for a row of the `integration` subtask and the owner's review for any
    other. The review is the run of `review_section(subtask)` at the row's `version`; a row
    whose run is not recorded, or recorded without a start, falls back to its subtask."""
    names = row["subtask"].split(", ")
    after_pr = _first_after_pr(state)
    if after_pr is not None:
        for name in names:
            started = _review_started(state, name, row["version"])
            if started is not None and started >= after_pr:
                return "landing_review"
    return "integration_review" if INTEGRATION in names else "owner_review"


def _first_after_pr(state: TaskState) -> datetime | None:
    stamps = [
        moment
        for entry in state.get("landing", [])
        if not before_pr(entry)
        if (moment := _moment(entry.get("at"))) is not None
    ]
    return min(stamps, default=None)


def _review_started(state: TaskState, subtask: str, version: int) -> datetime | None:
    for run in state.get("runs", []):
        if run.get("section") == review_section(subtask) and run.get("version") == version:
            return _moment(run.get("started_at"))
    return None


def _moment(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def before_pr(entry: Mapping[str, object]) -> bool:
    """Whether a `landing` entry was written before the pull request existed (the pre-PR
    important pass): such an entry says nothing about a pull request."""
    return bool(entry.get("before_pr"))
