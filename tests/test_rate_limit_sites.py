"""GH-996: a rate-limited `gh` call is a `TrackerRateLimited`, and every caller that already ends
on a `TrackerUnreachable` must end the same way on it. Each test raises the same failure once as
each class at one site and compares the outcomes, then pins the two sites whose outcome moved:
the `pr merge` cause and `deliver._transient`."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentic.core.process.adapters.github.tracker import GhTracker
from agentic.core.process.application import board_sync, deliver, poll
from agentic.core.process.application.ports import (
    IssueRow,
    MergeCallFailed,
    TrackerCallFailed,
    TrackerRateLimited,
    TrackerUnreachable,
)
from agentic.core.process.application.split_sweep import RepointResult, repoint_dependents
from agentic.core.process.application.stack_facts import StackMemo, stack_facts
from agentic.core.process.application.worktree_sweep import sweep
from agentic.core.process.domain import stacking
from agentic.core.process.domain.landing import IssueRef
from agentic.core.process.domain.task import StackParent
from agentic.core.process.domain.worktree_sweep import SweepMemory
from agentic.shared.configuration import Configuration
from tests.fakes import FakeBoard, FakeBoardSent, MemoryInbox, memory_ports
from tests.fakes_worktree_sweep import Clock, Lock, world

RATE_LIMITED = "GraphQL: API rate limit already exceeded for user ID 1."
UNREACHABLE = "error connecting to api.github.com"
FAILURES = [
    pytest.param(TrackerRateLimited("rate limited"), id="rate_limited"),
    pytest.param(TrackerUnreachable("unreachable"), id="unreachable"),
]


def test_worktree_sweep_stops_the_reads_of_the_round_for_both() -> None:
    outcomes = []
    for failure in (TrackerRateLimited("x"), TrackerUnreachable("x")):
        ports, workspace, tracker = world("GH-1", "GH-2", closed=(1, 2))
        tracker.failure = failure
        lines, memory = sweep(ports, Lock(), SweepMemory(), Clock())
        outcomes.append((tracker.reads, lines, workspace.removed_checkouts, memory.memos))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] == [1] and outcomes[0][3]["GH-1"][1] == "read_failed"


@pytest.mark.parametrize("failure", FAILURES)
def test_read_open_refs_prints_the_base_class_and_no_refs(failure: Exception) -> None:
    inbox = MemoryInbox()
    inbox.refs_error = failure
    refs, line = poll.read_open_refs(memory_ports(inbox=inbox))
    assert refs == []
    assert line == "agentic: poll: dependents from queue only (TrackerCallFailed)"


def _repoint(failure: Exception) -> tuple[RepointResult, list[tuple[int, str]]]:
    inbox = MemoryInbox(
        **{n: {"id": f"GH-{n}", "title": "t", "description": "Blocked by: #5"} for n in "78"}
    )
    inbox.task_exceptions[7] = failure
    ports = memory_ports(inbox=inbox)
    ports.tracker.inbox = inbox
    refs = [IssueRef(f"https://github.com/o/r/issues/{n}", "t", "Blocked by: #5") for n in (7, 8)]
    result = repoint_dependents(ports, 5, [9], refs, {5})
    return result, ports.tracker.updated_bodies


def test_split_sweep_ends_the_round_for_both() -> None:
    limited, limited_bodies = _repoint(TrackerRateLimited("x"))
    unreachable, unreachable_bodies = _repoint(TrackerUnreachable("x"))
    assert limited_bodies == unreachable_bodies == []  # #8 was never reached
    assert (limited.stopped, limited.considered) == (unreachable.stopped, unreachable.considered)
    assert (limited.stopped_by, unreachable.stopped_by) == (
        "TrackerRateLimited",
        "TrackerUnreachable",
    )


def _stack(tmp_path: Path, stderr: str) -> tuple[list[str], stacking.StackFacts]:
    calls: list[list[str]] = []

    def run(args: list[str], cwd: Any, capture_output: Any, text: Any, check: Any) -> Any:
        calls.append(args)
        raise subprocess.CalledProcessError(1, args, stderr=stderr)

    ports: Any = SimpleNamespace(
        workspace=SimpleNamespace(
            fetch_commit=lambda sha: True, is_ancestor=lambda path, sha, ref="HEAD": False
        ),
        tracker=GhTracker(tmp_path, run, log=lambda line: None),
    )
    record = StackParent(issue=7, task_id="GH-7", head="c" * 40, started_at="2026-11-16T10:00:00")
    unread = stacking.facts_of({"stack_parent": record})
    assert unread is not None
    facts = stack_facts(
        ports, Configuration(product_dir=tmp_path), unread, "e" * 40, "d" * 40, StackMemo()
    )
    return calls[0][:2], facts


def test_stack_facts_reads_the_parent_as_unknown_for_both(tmp_path: Path) -> None:
    limited = _stack(tmp_path, RATE_LIMITED)
    unreachable = _stack(tmp_path, UNREACHABLE)
    assert limited == unreachable
    assert limited[1].parent_state == stacking.parent_state_of("unknown", "unread")


@pytest.mark.parametrize("failure", FAILURES)
def test_board_sync_rests_and_writes_nothing_for_both(failure: Exception) -> None:
    board, sent = (
        FakeBoard(open=[IssueRow(1, "open", frozenset({"agentic"}), None)], fail=failure),
        FakeBoardSent(),
    )
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    ports = memory_ports(board=board, board_sent=sent)
    (line,) = board_sync.mirror(
        ports, now, live_issues=frozenset(), blockers={}, eval_set=frozenset()
    )
    reason = f"{type(failure).__name__}: {failure}"
    assert line == f"agentic: poll: board: not updated ({reason}), next try in 15 min"
    assert board.written == [] and sent.load().failed_at == now


def _tracker(tmp_path: Path, stderr: str, failing: str) -> GhTracker:
    def run(args: list[str], cwd: Any, capture_output: Any, text: Any, check: Any) -> Any:
        if args[1:3] == ["repo", "view"]:
            return subprocess.CompletedProcess(args, 0, '{"squashMergeAllowed": true}', "")
        if args[1] == failing:
            raise subprocess.CalledProcessError(1, args, stderr=stderr)
        return subprocess.CompletedProcess(args, 0, "", "")

    return GhTracker(tmp_path, run, log=lambda line: None, now=lambda: "2026-11-27T00:00:00Z")


@pytest.mark.parametrize(
    ("stderr", "cause"),
    [(RATE_LIMITED, "rate_limited"), (UNREACHABLE, "unreachable"), ("not mergeable", None)],
)
def test_a_tracker_note_records_the_class_and_a_plain_failure_still_raises(
    tmp_path: Path, stderr: str, cause: str | None
) -> None:
    tracker = _tracker(tmp_path, stderr, "api")  # a comment goes through `gh api -i` (GH-1089)
    if cause is None:
        with pytest.raises(ValueError, match="gh issue comment failed"):
            tracker.note("GH-7", "text")
        return
    failure = tracker.note("GH-7", "text")
    assert failure is not None and (failure.target, failure.cls) == ("comment", cause)


@pytest.mark.parametrize(
    ("stderr", "cause"),
    [
        (RATE_LIMITED, "gh_unreadable"),
        (UNREACHABLE, "gh_unreadable"),
        ("not mergeable", "gh_rejected"),
    ],
)
def test_a_failed_pr_merge_is_unreadable_for_a_transport_class_and_rejected_for_a_refusal(
    tmp_path: Path, stderr: str, cause: str
) -> None:
    with pytest.raises(MergeCallFailed) as raised:
        _tracker(tmp_path, stderr, "pr").merge("https://github.com/o/r/pull/7", "a" * 40)
    assert raised.value.failure == cause


@pytest.mark.parametrize(
    ("failure", "transient"),
    [
        (TrackerRateLimited("x"), True),
        (TrackerUnreachable("x"), True),
        (TrackerCallFailed("x"), True),
        (ValueError("x"), False),
    ],
)
def test_deliver_treats_a_transport_class_as_transient_and_a_plain_value_error_as_not(
    failure: Exception, transient: bool
) -> None:
    assert deliver._transient(failure) is transient
