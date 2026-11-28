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

from ..domain import stacking
from ..domain.task import StackParent
from . import stack_facts
from .ports import IssueState, Ports

_DOUBT = stack_facts.DOUBT


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


def _carried(cfg: Configuration, ports: Ports, parent_id: str, head: str) -> bool | None:
    """Whether the parent's remote task branch carries `head`: `stack_facts.carried`."""
    return stack_facts.carried(cfg, ports, parent_id, head)


def _facts(cfg: Configuration, ports: Ports, parent_issue: int) -> stacking.ParentFacts:
    """The parent's facts at its current approved head: `stack_facts.parent_facts`, the one
    computation the hold shares."""
    return stack_facts.parent_facts(cfg, ports, parent_issue)


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
