"""Where a stacked child starts (GH-858, ADR-0103, ADR-0104): the one module that reads its
parent's facts, asks `stacking.start_ref`, and builds the document patch. It is the only writer
of `stack_parent`: every other module passes the fragment `decide` returns, unchanged, and
`checkouts.task_checkout` only reads the record.

The order is always `start_ref` -> document patch -> branch creation, and every read here
(tracker, document, remote tip, fetch) runs outside git's lock: the `start` handed to the
workspace returns a sha already decided. Any read that fails is `Wait(unreadable)`, never a
stacked start; a `Wait` writes nothing and never escalates."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from agentic.shared.configuration import Configuration

from ..domain import board, rules, stacking
from ..domain.task import StackParent
from .ports import GitError, IssueState, Ports
from .resume import state_from

# What a failed read of the tracker, a document or git raises; each one is doubt.
_DOUBT = (ValueError, OSError, KeyError, GitError)


@dataclass(frozen=True)
class Decision:
    """`ref` is `start_ref`'s answer; `patch` is the document fragment that goes with it: the
    record for `Parent`, the form that clears it for `Upstream` (only when there was a record
    to clear), empty for `Wait`."""

    ref: stacking.Upstream | stacking.Parent | stacking.Wait
    patch: dict[str, object]


class StackWait(Exception):
    """A stacked start that must wait: raised where a branch was about to be made, so that no
    branch, document or escalation follows. `cause` is from the closed `StackWaitCause`."""

    def __init__(self, cause: stacking.StackWaitCause, parent: int):
        self.cause, self.parent = cause, parent
        super().__init__(f"stacked start waits on #{parent} ({cause})")


def wait_line(task_id: str, parent: int, cause: stacking.StackWaitCause) -> str:
    return f"agentic: stack: {task_id} waits on #{parent} ({cause})"


def log_wait(task_id: str, parent: int, cause: stacking.StackWaitCause) -> None:
    """The one line a waiting stacked start leaves, on stderr; emitted only from here."""
    print(wait_line(task_id, parent, cause), file=sys.stderr)


def _issue(ports: Ports, number: int) -> IssueState | None:
    try:
        return ports.tracker.issue_state(number)
    except _DOUBT:
        return None


def _merged(
    cfg: Configuration, ports: Ports, state: IssueState, document: dict[str, object] | None
) -> bool | None:
    """Whether the parent's closing merge is in the fetched upstream; `None` when it could not
    be told. An open issue, or one not closed as completed, is not merged."""
    if state.state != "CLOSED" or state.state_reason != "COMPLETED":
        return False
    recorded = document.get("merge_commit") if document else None
    merge = str(recorded) if recorded else state.closing_merge_commit
    if merge is None:
        return False
    try:
        tip = ports.workspace.upstream().sha
        if not tip or not ports.workspace.fetch_commit(merge):
            return None
        return ports.workspace.is_ancestor(cfg.product_dir, merge, ref=tip)
    except _DOUBT:
        return None


def _carried(cfg: Configuration, ports: Ports, parent_id: str, head: str) -> bool | None:
    """Whether the parent's remote task branch carries `head`; `None` when git could not tell."""
    try:
        branch = rules.task_branch(parent_id)
        tip = ports.workspace.remote_tip(cfg.product_dir, branch, bounded=True)
        if tip is None:
            return False
        return ports.workspace.is_ancestor(cfg.product_dir, head, ref=tip)
    except _DOUBT:
        return None


def _facts(cfg: Configuration, ports: Ports, parent_issue: int) -> stacking.ParentFacts:
    """The parent's facts, read lazily and in the order `start_ref` decides on them: nothing
    past a merged or abandoned parent is read. A failed read leaves its fact `None`."""
    unread = stacking.ParentFacts(None, None, None, None, None, None)
    state = _issue(ports, parent_issue)
    try:
        parent_id = ports.inbox.task_id(parent_issue)
        document = ports.documents.load(parent_id)
    except _DOUBT:
        return unread
    if state is None:
        return unread
    merged = _merged(cfg, ports, state, document)
    abandoned = state.state == "CLOSED" and state.state_reason == "NOT_PLANNED"
    if merged is not False or abandoned or document is None:
        return stacking.ParentFacts(merged, abandoned, None, None, None, None)
    labels = state.labels
    paused = (
        None
        if labels is None
        else bool(document.get("escalated")) or board.NEEDS_HUMAN_LABEL in labels
    )
    ready = state.state == "OPEN" and document.get("status") == "ready_for_review"
    head = rules.approved_head(state_from(document))
    carried = _carried(cfg, ports, parent_id, head) if ready and head else None
    return stacking.ParentFacts(merged, abandoned, paused, ready, head, carried)


def decide(
    task_id: str,
    parent_issue: int,
    previous_record: StackParent | None,
    ports: Ports,
    now: str,
    *,
    cfg: Configuration,
) -> Decision:
    """Where `task_id` starts, given the parent it is stacked on. Reads the parent's facts,
    asks `stacking.start_ref` and, for `Parent`, brings the head in with `fetch_commit` (a
    failed fetch is `Wait(unreadable)`). `previous_record` is the record the task already has:
    `started_at` is kept from it when the head is the same, and `Upstream` clears it. `task_id`
    is the child's, for the caller's log line; `now` stamps a new record."""
    del task_id
    ref = stacking.start_ref(parent_issue, _facts(cfg, ports, parent_issue))
    match ref:
        case stacking.Parent(head=head):
            try:
                fetched = ports.workspace.fetch_commit(head)
            except _DOUBT:
                fetched = False
            if not fetched:
                return Decision(stacking.Wait("unreadable"), {})
            same = (
                previous_record is not None
                and previous_record["issue"] == parent_issue
                and previous_record["head"] == head
            )
            started = previous_record["started_at"] if same and previous_record else now
            record = StackParent(
                issue=parent_issue,
                task_id=ports.inbox.task_id(parent_issue),
                head=head,
                started_at=started,
            )
            return Decision(ref, {"stack_parent": record})
        case stacking.Upstream():
            return Decision(ref, {} if previous_record is None else {"stack_parent": None})
        case _:
            return Decision(ref, {})


def branch_start(record: StackParent, ports: Ports) -> str:
    """The sha a stacked task's branch starts at: the recorded head, brought in outside git's
    lock. Only reads. Raises `StackWait(unreadable)` when the commit cannot be fetched, so no
    branch is made."""
    try:
        fetched = ports.workspace.fetch_commit(record["head"])
    except _DOUBT:
        fetched = False
    if not fetched:
        raise StackWait("unreadable", record["issue"])
    return record["head"]
