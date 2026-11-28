"""GH-858: `poll._blocked` with the round's `ParentMemo` reads a Ready same-split parent as the
stacked one, and `run_once` launches a child whose only blocker it is, with the switch on;
any doubt leaves it `open`."""

from __future__ import annotations

import functools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.application import poll, split_sweep
from agentic.core.process.application.ports import IssueState, StackHint, Upstream
from agentic.core.process.domain import launch_order, rules, stacking
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_poll import _Locks, _round, _seed_body
from tests.core.process.application.test_poll_orphaned import _Documents
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports

HEAD = "a" * 40
TIP = "b" * 40
CHILD, PARENT = 6, 5
MARK = "<!-- agentic-split: GH-1 part {n} depth 2 -->"
RECORD = {"issue": 4, "task_id": "GH-4", "head": HEAD, "started_at": "2026-11-16T10:00:00Z"}
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _upstream() -> Upstream:
    return Upstream("HEAD", None, None)


def _cfg() -> Configuration:
    return Configuration(product_dir=Path("/p"))


def _ready(**more):
    return {
        "id": f"GH-{PARENT}",
        "status": "ready_for_review",
        "subtasks": {"a": {"reviewed_head": HEAD}},
        **more,
    }


def _ports(*, parent=None, child_body=None, parent_body=None, tip=TIP, ancestors=(HEAD,)):
    documents = _Documents()
    ports = memory_ports(documents=documents)
    ports.tracker.states[CHILD] = IssueState("OPEN", "", (PARENT,), None)
    ports.tracker.states[PARENT] = IssueState("OPEN", "", (), None)
    _seed_body(ports, CHILD, MARK.format(n=2) if child_body is None else child_body)
    _seed_body(ports, PARENT, MARK.format(n=1) if parent_body is None else parent_body)
    documents.save(_ready() if parent is None else parent)
    if tip is not None:
        ports.workspace.remote_tips[f"agentic/GH-{PARENT}"] = tip
    ports.workspace.ancestors = set(ancestors)
    return ports


def _judge(ports, memo=None):
    memo = memo if memo is not None else stacking.ParentMemo()
    return poll._blocked(_cfg(), ports, CHILD, _upstream, None, memo), memo


def test_without_the_memo_the_report_is_todays_and_reads_nothing_for_stacking():
    ports = _ports()
    report = poll._blocked(_cfg(), ports, CHILD, _upstream)
    assert report == rules.BlockerReport(open=(PARENT,))
    assert report.stacked_on is None and bool(report)
    assert ports.workspace.remote_tip_calls == [] and ports.workspace.ancestor_checks == []


@pytest.mark.parametrize("tip", [HEAD, TIP], ids=["tip equals the head", "tip descends from it"])
def test_a_ready_same_split_parent_whose_branch_carries_its_head_is_the_stacked_one(tip):
    ports = _ports(tip=tip)
    report, memo = _judge(ports)
    assert report == rules.BlockerReport(stacked_on=PARENT)
    assert report.open == () and memo.outcome(PARENT, HEAD) == "stacked"
    assert ports.workspace.remote_tip_bounded == [True]
    assert ports.workspace.remote_tip_calls == [(Path("/p"), f"agentic/GH-{PARENT}")]


def _not_stacked(outcome):
    return outcome


@pytest.mark.parametrize(
    ("kwargs", "outcome"),
    [
        ({"ancestors": ()}, "not_stacked"),  # a rewritten tip: the head is not in it
        ({"tip": None}, "not_stacked"),  # the branch is unpublished
        ({"parent": _ready() | {"status": "in_review"}}, None),
        ({"parent": _ready(stack_parent=RECORD)}, None),  # depth 1 only
        ({"parent": _ready(stack_parent={"issue": "x"})}, None),  # illegible record too
        ({"parent": {"id": "GH-5", "status": "ready_for_review"}}, None),  # no approved head
        ({"parent_body": MARK.format(n=1).replace("GH-1", "GH-2")}, None),  # another split
        ({"parent_body": MARK.format(n=1) * 2}, None),  # ambiguous marker
        ({"child_body": "no marker"}, None),
    ],
    ids=[
        "rewritten tip",
        "unpublished",
        "not ready",
        "own stack_parent",
        "illegible stack_parent",
        "no approved head",
        "other split",
        "ambiguous marker",
        "child has no marker",
    ],
)
def test_each_doubt_leaves_the_parent_open(kwargs, outcome):
    ports = _ports(**kwargs)
    report, memo = _judge(ports)
    assert report == rules.BlockerReport(open=(PARENT,))
    assert report.stacked_on is None and bool(report)
    assert memo.outcome(PARENT, HEAD) in (outcome, None)
    assert "stacked" not in memo.outcomes.values()


def test_a_missing_parent_document_leaves_it_open():
    ports = _ports(parent={"id": "GH-99", "status": "definition_ready"})  # GH-5 has none
    report, _ = _judge(ports)
    assert report == rules.BlockerReport(open=(PARENT,))


@pytest.mark.parametrize("failing", [CHILD, PARENT])
def test_a_gh_failure_reading_either_marker_body_leaves_the_parent_open(failing):
    ports = _ports()
    ports.inbox.task_exceptions[failing] = OSError("gh: rate limit")
    if failing == CHILD:
        ports.tracker.states[CHILD] = IssueState("OPEN", "", (PARENT,), None)  # native: no read
    report, _ = _judge(ports)
    assert report == rules.BlockerReport(open=(PARENT,))
    assert ports.workspace.remote_tip_calls == []


@pytest.mark.parametrize(
    "error", ["remote agentic/GH-5 moved between ls-remote (x) and fetch (y)", "timed out"]
)
def test_a_remote_failure_is_doubt_and_the_parent_stays_open(error):
    ports = _ports()
    ports.workspace.remote_tip_error = error
    report, memo = _judge(ports)
    assert report == rules.BlockerReport(open=(PARENT,))
    assert memo.outcome(PARENT, HEAD) == "doubt" and memo.origin_failed


def test_a_failed_ancestry_read_is_doubt():
    ports = _ports()
    ports.workspace.ancestor_errors = {HEAD}
    report, memo = _judge(ports)
    assert report == rules.BlockerReport(open=(PARENT,))
    assert memo.outcome(PARENT, HEAD) == "doubt" and not memo.origin_failed


def test_a_second_unresolved_blocker_is_never_stacked():
    ports = _ports()
    ports.tracker.states[CHILD] = IssueState("OPEN", "", (PARENT, 9), None)
    ports.tracker.states[9] = IssueState("OPEN", "", (), None)
    report, memo = _judge(ports)
    assert report == rules.BlockerReport(open=(PARENT, 9))
    assert memo.outcomes == {} and ports.workspace.remote_tip_calls == []


def test_two_children_of_one_parent_cost_one_remote_tip_and_one_ancestry_read():
    ports = _ports()
    other = 7
    ports.tracker.states[other] = IssueState("OPEN", "", (PARENT,), None)
    _seed_body(ports, other, MARK.format(n=3))
    memo = stacking.ParentMemo()
    first = poll._blocked(_cfg(), ports, CHILD, _upstream, None, memo)
    second = poll._blocked(_cfg(), ports, other, _upstream, None, memo)
    assert first == second == rules.BlockerReport(stacked_on=PARENT)
    assert len(ports.workspace.remote_tip_calls) == 1
    assert len(ports.workspace.ancestor_checks) == 1


def test_a_sibling_of_another_split_does_not_inherit_the_memoised_stack():
    ports = _ports()
    other = 7
    ports.tracker.states[other] = IssueState("OPEN", "", (PARENT,), None)
    _seed_body(ports, other, "<!-- agentic-split: GH-9 part 2 depth 2 -->")
    memo = stacking.ParentMemo()
    assert poll._blocked(_cfg(), ports, CHILD, _upstream, None, memo).stacked_on == PARENT
    assert poll._blocked(_cfg(), ports, other, _upstream, None, memo).open == (PARENT,)


def test_after_one_remote_failure_no_other_parent_is_read_and_each_is_a_doubt():
    ports = _ports()
    ports.workspace.remote_tip_error = "unreachable"
    second_parent, second_child = 8, 9
    ports.documents.save({**_ready(), "id": f"GH-{second_parent}"})
    ports.tracker.states[second_child] = IssueState("OPEN", "", (second_parent,), None)
    ports.tracker.states[second_parent] = IssueState("OPEN", "", (), None)
    _seed_body(ports, second_child, MARK.format(n=2))
    _seed_body(ports, second_parent, MARK.format(n=1))
    memo = stacking.ParentMemo()
    one = poll._blocked(_cfg(), ports, CHILD, _upstream, None, memo)
    two = poll._blocked(_cfg(), ports, second_child, _upstream, None, memo)
    assert one.open == (PARENT,) and two.open == (second_parent,)
    assert len(ports.workspace.remote_tip_calls) == 1
    assert memo.outcomes == {(PARENT, HEAD): "doubt", (second_parent, HEAD): "doubt"}


# -- a stacked report is not resolved ---------------------------------------------------


def test_a_stacked_report_resolves_nothing_for_the_part_verdict_the_sweep_or_the_order():
    ports = _ports()
    report, _ = _judge(ports)
    assert report.stacked_on == PARENT
    # `_blocker_verdict` alone resolves: the open parent stays `open`.
    assert poll._part_verdict(_cfg(), ports, _upstream, PARENT) == "open"
    # The sweep: a split parent whose part is the stacked parent is not closed.
    ports.documents.save({"id": "GH-1", "status": "split", "parts": [{"issue": PARENT}]})
    queued = {1: ports.documents.load("GH-1"), PARENT: ports.documents.load("GH-5")}
    assert split_sweep.sweep(ports, queued, verdict=lambda n: "open") == []
    # The launch order counts the edge as it always did.
    assert launch_order.dependents({CHILD: (PARENT,)}) == {PARENT: 1}


def test_the_orphan_pass_without_the_memo_still_holds_a_child_with_an_open_blocker():
    ports = _ports()
    ports.documents.save({"id": f"GH-{CHILD}", "status": "definition_ready"})
    reports: list[rules.BlockerReport] = []
    by_issue: dict[int, rules.BlockerReport] = {}
    orphans, _ = poll.gather_orphans(
        _cfg(),
        ports,
        _Locks(),
        {CHILD: ports.documents.load(f"GH-{CHILD}")},
        frozenset(),
        frozenset(),
        NOW,
        functools.cache(ports.workspace.upstream),
        reports,
        by_issue,
    )
    assert orphans == {} and by_issue[CHILD] == rules.BlockerReport(open=(PARENT,))
    assert ports.workspace.remote_tip_calls == []


# -- the orphan pass ----------------------------------------------------------------------


def _orphans(ports, raw):
    reports: list[rules.BlockerReport] = []
    by_issue: dict[int, rules.BlockerReport] = {}
    orphans, _ = poll.gather_orphans(
        _cfg(),
        ports,
        _Locks(),
        {CHILD: raw},
        frozenset(),
        frozenset(),
        NOW,
        functools.cache(ports.workspace.upstream),
        reports,
        by_issue,
    )
    return orphans, by_issue


def test_a_stacked_orphan_with_an_open_ready_parent_is_retaken_without_judging_blockers():
    ports = _ports()
    raw = {"id": f"GH-{CHILD}", "status": "definition_ready", "stack_parent": RECORD}
    ports.documents.save(raw)
    reads: list[int] = []
    real = ports.tracker.issue_state
    ports.tracker.issue_state = lambda n: (reads.append(n), real(n))[1]
    orphans, by_issue = _orphans(ports, raw)
    assert list(orphans) == [CHILD]
    assert by_issue[CHILD] == rules.BlockerReport() and reads == []


def test_a_non_stacked_orphan_or_one_with_an_illegible_record_stays_held_by_its_blocker():
    for raw in (
        {"id": f"GH-{CHILD}", "status": "definition_ready"},
        {"id": f"GH-{CHILD}", "status": "definition_ready", "stack_parent": {"issue": "x"}},
    ):
        ports = _ports()
        ports.documents.save(raw)
        orphans, by_issue = _orphans(ports, raw)
        assert orphans == {} and by_issue[CHILD] == rules.BlockerReport(open=(PARENT,))


# -- the launch seam: run_once with port doubles ------------------------------------------


def _launch_round(ports, registry, tmp_path):
    launcher = FakeLauncher()
    ports.tracker._queue = [CHILD]
    lines = _round(Configuration(product_dir=tmp_path), ports, registry, launcher, NOW)
    return lines, launcher


def test_with_the_switch_absent_the_parent_stays_a_blocker_and_no_hint_is_made(tmp_path):
    ports = _ports()
    registry = MemoryRunRegistry()
    lines, launcher = _launch_round(ports, registry, tmp_path)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == [] and registry.attempts == {}
    assert ports.workspace.remote_tip_calls == []


def test_with_the_switch_on_a_child_of_a_ready_parent_launches_and_records_the_hint(tmp_path):
    ports = _ports()
    ports.stack.declared_stacking = True
    registry = MemoryRunRegistry()
    lines, launcher = _launch_round(ports, registry, tmp_path)
    assert lines == [f"agentic: poll: launch #{CHILD} -> pid 4242"]
    attempt = registry.launch_attempts(ports.stack.product(), CHILD)
    assert attempt is not None and attempt.prior == 1
    assert attempt.stack_hint == StackHint(PARENT, HEAD)
    assert len(launcher.spawned) == 1


def test_a_relaunch_without_stacking_clears_the_old_hint(tmp_path):
    ports = _ports()
    ports.tracker.states[CHILD] = IssueState("OPEN", "", (), None)  # no blocker any more
    registry = MemoryRunRegistry()
    product = ports.stack.product()
    registry.record_launch(product, CHILD, NOW - timedelta(days=2), stack_hint=StackHint(5, HEAD))
    lines, _ = _launch_round(ports, registry, tmp_path)
    assert lines == [f"agentic: poll: launch #{CHILD} -> pid 4242"]
    attempt = registry.launch_attempts(product, CHILD)
    assert attempt is not None and attempt.prior == 2 and attempt.stack_hint is None


def test_a_parent_that_is_not_ready_leaves_the_child_blocked_with_no_attempt(tmp_path):
    ports = _ports(parent=_ready() | {"status": "in_review"})
    ports.stack.declared_stacking = True
    registry = MemoryRunRegistry()
    lines, launcher = _launch_round(ports, registry, tmp_path)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert launcher.spawned == [] and registry.attempts == {}


def test_an_unreadable_switch_reads_as_off(tmp_path):
    ports = _ports()
    ports.stack.declared_stacking = True
    ports.stack.stacking = lambda revision: (_ for _ in ()).throw(ValueError("not a boolean"))
    registry = MemoryRunRegistry()
    lines, launcher = _launch_round(ports, registry, tmp_path)
    assert launcher.spawned == [] and registry.attempts == {}
    assert ports.workspace.remote_tip_calls == []
