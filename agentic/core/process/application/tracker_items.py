"""The system performs its own tracker items (GH-689, ADR-0062 addendum): a `comment` item is
posted on the task's own issue, nowhere else, and the record of it is written only after the
post succeeded.

Idempotent over a lost checkpoint, the receptor pattern `post_tracker_checklist` uses: an item
with a record in `tracker_actions_done` is skipped; otherwise the issue's comments are read once
and an item whose marker is already there is recorded without posting; otherwise it is posted,
ending with its marker. The first failure stops the run and leaves the rest for a later one."""

from __future__ import annotations

from typing import NamedTuple

from ..domain import task
from ..domain.task import TaskState, TrackerActionDone
from ..domain.tracker_items import (
    PLAN_NOTE,
    TrackerItem,
    item_hash,
    marker,
    parse,
    system_items,
)
from .ports import Ports
from .run_role import finish


class SystemRun(NamedTuple):
    state: TaskState  # the state with the records written so far
    failed: Exception | None  # the failure that stopped the run, if any
    left: list[TrackerItem]  # the system items not done, in plan order
    lapses: list[str]  # steps that failed without stopping the run, `step: Class`


def run_system_items(state: TaskState, ports: Ports) -> SystemRun:
    """Performs the plan's system items in order; see the module. A failed save of a record is a
    lapse and the run goes on: the comment is on the issue and the next run finds its marker."""
    items = system_items(parse(state.get("tracker_items", [])))
    lapses: list[str] = []
    comments: list[str] | None = None
    for index, item in enumerate(items):
        key = item_hash(item)
        if key in state.get("tracker_actions_done", {}):
            continue
        try:
            if item["kind"] != "comment":
                raise ValueError(f"the system does not perform a {item['kind']} item yet")
            if comments is None:
                comments = ports.tracker.issue_comments(state["id"])
            line = marker(item, state["id"])
            if not any(line in body for body in comments):
                ports.tracker.comment(state["id"], f"{PLAN_NOTE}{item['text']}\n\n{line}")
        except Exception as failed:
            done = state.get("tracker_actions_done", {})
            left = [i for i in items[index:] if item_hash(i) not in done]
            return SystemRun(state, failed, left, lapses)
        record: TrackerActionDone = {"at": ports.now(), "kind": item["kind"]}
        patch: TaskState = {"tracker_actions_done": {key: record}}
        try:
            finish(state, patch, ports)
        except Exception as failed:
            lapses.append(f"tracker record save: {type(failed).__name__}")
        state = task.apply(state, patch)
    return SystemRun(state, None, [], lapses)
