"""GH-375, ADR-0073: the board mirror inside `poll.run_once`, against `FakeBoard` and
`FakeBoardSent`. The round's own lines are the ones it had before the mirror; the mirror adds
at most its own notes and never fails the round."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.process.application import board_sync, poll, stack_hold
from agentic.core.process.application.ports import IssueRow, IssueState, TrackerCallFailed
from agentic.core.process.domain import board as domain
from agentic.shared.configuration import Configuration
from tests.fakes import (
    FakeBoard,
    FakeBoardSent,
    FakeLauncher,
    MemoryDocuments,
    MemoryRunRegistry,
    memory_ports,
)

TE_NECESITA, EN_CURSO, PROXIMO, EN_PAUSA, HECHO = get_args(domain.Etapa)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
AGENTIC = frozenset({"agentic"})


class _NoLocks:
    """A `RunLock` with no run on record: the poller only reads it, never takes it."""

    def read(self, task_id):
        return None

    def refusal(self, task_id):
        return None


def _round(tmp_path, ports, launcher=None, now=NOW):
    return poll.run_once(
        Configuration(product_dir=tmp_path),
        ports,
        MemoryRunRegistry(),
        launcher or FakeLauncher(),
        now,
        sleep=lambda _: None,
        argv0="agentic",
        lock=_NoLocks(),
        current_boot=None,
    )


def _queued(board=None, board_sent=None):
    """Issue 5 is open and queued with an open blocker `3`; issue 6 is queued and free."""
    ports = memory_ports(board=board, board_sent=board_sent)
    ports.tracker._queue = [5, 6]
    ports.tracker.states[5] = IssueState("OPEN", "", (3,), None)
    ports.tracker.states[3] = IssueState("OPEN", "", None, None)
    ports.tracker.states[6] = IssueState("OPEN", "", None, None)
    for number in (5, 6):  # no native blockers: the poller reads the body, so seed one
        ports.inbox.issues[str(number)] = {"id": f"GH-{number}", "title": "t", "description": "x"}
    return ports


def _rows():
    return [IssueRow(n, "open", AGENTIC, None) for n in (3, 5, 6)]


def _etapas(board: FakeBoard) -> dict[int, str]:
    return {w.issue: str(w.value) for w in board.written if w.field == "etapa"}


def test_without_a_board_the_round_says_exactly_what_it_said_before(tmp_path):
    lines = _round(tmp_path, _queued())
    assert lines == ["agentic: poll: launch #6 -> pid 4242"]


def test_a_board_adds_no_line_when_it_succeeds_and_writes_the_rounds_facts(tmp_path):
    board = FakeBoard(open=_rows())
    ports = _queued(board, FakeBoardSent())
    launcher = FakeLauncher()
    lines = _round(tmp_path, ports, launcher)
    assert lines == ["agentic: poll: launch #6 -> pid 4242"]
    # 6 is launched this round (the registry does not show it yet), 5 waits on the open 3.
    assert _etapas(board)[6] == EN_CURSO
    assert _etapas(board)[5] == PROXIMO
    assert {w.value for w in board.written if w.field == "motivo" and w.issue == 5} == {
        f"{domain.BLOCKER_PREFIX}#3"
    }


def test_an_idle_second_round_reads_the_open_issues_and_calls_nothing_else(tmp_path):
    board = FakeBoard(open=_rows())
    ports = _queued(board, FakeBoardSent())
    _round(tmp_path, ports)
    board.calls.clear()
    _round(tmp_path, ports)
    assert [name for name, _ in board.calls] == ["project_id", "open_issues"]


@pytest.mark.parametrize("failure", [TrackerCallFailed("x"), ValueError("x"), OSError("x")])
def test_a_failing_board_does_not_fail_the_round(tmp_path, failure):
    board = FakeBoard(open=_rows(), fail=failure)
    ports = _queued(board, FakeBoardSent())
    lines = _round(tmp_path, ports)
    name = type(failure).__name__
    shown = f"{name}: x" if isinstance(failure, ValueError) else name
    note = f"agentic: poll: board: not updated ({shown}), next try in 15 min"
    assert sorted(lines) == sorted(["agentic: poll: launch #6 -> pid 4242", note])
    board.calls.clear()
    again = _round(tmp_path, ports, now=NOW + timedelta(minutes=5))
    assert board.calls == []
    assert not any("board" in line for line in again)


def test_a_closed_issue_is_hecho_with_its_day(tmp_path):
    board = FakeBoard(closed=[IssueRow(9, "closed", AGENTIC, date(2026, 10, 20))])
    ports = memory_ports(board=board, board_sent=FakeBoardSent())
    lines = _round(tmp_path, ports)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert _etapas(board) == {9: HECHO}


def test_the_mirror_gets_the_stack_pauses_the_round_held_its_children_with(tmp_path, monkeypatch):
    held = stack_hold.StackHold(
        held={5: "parent_stopped", 6: "unreadable"},
        causes={5: "parent_stopped", 6: "unreadable"},
        culprits={5: 40},
    )
    monkeypatch.setattr(stack_hold, "compute", lambda *args, **kwargs: held)
    monkeypatch.setattr(stack_hold, "write_episodes", lambda *args, **kwargs: [])
    seen: list[dict] = []
    real = board_sync.mirror

    def spy(*args, **kwargs):
        seen.append(dict(kwargs["stack_pauses"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(board_sync, "mirror", spy)
    board = FakeBoard(open=_rows())
    docs = MemoryDocuments()
    for number in (5, 6):
        docs.save({"id": f"GH-{number}", "status": "developing"})
    ports = memory_ports(documents=docs, board=board, board_sent=FakeBoardSent())
    ports.tracker._queue = [5, 6]
    _round(tmp_path, ports)
    assert seen == [{5: ("parent_stopped", 40), 6: ("unreadable", None)}]
    motivo = {w.issue: w.value for w in board.written if w.field == "motivo"}
    assert motivo[5] == domain.STACK_STOPPED.format(40)
    assert motivo[6] == domain.STACK_UNREADABLE


def test_a_round_with_no_held_child_gives_the_mirror_no_pauses(tmp_path, monkeypatch):
    seen: list[dict] = []
    real = board_sync.mirror

    def spy(*args, **kwargs):
        seen.append(dict(kwargs["stack_pauses"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(board_sync, "mirror", spy)
    _round(tmp_path, _queued(FakeBoard(open=_rows()), FakeBoardSent()))
    assert seen == [{}]
