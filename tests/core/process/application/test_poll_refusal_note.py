"""GH-895: the poller comments the four `POLLER_NOTED` refusals once per (head, reason) and records
`refusal_noted`. In-memory ports with a recording tracker and recording documents (not
`dry_run.read_only_ports`: the guard under test is the round's own `dry_run`, not the proxy)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueComment, IssueState, Upstream
from agentic.core.process.application.refusal_note import AUTO_MERGE_REFUSED, LEFT_TO_HUMAN
from agentic.core.process.application.stack_facts import StackMemo
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import (
    VERDICT_SYNC,
    StackParent,
    TrackerFailure,
    refusal_noted_record,
)
from tests.core.process.application.test_poll import _cfg, _Locks
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _ready
from tests.fakes import MemoryDocuments, MemoryTracker, memory_ports

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
PARENT = "c" * 40
CLOSING = "f" * 40
ORPHANED = "stack_base_orphaned"
OTHER = "criteria_unreadable"
FIRST = f"{AUTO_MERGE_REFUSED}{HEAD} {ORPHANED}"


class _Tracker(MemoryTracker):
    """Counts every tracker call the note path makes, and scripts the failures of the read."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        # Rounds run: the round's own assessment reads the thread once per ready task (GH-784).
        self.rounds = 0
        self.notes = 0
        self.read_fails_from: int | None = None
        # Run inside a `note` call, before it answers: a write another process makes meanwhile.
        self.during_note: Any = None

    def timestamped_comments(self, task_id):
        self.reads += 1
        if self.read_fails_from is not None and self.reads >= self.read_fails_from:
            raise ValueError("gh issue view failed: scripted")
        return super().timestamped_comments(task_id)

    def note(self, task_id, text):
        self.notes += 1
        if self.during_note is not None:
            self.during_note(task_id)
        return super().note(task_id, text)


class _Documents(MemoryDocuments):
    def __init__(self) -> None:
        super().__init__()
        self.closing_calls: list[tuple[str, dict[str, object]]] = []

    def record_closure(self, task_id, values):
        self.closing_calls.append((task_id, dict(values)))
        return super().record_closure(task_id, values)


def _stacked(**more):
    """One task, #5, whose pull request is refused `stack_base_orphaned` (its parent closed
    without its head reaching the base): a refusal a person owns, noted by the poller."""
    ports = memory_ports(tracker=_Tracker(), documents=_Documents())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False
    record = StackParent(issue=9, task_id="GH-9", head=PARENT, started_at="2026-11-16T10:00:00")
    _ready(ports, 5, stack_parent=record, **more)
    ports.tracker.states[9] = IssueState("CLOSED", "COMPLETED", (), CLOSING)
    ports.workspace.ancestors_by_ref[CLOSING] = set()
    return ports


def _round(ports, *, held=False, dry_run=False, busy=frozenset(), locks=None, now=NOW):
    lines: list[str] = []
    ports.tracker.rounds += 1
    poll.gather_merges(
        ports,
        frozenset(),
        MASTER,
        locks or _Locks(),
        lines,
        now=now,
        busy=busy,
        cfg=_cfg(Path("/p")),
        held=held,
        dry_run=dry_run,
        memo=StackMemo(),
    )
    return lines


def _note_reads(ports) -> int:
    """The reads beyond the one the round's own assessment makes per ready task."""
    return ports.tracker.reads - ports.tracker.rounds


def _calls(ports) -> int:
    """The tracker calls and document writes the note path makes."""
    written = [
        values
        for _, values in ports.documents.closing_calls
        if "refusal_noted" in values or "tracker_failures" in values
    ]
    return max(_note_reads(ports), 0) + ports.tracker.notes + len(written)


def _noted(lines: list[str]) -> list[str]:
    return [line for line in lines if "refusal note" in line]


def test_the_first_round_comments_and_records():
    ports = _stacked()

    lines = _round(ports)

    assert [text.splitlines()[0] for _, text in ports.tracker.comments] == [FIRST]
    assert ports.documents.load("GH-5")["refusal_noted"] == refusal_noted_record(
        HEAD, ORPHANED, NOW.isoformat()
    )
    assert f"agentic: poll: auto-merge #5 -> refusal noted: {ORPHANED}" in lines


def _force(monkeypatch, reasons: list[str]) -> list[str]:
    """Every assessment is refused with `reasons[0]`, whatever the document says (the list is
    the caller's to change between rounds)."""
    original = poll.assess_merge

    def assessed(*args, **kwargs):
        result = original(*args, **kwargs)
        return dataclasses.replace(result, decision=rules.MergeDecision("refuse", reasons[0]))

    monkeypatch.setattr(poll, "assess_merge", assessed)
    return reasons


def _reset(ports) -> None:
    ports.tracker.reads = ports.tracker.notes = ports.tracker.rounds = 0
    ports.documents.closing_calls.clear()


def _failures(ports) -> list[dict[str, str]]:
    rows = ports.documents.load("GH-5").get("tracker_failures")
    return rows if isinstance(rows, list) else []


def test_a_second_round_with_the_same_pair_makes_zero_tracker_calls():
    ports = _stacked()
    _round(ports)
    _reset(ports)

    lines = _round(ports)

    assert _calls(ports) == 0
    assert len(ports.tracker.comments) == 1
    assert _noted(lines) == []


def test_a_pair_the_verb_already_commented_with_the_full_sha_is_recorded_without_a_comment():
    ports = _stacked()
    verb = f"{FIRST}\n\nThe commit this task was built on will never reach the base branch."
    ports.tracker.timestamped["GH-5"] = [IssueComment(verb, "2026-11-17T09:00:00Z")]

    lines = _round(ports)

    assert ports.tracker.comments == []
    assert ports.tracker.notes == 0
    assert ports.documents.load("GH-5")["refusal_noted"]["head"] == HEAD
    assert f"agentic: poll: auto-merge #5 -> refusal noted: {ORPHANED}" in lines


def test_reasons_a_b_a_on_one_head_give_two_comments_and_three_records(monkeypatch):
    """The poller skips by `said` (any earlier comment of this head and reason, ever), not by the
    verb's rule (only the LAST refusal comment counts): the third round does not comment A again
    but records it, so the pair `(head, A)` is stored and the next round reads nothing."""
    ports = _stacked()
    current = _force(monkeypatch, [])
    scripted = iter([ORPHANED, OTHER, ORPHANED])
    for _ in range(3):
        current[:] = [next(scripted)]
        _round(ports)

    posted = [text.splitlines()[0] for _, text in ports.tracker.comments]
    assert posted == [FIRST, f"{AUTO_MERGE_REFUSED}{HEAD} {OTHER}"]
    records = [
        values["refusal_noted"]["reason"]
        for _, values in ports.documents.closing_calls
        if "refusal_noted" in values
    ]
    assert records == [ORPHANED, OTHER, ORPHANED]


def test_a_failed_note_lands_in_tracker_failures_with_no_record():
    ports = _stacked()
    ports.tracker.comment_failures = 1

    lines = _round(ports)

    assert "refusal_noted" not in ports.documents.load("GH-5")
    assert [row["target"] for row in _failures(ports)] == ["comment"]
    assert _noted(lines) == []


def test_a_tracker_failures_row_written_after_raw_was_read_survives_a_failed_note():
    ports = _stacked()
    ports.tracker.comment_failures = 1
    other = TrackerFailure(target="label", cls="transient", at="2026-11-17T11:00:00Z").record()

    def concurrently(task_id: str) -> None:
        ports.documents.record_closure(task_id, {"tracker_failures": [other]})

    ports.tracker.during_note = concurrently

    _round(ports)

    assert [row["target"] for row in _failures(ports)] == ["label", "comment"]


def test_a_note_that_fails_three_rounds_in_a_row_leaves_one_row_and_no_record():
    ports = _stacked()
    ports.tracker.comment_failures = 3

    for _ in range(3):
        _round(ports)

    assert ports.tracker.notes == 3
    assert len(_failures(ports)) == 1
    assert "refusal_noted" not in ports.documents.load("GH-5")


def test_a_failed_read_gives_no_comment_no_record_and_the_unread_line():
    ports = _stacked()
    ports.tracker.read_fails_from = 2  # the round's own assessment read is the first

    lines = _round(ports)

    assert ports.tracker.comments == []
    assert ports.tracker.notes == 0
    assert "refusal_noted" not in ports.documents.load("GH-5")
    assert "agentic: poll: auto-merge #5 -> refusal note unread" in lines


def test_an_empty_read_comments_and_records():
    ports = _stacked()
    ports.tracker.timestamped["GH-5"] = []

    _round(ports)

    assert _note_reads(ports) == 1
    assert [text.splitlines()[0] for _, text in ports.tracker.comments] == [FIRST]
    assert ports.documents.load("GH-5")["refusal_noted"]["head"] == HEAD


def test_a_person_owned_waiting_pull_request_is_left_for_the_human_to_merge():
    ports = _stacked()
    _ready(ports, 6)
    ports.documents.record_closure("GH-6", {"landing_queued_at": "2026-11-17T08:00:00"})

    lines = _round(ports)

    assert not any("#5 -> waiting" in line for line in lines)
    [(_, text)] = ports.tracker.comments
    assert text.endswith(LEFT_TO_HUMAN)
    assert not any(body.startswith("Landing: ") for _, body in ports.tracker.comments)


def test_a_head_whose_diagnosis_is_a_sync_not_yet_synced_has_no_closing_and_no_landing_line(
    monkeypatch,
):
    diagnosis = {
        "head": HEAD,
        "step": "diagnose",
        "refusal": "merge_failed",
        "delivered": True,
        "verdict": VERDICT_SYNC,
        "upstream": MASTER,
    }
    ports = _stacked(landing=[diagnosis])
    _force(monkeypatch, [ORPHANED])
    # A person's task is never the order's Head (`waits_on_human`), so the real round gives this
    # one the `waiting` turn, whose table row is `none` before the verdict is read: the turn is
    # scripted to reach the Head's row, where the `sync_master` the diagnosis owes takes the
    # place of the closing.
    monkeypatch.setattr(poll.landing_order, "turn_of", lambda *_: "head")

    _round(ports)

    [(_, text)] = ports.tracker.comments
    assert LEFT_TO_HUMAN not in text
    assert text.splitlines()[0] == FIRST
    assert not any(body.startswith("Landing: ") for _, body in ports.tracker.comments)
    assert ports.documents.load("GH-5")["refusal_noted"]["head"] == HEAD


@pytest.mark.parametrize("case", ["held", "busy", "lock", "dry_run"])
def test_a_round_that_must_not_write_writes_nothing_and_asks_the_tracker_nothing(case):
    ports = _stacked()
    options: dict[str, Any] = {
        "held": {"held": True},
        "busy": {"busy": frozenset({"GH-5"})},
        "lock": {"locks": _Locks(alive={"GH-5"})},
        "dry_run": {"dry_run": True},
    }[case]

    lines = _round(ports, **options)

    assert _calls(ports) == 0
    assert ports.tracker.comments == []
    assert "refusal_noted" not in ports.documents.load("GH-5")
    assert _noted(lines) == []
