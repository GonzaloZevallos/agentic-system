"""GH-860: `stack_facts` reads a stacked child's parent through counting doubles. Each test says
what was asked of git and the tracker, and what reached `StackFacts`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.application.ports import (
    GitError,
    IssueComment,
    IssueState,
    LockRecord,
    TrackerCallFailed,
)
from agentic.core.process.application.stack_facts import (
    StackMemo,
    parent_chain,
    stack_facts,
)
from agentic.core.process.domain import stacking
from agentic.core.process.domain.task import StackParent, apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.gh374_shape import document as escalated_document
from tests.fakes import MemoryDocuments, MemoryTracker

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


# --- the pause predicate over the parent chain (GH-898) ----------------------------------------

STARTED = "2026-11-16T10:00:00+00:00"
AT = "2026-11-17T09:00:00+00:00"
LANDED = "9" * 40


def _head(n: int) -> str:
    return f"{n:040x}"


def _record(n: int, head: str | None = None) -> StackParent:
    return StackParent(issue=n, task_id=f"GH-{n}", head=head or _head(n), started_at=STARTED)


class _Documents(MemoryDocuments):
    def __init__(self) -> None:
        super().__init__()
        self.loads: list[str] = []
        self.raises: set[str] = set()

    def load(self, task_id: str) -> dict[str, object] | None:
        self.loads.append(task_id)
        if task_id in self.raises:
            raise OSError("unreadable")
        return super().load(task_id)


class _Lock:
    def __init__(self) -> None:
        self.records: dict[str, LockRecord] = {}
        self.raises: set[str] = set()
        self.reads: list[str] = []

    def read(self, task_id: str) -> LockRecord | None:
        self.reads.append(task_id)
        if task_id in self.raises:
            raise OSError("unreadable")
        return self.records.get(task_id)


class _World:
    """The doubles of one window: git (`_Workspace`), the tracker (`MemoryTracker`, which counts
    `issue_state` per issue), the documents and the run lock."""

    def __init__(self) -> None:
        self.workspace = _Workspace()
        self.tracker = MemoryTracker()
        self.documents = _Documents()
        self.lock = _Lock()
        self.memo = StackMemo()

    def parent(self, n: int, state: IssueState | None = None, **doc: object) -> StackParent:
        """Issue `n` is open and idle unless said otherwise; its document is `doc`."""
        self.tracker.states[n] = state or IssueState("OPEN", "", (), None, ())
        if doc.pop("save", True):
            self.documents.save({"id": f"GH-{n}", **doc})  # type: ignore[typeddict-item]
        return _record(n)

    def landed(self, *heads: str) -> None:
        self.workspace.ancestors |= {(head, TIP) for head in heads}

    def chain(self, record: StackParent) -> list[stacking.ParentLink]:
        ports = cast(Any, self)
        return parent_chain(ports, CFG, self.lock, record, TIP, self.memo)

    def cause(self, record: StackParent) -> stacking.PauseCause:
        return stacking.pause_cause(self.chain(record))

    def reads(self) -> tuple[object, ...]:
        return (
            sum(self.tracker.issue_state_calls.values()),
            list(self.documents.loads),
            list(self.lock.reads),
            list(self.workspace.fetched),
            list(self.workspace.asked),
        )


def test_three_children_of_one_parent_read_the_parent_issue_once():
    world = _World()
    parent = world.parent(7)
    for child in ("a", "b", "c"):
        assert world.cause(parent) == "none", child
    stack_facts(cast(Any, world), CFG, _unread(), TIP, CHILD, world.memo)
    assert world.tracker.issue_state_calls[7] == 1
    assert world.documents.loads == ["GH-7"]
    assert world.lock.reads == ["GH-7"]


def test_a_merged_parent_makes_no_issue_document_or_lock_read():
    world = _World()
    parent = world.parent(7, IssueState("CLOSED", "NOT_PLANNED", (), None, ()), escalated="x")
    world.landed(parent["head"])
    assert world.cause(parent) == "none"
    assert (sum(world.tracker.issue_state_calls.values()), world.documents.loads) == (0, [])
    assert world.lock.reads == []


def test_each_cause_of_an_idle_parent_outside_upstream():
    world = _World()
    assert world.cause(world.parent(1)) == "none"
    assert world.cause(world.parent(2, escalated="CI red")) == "parent_escalated"
    stopped = world.parent(3)
    world.lock.records["GH-3"] = LockRecord(1, "h", STARTED, "run", stopped_deliberately=True)
    assert world.cause(stopped) == "parent_stopped"
    abandoned = world.parent(4, IssueState("CLOSED", "NOT_PLANNED", (), None, ()))
    assert world.cause(abandoned) == "parent_abandoned"


def test_a_parent_closed_completed_outside_upstream_does_not_pause_while_refusal_orphans_it():
    world = _World()
    parent = world.parent(7, IssueState("CLOSED", "COMPLETED", (), None, ()))
    facts = stack_facts(cast(Any, world), CFG, _unread(7), TIP, CHILD, world.memo)
    assert (world.cause(parent), stacking.refusal(facts, None)) == ("none", "stack_base_orphaned")


def test_a_reopened_idle_parent_does_not_pause_while_the_merge_is_still_refused():
    world = _World()
    parent = world.parent(7)
    facts = stack_facts(cast(Any, world), CFG, _unread(7), TIP, CHILD, world.memo)
    assert (world.cause(parent), stacking.refusal(facts, None)) == ("none", "stack_parent_unmerged")


def test_a_dead_lock_without_the_mark_and_no_lock_do_not_pause():
    world = _World()
    dead = world.parent(7)
    world.lock.records["GH-7"] = LockRecord(999999, "h", STARTED, "run")
    assert world.cause(dead) == "none"
    assert world.cause(world.parent(8)) == "none"


def test_the_needs_human_label_makes_the_parent_escalated():
    world = _World()
    labelled = world.parent(7, IssueState("OPEN", "", (), None, ("needs-human",)))
    assert world.cause(labelled) == "parent_escalated"


def test_a_grandparent_that_waits_on_a_person_pauses_the_child():
    world = _World()
    world.parent(8, escalated="CI red")
    parent = world.parent(7, stack_parent=_record(8))
    assert [link.record["issue"] for link in world.chain(parent)] == [7, 8]
    assert world.cause(parent) == "parent_escalated"


def test_a_failed_read_of_any_fact_is_unreadable_never_none():
    world = _World()
    world.tracker.state_errors.add(1)
    assert world.cause(world.parent(1)) == "unreadable"
    assert world.cause(world.parent(2, save=False)) == "unreadable"
    world.documents.raises.add("GH-3")
    assert world.cause(world.parent(3)) == "unreadable"
    assert world.cause(world.parent(4, IssueState("OPEN", "", (), None, None))) == "unreadable"
    world.lock.raises.add("GH-5")
    assert world.cause(world.parent(5)) == "unreadable"


def test_a_git_failure_on_the_recorded_head_is_unreadable_and_reads_nothing_else():
    world = _World()
    parent = world.parent(7)
    world.workspace.raises = True
    assert world.cause(parent) == "unreadable"
    assert sum(world.tracker.issue_state_calls.values()) == 0


def test_a_chain_deeper_than_ten_and_a_cycle_are_unreadable():
    world = _World()
    for n in range(1, 13):
        world.parent(n, stack_parent=_record(n + 1))
    world.parent(13)
    assert world.cause(_record(4)) == "none"
    assert world.cause(_record(1)) == "unreadable"
    cycle = _World()
    cycle.parent(8, stack_parent=_record(7))
    assert cycle.cause(cycle.parent(7, stack_parent=_record(8))) == "unreadable"


def _landing(*entries: dict[str, object], **doc: object) -> dict[str, object]:
    return {"landing": list(entries), **doc}


def test_an_escalate_entry_on_an_older_head_does_not_pause_but_one_on_the_last_head_does():
    old = {"head": "1" * 40, "step": "escalate", "refusal": "ci_failed", "at": AT}
    last = {"head": "2" * 40, "step": "fix", "refusal": "ci_failed", "at": AT}
    world = _World()
    assert world.cause(world.parent(7, **_landing(old, last))) == "none"
    assert world.cause(world.parent(8, **_landing(last, {**old, "head": "2" * 40}))) == (
        "parent_escalated"
    )


def test_escalated_with_no_entries_pauses_and_nothing_set_does_not():
    world = _World()
    assert world.cause(world.parent(7, escalated="why")) == "parent_escalated"
    assert world.cause(world.parent(8, **_landing())) == "none"


def _stranded(**over: object) -> dict[str, object]:
    return {
        "status": "waiting_on_human",
        "escalated": "network",
        "escalation_class": "failure",
        "pull_request": "https://github.com/o/r/pull/7",
        "current_role": "Landing Diagnoser",
        "landing": [{"head": "1" * 40, "step": "diagnose", "at": AT}],
        "pause_log": [
            {"kind": "failure", "section": "Landing Diagnoser", "value": "network", "at": AT},
            {"kind": "escalation", "value": "failure", "at": AT},
        ],
        **over,
    }


def test_a_stranded_parent_is_not_a_persons_but_a_told_one_is():
    world = _World()
    parent = world.parent(7, **_stranded())
    assert landing_plan.stranded_now(cast(Any, world), "GH-7", _stranded())
    assert world.cause(parent) == "none"
    told = _World()
    told.tracker.timestamped["GH-7"] = [IssueComment("Escalated: network", "2026-11-18T00:00:00Z")]
    assert told.cause(told.parent(7, **_stranded())) == "parent_escalated"


def test_a_stranded_check_that_fails_reads_as_not_stranded_and_escalated_stays():
    world = _World()

    def failing(task_id: str) -> list[IssueComment]:
        raise ValueError("gh failed")

    world.tracker.timestamped_comments = failing  # type: ignore[method-assign]
    assert world.cause(world.parent(7, **_stranded())) == "parent_escalated"


def test_an_owner_answer_frees_the_child_only_once_the_release_is_written():
    raw = escalated_document(id="GH-7")
    world = _World()
    parent = world.parent(7, **{k: v for k, v in raw.items() if k != "id"})
    assert world.cause(parent) == "parent_escalated"
    answer = landing_plan.Release(None, "released_by_ci", True)
    patch = landing_plan.release_patch(raw, answer, "b" * 40, AT)  # type: ignore[arg-type]
    written = apply(raw, patch)  # type: ignore[arg-type]
    freed = _World()
    freed.parent(7, **{k: v for k, v in written.items() if k != "id"})
    assert freed.cause(_record(7)) == "none"


def test_two_chains_reaching_one_issue_at_different_heads_each_get_their_own_answer():
    world = _World()
    world.parent(9, escalated="CI red")
    first, second = _head(90), _head(91)
    world.landed(first)
    assert world.cause(_record(9, first)) == "none"
    assert world.cause(_record(9, second)) == "parent_escalated"
    assert world.cause(_record(9, first)) == "none"
    assert world.tracker.issue_state_calls[9] == 1


def test_a_second_walk_through_a_memoised_link_reads_nothing_on_any_double():
    world = _World()
    world.parent(8, escalated="CI red")
    parent = world.parent(7, stack_parent=_record(8))
    assert world.cause(parent) == "parent_escalated"
    before = world.reads()
    assert world.cause(parent) == "parent_escalated"
    assert world.reads() == before
