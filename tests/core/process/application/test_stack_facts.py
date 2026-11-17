"""GH-860: `stack_facts` reads a stacked child's parent through counting doubles. Each test says
what was asked of git and the tracker, and what reached `StackFacts`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from agentic.core.process.application.ports import GitError, IssueState, TrackerCallFailed
from agentic.core.process.application.stack_facts import StackMemo, stack_facts
from agentic.core.process.domain import stacking
from agentic.core.process.domain.task import StackParent
from agentic.shared.configuration import Configuration

PARENT = "c" * 40
CHILD = "d" * 40
TIP = "e" * 40
MERGE = "f" * 40
CFG = Configuration(product_dir=Path("/p"))


@dataclass
class _Workspace:
    missing: set[str] = field(default_factory=set)
    ancestors: set[tuple[str, str]] = field(default_factory=set)
    raises: bool = False
    fetched: list[str] = field(default_factory=list)
    asked: list[tuple[str, str]] = field(default_factory=list)

    def fetch_commit(self, sha: str) -> bool:
        self.fetched.append(sha)
        return sha not in self.missing

    def is_ancestor(self, path: Path, sha: str, ref: str = "HEAD") -> bool:
        assert sha not in self.missing and ref not in self.missing, "asked of a missing object"
        self.asked.append((sha, ref))
        if self.raises:
            raise GitError("boom")
        return (sha, ref) in self.ancestors


@dataclass
class _Tracker:
    state: IssueState | Exception
    asked: list[int] = field(default_factory=list)

    def issue_state(self, number: int) -> IssueState:
        self.asked.append(number)
        if isinstance(self.state, Exception):
            raise self.state
        return self.state


@dataclass
class _Ports:
    workspace: _Workspace
    tracker: _Tracker


def _issue(state: Literal["OPEN", "CLOSED"], merge: str | None = None) -> IssueState:
    return IssueState(state, "COMPLETED", (), merge)


def _unread(issue: int = 7) -> stacking.StackFacts:
    record = StackParent(issue=issue, task_id="GH-7", head=PARENT, started_at="2026-11-16T10:00:00")
    facts = stacking.facts_of({"stack_parent": record})
    assert facts is not None
    return facts


def _ports(state: IssueState | Exception | None = None, **workspace: Any) -> _Ports:
    return _Ports(_Workspace(**workspace), _Tracker(state or _issue("OPEN")))


def _read(
    ports: _Ports,
    memo: StackMemo | None = None,
    tip: str = TIP,
    head: str = CHILD,
    unread: stacking.StackFacts | None = None,
) -> stacking.StackFacts:
    return stack_facts(cast(Any, ports), CFG, unread or _unread(), tip, head, memo or StackMemo())


def test_an_unreadable_record_comes_back_unchanged_with_no_read():
    ports = _ports()
    unread = stacking.facts_of({"stack_parent": None})
    assert unread is not None
    assert _read(ports, unread=unread) is unread
    assert (ports.workspace.fetched, ports.workspace.asked, ports.tracker.asked) == ([], [], [])


def test_a_missing_child_head_never_fetches_the_parent_and_is_none_not_false():
    ports = _ports(missing={CHILD})
    facts = _read(ports)
    assert ports.workspace.fetched == [CHILD]
    assert ports.workspace.asked == []
    assert (facts.parent_in_upstream, facts.child_has_parent) == (None, None)
    assert stacking.refusal(facts, False) == "stack_parent_unmerged"


def test_a_missing_parent_object_is_none_not_false():
    facts = _read(_ports(missing={PARENT}))
    assert (facts.parent_in_upstream, facts.child_has_parent) == (None, None)


def test_a_git_error_is_none():
    facts = _read(_ports(raises=True))
    assert (facts.parent_in_upstream, facts.child_has_parent) == (None, None)
    assert stacking.refusal(facts, False) == "stack_parent_unmerged"


def test_a_landed_parent_reads_no_issue_and_asks_the_child_for_it():
    ports = _ports(ancestors={(PARENT, TIP), (PARENT, CHILD)})
    facts = _read(ports)
    assert ports.tracker.asked == []
    assert (facts.parent_in_upstream, facts.child_has_parent) == (True, True)
    assert stacking.refusal(facts, False) is None


def test_an_unlanded_parent_never_asks_whether_the_child_holds_it():
    ports = _ports()
    facts = _read(ports)
    assert ports.workspace.asked == [(PARENT, TIP)]
    assert (facts.parent_in_upstream, facts.child_has_parent) == (False, None)
    assert facts.parent_state == "open"


def test_two_children_of_one_parent_share_the_issue_and_the_parent_existence():
    ports = _ports()
    memo = StackMemo()
    _read(ports, memo, head=CHILD)
    _read(ports, memo, head="a" * 40)
    assert ports.tracker.asked == [7]
    assert ports.workspace.fetched.count(PARENT) == 1
    assert ports.workspace.asked == [(PARENT, TIP)]


def test_a_different_tip_is_not_served_the_old_ancestry():
    ports = _ports(ancestors={(PARENT, "9" * 40)})
    memo = StackMemo()
    assert _read(ports, memo, tip=TIP).parent_in_upstream is False
    assert _read(ports, memo, tip="9" * 40).parent_in_upstream is True


def test_a_closed_parent_whose_closing_merge_holds_the_head_is_never_orphaned():
    ports = _ports(_issue("CLOSED", MERGE), ancestors={(PARENT, MERGE)})
    facts = _read(ports)
    assert facts.parent_state == "unknown"
    assert stacking.refusal(facts, False) == "stack_parent_unmerged"


@pytest.mark.parametrize("merge", [None, MERGE])
def test_a_closed_parent_left_out_of_upstream_is_orphaned(merge):
    facts = _read(_ports(_issue("CLOSED", merge)))
    assert facts.parent_state == "closed"
    assert stacking.refusal(facts, False) == "stack_base_orphaned"


def test_a_closing_merge_that_cannot_be_read_is_unknown_not_orphaned():
    facts = _read(_ports(_issue("CLOSED", MERGE), missing={MERGE}))
    assert facts.parent_state == "unknown"


@pytest.mark.parametrize(
    "error", [TrackerCallFailed("gh"), ValueError("x"), OSError(), KeyError("k")]
)
def test_a_tracker_failure_is_unknown_and_asked_once(error):
    ports = _ports(error)
    memo = StackMemo()
    assert _read(ports, memo).parent_state == "unknown"
    assert _read(ports, memo).parent_state == "unknown"
    assert ports.tracker.asked == [7]


# --- the two builders of MergeFacts hand the unread facts (GH-860) ------------------------------


def _poller_facts(raw: dict[str, Any]) -> Any:
    from agentic.core.process.application import poll

    return poll._merge_facts("GH-9", raw, None, None, 9, None, comments=None)


def test_the_poller_builds_the_unread_facts_of_the_record_and_none_without_it():
    record = StackParent(issue=7, task_id="GH-7", head=PARENT, started_at="2026-11-16T10:00:00")
    assert _poller_facts({"status": "ready_for_review"}).stack is None
    assert _poller_facts({"stack_parent": record}).stack == stacking.StackFacts(
        record=record, parent_state="unknown", parent_in_upstream=None, child_has_parent=None
    )
    assert _poller_facts({"stack_parent": "x"}).stack == stacking.StackFacts(
        record=None, parent_state="unknown", parent_in_upstream=None, child_has_parent=None
    )


def test_both_heads_fetched_reads_true():
    ports = _ports()
    assert _read(ports).heads_fetched is True


@pytest.mark.parametrize("missing", [CHILD, PARENT])
def test_either_head_missing_reads_false(missing):
    assert _read(_ports(missing={missing})).heads_fetched is False


def test_each_sha_is_fetched_once_per_memo_for_the_heads():
    ports = _ports()
    memo = StackMemo()
    _read(ports, memo)
    _read(ports, memo)
    assert sorted(ports.workspace.fetched) == sorted([CHILD, PARENT])


def test_an_unreadable_record_leaves_heads_fetched_unread():
    unread = stacking.facts_of({"stack_parent": None})
    assert unread is not None
    assert _read(_ports(), unread=unread).heads_fetched is None
