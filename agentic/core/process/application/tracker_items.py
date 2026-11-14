"""The system performs its own tracker items (GH-689, GH-690, ADR-0062 addendum): a `comment`
item is posted on the task's own issue, nowhere else; an `open_issue` item is opened as a new
issue labelled `needs-triage`, ending with a `From task: #N` footer and its marker. The record
of an item is written only after the action succeeded.

Idempotent over a lost checkpoint, the receptor pattern `post_tracker_checklist` uses: an item
with a record in `tracker_actions_done` is skipped; otherwise the issue's comments are read once
and an item whose marker is already there is recorded without posting; otherwise it is posted,
ending with its marker. An `open_issue` item looks its marker up in the bodies of the open
issues (`open_issue_refs`, read once per run, which raises on a list that may be cut: then no
action and no record) and only then opens the issue; it never finds an issue by its title. The
first failure stops the run and leaves the rest for a later one."""

from __future__ import annotations

from typing import NamedTuple

from ..domain import task
from ..domain.task import TaskState, TrackerActionDone
from ..domain.tracker_items import (
    PLAN_NOTE,
    TRIAGE_LABEL,
    OpenIssueItem,
    TrackerItem,
    item_hash,
    marker,
    parse,
    system_items,
)
from .ports import IssueRef, Ports
from .run_role import finish


class SystemRun(NamedTuple):
    state: TaskState  # the state with the records written so far
    failed: Exception | None  # the failure that stopped the run, if any
    left: list[TrackerItem]  # the system items not done, in plan order
    lapses: list[str]  # steps that failed without stopping the run, `step: Class`


def _open(item: OpenIssueItem, line: str, task_id: str, ports: Ports) -> str:
    """Opens the issue of an `open_issue` item and answers its URL: the label is the system's
    (`TRIAGE_LABEL`, never a field), and the footer naming the task, then the marker, is added
    here, after the item was validated."""
    number = ports.inbox.issue_of(task_id)
    if number is None:
        raise ValueError(f"task {task_id} has no issue to link the new issue to")
    ports.tracker.ensure_label(TRIAGE_LABEL)
    body = f"{item['body']}\n\nFrom task: #{number}\n\n{line}"
    return ports.tracker.open_issue(item["title"], body, label=TRIAGE_LABEL)


def run_system_items(state: TaskState, ports: Ports) -> SystemRun:
    """Performs the plan's system items in order; see the module. A failed save of a record is a
    lapse and the run goes on: the comment is on the issue and the next run finds its marker."""
    items = system_items(parse(state.get("tracker_items", [])))
    lapses: list[str] = []
    comments: list[str] | None = None
    refs: list[IssueRef] | None = None
    for index, item in enumerate(items):
        key = item_hash(item)
        if key in state.get("tracker_actions_done", {}):
            continue
        url: str | None = None
        try:
            line = marker(item, state["id"])
            if item["kind"] == "comment":
                if comments is None:
                    comments = ports.tracker.issue_comments(state["id"])
                if not any(line in body for body in comments):
                    ports.tracker.comment(state["id"], f"{PLAN_NOTE}{item['text']}\n\n{line}")
            elif item["kind"] == "open_issue":
                if refs is None:
                    refs = ports.inbox.open_issue_refs()
                url = next((ref.url for ref in refs if line in ref.body), None)
                if url is None:
                    url = _open(item, line, state["id"], ports)
            else:
                raise ValueError(f"the system does not perform a {item['kind']} item")
        except Exception as failed:
            done = state.get("tracker_actions_done", {})
            left = [i for i in items[index:] if item_hash(i) not in done]
            return SystemRun(state, failed, left, lapses)
        record: TrackerActionDone = {"at": ports.now(), "kind": item["kind"]}
        if url is not None:
            record["url"] = url
        patch: TaskState = {"tracker_actions_done": {key: record}}
        try:
            finish(state, patch, ports)
        except Exception as failed:
            lapses.append(f"tracker record save: {type(failed).__name__}")
        state = task.apply(state, patch)
    return SystemRun(state, None, [], lapses)
