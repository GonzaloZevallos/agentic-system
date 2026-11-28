"""GH-858: `stacked_start.decide` and `branch_start`, and `checkouts.task_checkout` handing the
recorded head to the workspace, through the in-memory port doubles."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.application import checkouts, stacked_start
from agentic.core.process.application.ports import IssueState, Upstream
from agentic.core.process.domain import rules, stacking
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

HEAD, MOVED, TIP, MERGE = "a" * 40, "b" * 40, "c" * 40, "d" * 40
PARENT, CHILD = 5, 6
NOW, EARLIER = "2026-11-27T12:00:00+00:00", "2026-11-26T09:00:00+00:00"
CFG = Configuration(product_dir=Path("/p"))
BRANCH = f"agentic/GH-{PARENT}"


def _parent(**more):
    return {
        "id": f"GH-{PARENT}",
        "title": "p",
        "description": "d",
        "status": "ready_for_review",
        "subtasks": {"a": {"reviewed_head": HEAD}},
        **more,
    }


def _ports(document=None, *, issue=None, tip=TIP, carried=True):
    ports = memory_ports()
    ports.tracker.states[PARENT] = issue or IssueState("OPEN", "", (), None, ())
    if document is not False:
        ports.documents.save(_parent() if document is None else document)
    if tip is not None:
        ports.workspace.remote_tips[BRANCH] = tip
    ports.workspace.ancestors = {HEAD} if carried else set()
    ports.workspace.upstream_answer = Upstream("origin/main", TIP, None)
    return ports


def _decide(ports, previous=None):
    return stacked_start.decide(f"GH-{CHILD}", PARENT, previous, ports, NOW, cfg=CFG)


def test_a_ready_parent_is_recorded_with_its_head_and_the_instant():
    ports = _ports()
    decision = _decide(ports)
    assert decision.ref == stacking.Parent(HEAD)
    assert decision.patch == {
        "stack_parent": {"issue": PARENT, "task_id": "GH-5", "head": HEAD, "started_at": NOW}
    }
    assert HEAD in ports.workspace.fetched_commits


def test_the_same_head_keeps_the_recorded_instant_and_a_new_head_restamps_it():
    previous = {"issue": PARENT, "task_id": "GH-5", "head": HEAD, "started_at": EARLIER}
    assert _decide(_ports(), previous).patch["stack_parent"]["started_at"] == EARLIER
    moved = _parent(subtasks={"a": {"reviewed_head": MOVED}})
    ports = _ports(moved)
    ports.workspace.ancestors = {MOVED}
    assert _decide(ports, previous).patch["stack_parent"]["started_at"] == NOW


@pytest.mark.parametrize(
    "issue",
    [
        IssueState("CLOSED", "COMPLETED", (), MERGE, ()),
        IssueState("CLOSED", "NOT_PLANNED", (), None, ()),
    ],
    ids=["merged", "abandoned"],
)
def test_a_merged_or_abandoned_parent_starts_at_upstream_and_clears_the_record(issue):
    ports = _ports(issue=issue)
    ports.workspace.ancestors = {MERGE}
    previous = {"issue": PARENT, "task_id": "GH-5", "head": HEAD, "started_at": EARLIER}
    decision = _decide(ports, previous)
    assert decision.ref == stacking.Upstream() and decision.patch == {"stack_parent": None}
    assert _decide(ports).patch == {}  # nothing to clear: nothing written


@pytest.mark.parametrize(
    ("kwargs", "cause"),
    [
        ({"document": _parent(status="planning")}, "parent_not_ready"),  # a restarted run
        ({"document": _parent(escalated="boom")}, "parent_paused"),
        ({"issue": IssueState("OPEN", "", (), None, ("needs-human",))}, "parent_paused"),
        ({"carried": False}, "head_not_carried"),
        ({"tip": None}, "head_not_carried"),
        ({"document": False}, "unreadable"),
        ({"issue": IssueState("CLOSED", "COMPLETED", (), None, ())}, "parent_not_ready"),
    ],
    ids=[
        "not ready",
        "escalated",
        "needs-human",
        "not carried",
        "unpublished",
        "no document",
        "closed unmerged",
    ],
)
def test_every_wait_cause_writes_nothing(kwargs, cause):
    decision = _decide(_ports(**kwargs))
    assert decision.ref == stacking.Wait(cause) and decision.patch == {}


def test_a_failed_read_or_fetch_is_a_wait_never_a_launch():
    ports = _ports()
    ports.workspace.remote_tip_error = "origin down"
    assert _decide(ports).ref == stacking.Wait("unreadable")
    ports = _ports()
    ports.tracker.state_errors.add(PARENT)
    assert _decide(ports).ref == stacking.Wait("unreadable")
    ports = _ports()
    ports.workspace.unfetchable.add(HEAD)
    decision = _decide(ports)
    assert decision.ref == stacking.Wait("unreadable") and decision.patch == {}


def test_the_wait_line_is_the_closed_form():
    assert stacked_start.wait_line("GH-6", 5, "parent_paused") == (
        "agentic: stack: GH-6 waits on #5 (parent_paused)"
    )


def _child(record=True):
    state = {"id": f"GH-{CHILD}", "title": "c", "description": "d"}
    if record:
        state["stack_parent"] = {
            "issue": PARENT,
            "task_id": "GH-5",
            "head": HEAD,
            "started_at": EARLIER,
        }
    return state


def test_the_branch_starts_at_the_recorded_head_from_two_nodes_and_the_state_is_untouched():
    ports = memory_ports()
    state = _child()
    before = dict(state)
    first = checkouts.task_checkout(state, CFG, ports)
    second = checkouts.task_checkout(state, CFG, ports)
    branch = rules.task_branch(f"GH-{CHILD}")
    assert ports.workspace.started == {branch: HEAD}
    assert first.base == second.base == HEAD
    assert ports.workspace.fetched_commits == [HEAD]
    assert state == before and ports.documents.saved == []


def test_a_task_with_no_record_branches_as_always():
    ports = memory_ports()
    checkouts.task_checkout(_child(record=False), CFG, ports)
    assert ports.workspace.started == {} and ports.workspace.fetched_commits == []


def test_a_crash_between_the_patch_and_the_checkout_resumes_at_the_recorded_head():
    ports = memory_ports()
    state = _child()  # the record is on disk, no branch yet
    checkouts.task_checkout(state, CFG, ports)
    assert ports.workspace.started[rules.task_branch(f"GH-{CHILD}")] == HEAD
    record = stacking.parse_stack_parent(state)
    assert isinstance(record, dict)
    facts = stacking.StackFacts(
        record=record,
        parent_state="unknown",
        parent_in_upstream=None,
        child_has_parent=None,
    )
    assert stacking.refusal(facts, None) == "stack_parent_unmerged"


def test_a_head_that_cannot_be_fetched_raises_and_makes_no_branch():
    ports = memory_ports()
    ports.workspace.unfetchable.add(HEAD)
    with pytest.raises(stacked_start.StackWait) as raised:
        checkouts.task_checkout(_child(), CFG, ports)
    assert raised.value.cause == "unreadable" and raised.value.parent == PARENT
    assert ports.workspace.checkouts_made == [] and ports.workspace.started == {}
