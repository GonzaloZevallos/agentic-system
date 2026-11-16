"""GH-860: the poller's decision reads a stacked child's facts through `stack_facts`, once the
pair is landable, memoised per window. In-memory ports with counting wrappers: no `gh`, no git."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentic.core.process.application import merge, poll
from agentic.core.process.application.ports import IssueState, Upstream
from agentic.core.process.application.stack_facts import StackMemo
from agentic.core.process.domain.task import VERDICT_UNLANDABLE, StackParent
from tests.core.process.application.test_poll import _cfg, _Locks
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _ports

PARENT = "c" * 40
CLOSING = "f" * 40


def _record(issue: int = 9) -> StackParent:
    return StackParent(
        issue=issue, task_id=f"GH-{issue}", head=PARENT, started_at="2026-11-16T10:00:00"
    )


def _stacked(*numbers: int, issue: int = 9, **more):
    ports = _ports(*numbers, stack_parent=_record(issue), **more)
    ports.tracker.states[issue] = IssueState("OPEN", "", (), None)
    return ports


def _count(ports) -> dict[str, int]:
    """Wraps the three reads a stacking fact costs; the dict is what each was asked."""
    calls = {"issue_state": 0, "fetch_commit": 0, "is_ancestor": 0}
    tracker, workspace = ports.tracker, ports.workspace
    issue_state, fetch, ancestor = (
        tracker.issue_state,
        workspace.fetch_commit,
        workspace.is_ancestor,
    )

    def counted(name, original):
        def wrapper(*args, **kwargs):
            # The landing order reads the labels of every task's issue: only the parent's counts.
            if name != "issue_state" or args[0] == 9:
                calls[name] += 1
            return original(*args, **kwargs)

        return wrapper

    tracker.issue_state = counted("issue_state", issue_state)
    workspace.fetch_commit = counted("fetch_commit", fetch)
    workspace.is_ancestor = counted("is_ancestor", ancestor)
    return calls


def _assess(ports, number: int = 5, memo: StackMemo | None = None):
    return poll.assess_merge(
        ports,
        f"GH-{number}",
        ports.documents.load(f"GH-{number}"),
        number,
        frozenset(),
        MASTER,
        cfg=_cfg(Path("/p")),
        memo=memo or StackMemo(),
    )


def test_a_stacked_child_that_reaches_the_row_is_read_and_refused_per_the_table():
    ports = _stacked(5)
    assessed = _assess(ports)
    assert assessed.decision.reason == "stack_parent_unmerged"
    assert assessed.facts.stack.parent_state == "open"
    assert assessed.facts.stack.parent_in_upstream is False


def test_a_stacked_child_whose_closed_parent_left_its_head_out_is_orphaned():
    ports = _stacked(5)
    ports.tracker.states[9] = IssueState("CLOSED", "COMPLETED", (), CLOSING)
    ports.workspace.ancestors_by_ref[CLOSING] = set()
    assert _assess(ports).decision.reason == "stack_base_orphaned"


def test_a_landed_parent_the_child_holds_merges():
    ports = _stacked(5)
    ports.workspace.ancestors = {PARENT}
    assessed = _assess(ports)
    assert assessed.decision.kind == "merge"
    assert assessed.facts.stack.child_has_parent is True


def _forbid_stack_facts(monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("stack_facts was called")

    monkeypatch.setattr(poll, "stack_facts", refuse)


def test_a_document_without_the_record_makes_no_stacking_read(monkeypatch):
    ports = _ports(5)
    _forbid_stack_facts(monkeypatch)
    calls = _count(ports)
    assert _assess(ports).decision.kind == "merge"
    assert calls["issue_state"] == 0


def test_an_unlandable_verdict_makes_no_stacking_read(monkeypatch):
    diagnosis = {
        "head": HEAD,
        "step": "diagnose",
        "refusal": "merge_failed",
        "delivered": True,
        "verdict": VERDICT_UNLANDABLE,
    }
    ports = _stacked(5, landing=[diagnosis])
    _forbid_stack_facts(monkeypatch)
    calls = _count(ports)
    _assess(ports)
    assert calls["issue_state"] == 0


def test_two_children_of_one_parent_in_a_round_share_one_issue_read():
    ports = _stacked(5, 6)
    calls = _count(ports)
    memo = StackMemo()
    assert _assess(ports, 5, memo).decision.reason == "stack_parent_unmerged"
    assert _assess(ports, 6, memo).decision.reason == "stack_parent_unmerged"
    assert calls["issue_state"] == 1


def test_the_poller_round_gives_its_children_one_memo(tmp_path):
    ports = _stacked(5, 6)
    calls = _count(ports)
    lines: list[str] = []
    found = poll.gather_merges(
        ports, frozenset(), MASTER, _Locks(), lines, cfg=_cfg(tmp_path), memo=StackMemo()
    )
    assert found == []
    assert calls["issue_state"] == 1
    assert any("stack_parent_unmerged" in line for line in lines)


def test_two_assessments_with_a_fresh_memo_each_read_the_issue_twice(tmp_path):
    """What `landing.land` and `resume` do: a fresh memo at each `assess_merge` call."""
    ports = _stacked(5)
    calls = _count(ports)
    cfg = _cfg(tmp_path)
    for _ in range(2):
        poll.assess_merge(
            ports,
            "GH-5",
            ports.documents.load("GH-5"),
            5,
            frozenset(),
            MASTER,
            cfg=cfg,
            memo=StackMemo(),
        )
    assert calls["issue_state"] == 2


def test_the_verb_still_refuses_an_unread_record_as_unmerged_until_876(tmp_path):
    """Pins today's result: `merge_task` inherits the unread facts and never upgrades them.
    #876 wires the verb's own read and changes this on purpose."""
    ports = _stacked(5)
    calls = _count(ports)
    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda _line: None,
        lambda: datetime(2026, 11, 16, tzinfo=UTC).isoformat(),
    )
    assert "stack_parent_unmerged" in repr(result)
    assert calls["issue_state"] == 0
