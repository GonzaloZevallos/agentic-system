"""The early size check: a cheap verdict, right after PM approves a PRD, on whether the task is
so clearly over the size budget that it should split before the design ladder and the Tech Lead.

Pure, and read by no node, edge or router yet. `applies` is the single predicate that says whether
the check is still due; `measured` is the only reader of the record the check leaves in the
state (`early_split`), and nothing reads its figures; `verdict` is the closed decision on a
declared size. Every number of the budget lives in `size_budget`; none is written here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from . import rules, size_budget
from .roles import (
    PM_CONSOLIDATOR,
    TECH_WRITER,
    UI_DESIGNER,
    UI_LEAD,
    UX_DESIGNER,
    UX_LEAD,
)
from .size_budget import PlanSize
from .task import EarlySplitCause, TaskState

Cause = EarlySplitCause


@dataclass(frozen=True)
class Verdict:
    """What the early check decided and why: `split` or `go`, and one cause of the closed set."""

    verdict: Literal["split", "go"]
    cause: Cause


def verdict(size: PlanSize | None, parts_ok: bool) -> Verdict:
    """The decision on a declared `size`, the first row that holds:

    | # | size                                   | verdict | cause          |
    |---|----------------------------------------|---------|----------------|
    | 1 | unreadable (`None`, or a negative field) | go    | sizer_failed   |
    | 2 | not over any cap                       | go      | fits           |
    | 3 | over a cap, not clearly over           | go      | close          |
    | 4 | clearly over, `parts_ok`               | split   | over           |
    | 5 | clearly over, not `parts_ok`           | go      | parts_refused  |

    `parts_ok` (the parts a split would write were accepted) is read only in rows 4 and 5."""
    if size is None or min(size.hand_files, size.hand_lines, size.test_lines) < 0:
        return Verdict("go", "sizer_failed")
    if not size_budget.crossed(size):
        return Verdict("go", "fits")
    if not size_budget.clearly_over(size):
        return Verdict("go", "close")
    if parts_ok:
        return Verdict("split", "over")
    return Verdict("go", "parts_refused")


def measured(state: TaskState) -> bool:
    """Whether the early check already has its answer: some row of `early_split` has a cause
    other than `sizer_failed`, or at least two rows are `sizer_failed` (one retry, counted by
    rows). The only reader of the record, and it reads the cause alone."""
    causes = [row.get("cause") for row in state.get("early_split", [])]
    failed = sum(1 for cause in causes if cause == "sizer_failed")
    return failed >= 2 or len(causes) > failed


def _not_a_feature(state: TaskState) -> bool:
    return state.get("type") != "feature"


def _not_full_path(state: TaskState) -> bool:
    return state.get("path", "full") != "full"


def _is_part_of_a_split(state: TaskState) -> bool:
    return rules.split_part_of(state.get("description", "")) is not None


def _is_promoted(state: TaskState) -> bool:
    return bool(state.get("promotion"))


def _is_evaluation(state: TaskState) -> bool:
    return "evaluation" in state


def _ran_a_plan_role(state: TaskState) -> bool:
    return any(
        rules.phase_of(run.get("section", run["role"])) == "plan" for run in state.get("runs", [])
    )


def _holds_plan_record(state: TaskState) -> bool:
    return any(
        (
            state.get("plan_route"),
            state.get("estimate"),
            state.get("planned_size"),
            state.get("subtasks"),
            state.get("parts"),
        )
    )


def _plan_is_due(state: TaskState) -> bool:
    return bool(state.get("plan_due") or state.get("fit_question"))


_LADDER_SECTIONS = frozenset(
    role.name for role in (UX_DESIGNER, UX_LEAD, UI_DESIGNER, UI_LEAD, PM_CONSOLIDATOR, TECH_WRITER)
)


def _ladder_started(state: TaskState) -> bool:
    return any(run.get("section", run["role"]) in _LADDER_SECTIONS for run in state.get("runs", []))


# The task is not the kind of work the check is for.
_OUT_OF_SCOPE: tuple[Callable[[TaskState], bool], ...] = (
    _not_a_feature,
    _not_full_path,
    rules.spec_delivery,
    _is_part_of_a_split,
    _is_promoted,
    _is_evaluation,
)
# The plan already started, so the check came too late to be the first word on size.
_PLAN_STARTED: tuple[Callable[[TaskState], bool], ...] = (
    _ran_a_plan_role,
    _holds_plan_record,
    _plan_is_due,
)
_LADDER_STARTED: tuple[Callable[[TaskState], bool], ...] = (_ladder_started,)


def applies(state: TaskState) -> bool:
    """Whether the early check is still due for this task: false for anything but a `feature`
    on path `full` without a spec; for a part of a split, a promoted task, or an evaluation
    run; for a task whose plan or design ladder already started; and once `measured`."""
    return not any(
        exclusion(state)
        for exclusion in (*_OUT_OF_SCOPE, *_PLAN_STARTED, *_LADDER_STARTED, measured)
    )
