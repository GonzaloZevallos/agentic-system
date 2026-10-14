"""The landing steps that move the pull request's head (GH-239): `sync_master` and
`re_review`. Inert in this subtask: each logs that it did nothing and changes nothing, so
the dispatch in `landing.land` is complete before any executor is. `reopen` is the one
thing `landing_fix` already needs of it: the patch that sends units back to a Developer."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import rules
from ..domain.roles import pair_for
from ..domain.task import (
    INTEGRATION,
    ChangeCategory,
    ChangesRequested,
    Subtask,
    SubtaskStatus,
    TaskState,
)
from .landing_plan import LandingContext


def reopen(
    state: TaskState,
    units: Sequence[str],
    reason: str,
    category: ChangeCategory,
    status: SubtaskStatus,
) -> TaskState:
    """The patch that reopens `units` (the reviewed unit when none is a subtask of the
    document) with `status`, `reason` and `category`, puts `integration` back to pending
    when it is not one of them, and enters each reopen into `change_history` with
    `by: "landing"`. It never touches `changes_requested`, the pair limit's counter."""
    subtasks = state.get("subtasks", {})
    names = [u for u in units if u in subtasks]
    if not names:
        reviewed = rules.reviewed_unit(state)
        names = [next(n for n, s in subtasks.items() if s is reviewed)] if reviewed else []
    patched: dict[str, Subtask] = {
        name: {**subtasks[name], "status": status, "reason": reason, "change_category": category}
        for name in names
    }
    if INTEGRATION in subtasks and INTEGRATION not in names:
        patched[INTEGRATION] = {**subtasks[INTEGRATION], "status": "pending"}
    history: list[ChangesRequested] = [
        {**rules.change_entry(pair_for(name), category, reason), "by": "landing"} for name in names
    ]
    return TaskState(
        id=state.get("id", ""),
        title=state.get("title", ""),
        description=state.get("description", ""),
        subtasks=patched,
        change_history=history,
    )


def sync_master(context: LandingContext) -> None:
    context.log(f"agentic: land: {context.task_id}: sync_master is not built yet")


def re_review(context: LandingContext) -> None:
    context.log(f"agentic: land: {context.task_id}: re_review is not built yet")
