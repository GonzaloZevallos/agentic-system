"""`board_sync.mirror` (GH-375, ADR-0071): the issue rows `gh issue list` gives, plus real-shaped
task documents, in; the card writes and the sent-memory out. The Project is never read back."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from agentic.core.process.application import board_sync
from agentic.core.process.application.ports import IssueRow, SentMemory, TrackerCallFailed
from agentic.core.process.domain import rules
from tests.fakes import FakeBoard, FakeBoardSent, MemoryDocuments, memory_ports

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
AGENTIC = frozenset({"agentic"})


def row(number: int, labels: frozenset[str] = AGENTIC, closed: date | None = None) -> IssueRow:
    return IssueRow(number, "closed" if closed else "open", labels, closed)


def mirror(ports, *, live=frozenset(), blockers=None, eval_set=frozenset(), now=NOW):
    return board_sync.mirror(
        ports, now, live_issues=live, blockers=blockers or {}, eval_set=eval_set
    )


def documents(**by_issue: dict) -> MemoryDocuments:
    docs = MemoryDocuments()
    for issue, fields in by_issue.items():
        docs.save({"id": f"GH-{issue.removeprefix('i')}", **fields})
    return docs


def etapas(board: FakeBoard) -> dict[int, str]:
    return {w.issue: str(w.value) for w in board.written if w.field == "etapa"}


def ports_with(board, docs=None, sent=None):
    sent = sent if sent is not None else FakeBoardSent()
    return memory_ports(documents=docs, board=board, board_sent=sent), sent


def test_no_board_means_no_lines_and_no_call() -> None:
    assert mirror(memory_ports()) == []


def test_each_row_of_the_table_gets_its_etapa() -> None:
    docs = documents(
        i1={"status": "ready_for_review"},  # in the eval set: the human merges
        i2={"status": "ready_for_review"},  # outside it: nobody owes anything
        i3={"status": "discarded"},
        i5={"status": "waiting_on_human", "escalated": "Which scope?\nsecond line"},
    )
    board = FakeBoard(
        open=[
            row(1),
            row(2),
            row(3),
            row(4),
            row(5),
            row(6, frozenset({"agentic", "ready-for-human"})),
            row(7, frozenset({"needs-human"})),
            row(8, frozenset()),
            row(9),
            row(10),
        ],
        closed=[row(11, closed=date(2026, 10, 20))],
    )
    ports, _ = ports_with(board, docs)
    blockers = {9: rules.BlockerReport(open=(3,)), 10: rules.BlockerReport()}
    mirror(ports, live=frozenset({4}), blockers=blockers, eval_set=frozenset({1}))
    assert etapas(board) == {
        1: "Necesita humano",
        2: "En cola",
        3: "Necesita humano",
        4: "Corriendo",
        5: "Necesita humano",
        6: "Fuera del sistema",
        7: "Necesita humano",
        8: "Fuera del sistema",
        9: "Trabada",
        10: "En cola",
        11: "Hecha",
    }
    bloqueo = {w.issue: w.value for w in board.written if w.field == "bloqueo"}
    assert bloqueo[9] == "#3" and bloqueo[5] == "Which scope?"
    assert {w.value for w in board.written if w.field == "fin"} == {date(2026, 10, 20)}


def test_cost_and_first_run_day_come_from_the_document_through_the_mirror() -> None:
    docs = documents(
        i1={
            "status": "developing",
            "cost_usd": 3.456,
            "landing_cost_carried": 1.0,
            "runs": [
                {"role": "developer", "started_at": "2026-10-22T23:30:00-03:00"},
                {"role": "tech_lead", "started_at": "2026-10-21T10:00:00+00:00"},
                {"role": "reviewer", "started_at": "not a date"},
            ],
        },
    )
    board = FakeBoard(open=[row(1), row(2)])
    ports, _ = ports_with(board, docs)
    mirror(ports)
    by_field = {(w.issue, w.field): w.value for w in board.written}
    assert by_field[(1, "costo")] == 4.46
    assert by_field[(1, "inicio")] == date(2026, 10, 21)
    assert (2, "costo") not in by_field and (2, "inicio") not in by_field


def test_unreadable_eval_set_keeps_the_pr_with_the_human() -> None:
    board = FakeBoard(open=[row(1)])
    ports, _ = ports_with(board, documents(i1={"status": "ready_for_review"}))
    mirror(ports, eval_set=None)
    assert etapas(board) == {1: "Necesita humano"}


def test_unchanged_issue_makes_no_write_and_only_the_open_read() -> None:
    board = FakeBoard(open=[row(1)])
    ports, _ = ports_with(board)
    mirror(ports)
    board.calls.clear()
    assert mirror(ports) == []
    assert [name for name, _ in board.calls] == ["project_id", "open_issues"]


def test_a_launch_this_round_is_corriendo() -> None:
    board = FakeBoard(open=[row(1)])
    ports, _ = ports_with(board)
    mirror(ports, live=frozenset({1}))
    assert etapas(board) == {1: "Corriendo"}


@pytest.mark.parametrize("failure", [TrackerCallFailed("x"), ValueError("x"), OSError("x")])
def test_a_failing_board_is_one_line_and_rests_fifteen_minutes(failure: Exception) -> None:
    board = FakeBoard(open=[row(1)], fail=failure)
    ports, sent = ports_with(board)
    lines = mirror(ports)
    assert lines == [
        f"agentic: poll: board: not updated ({type(failure).__name__}), next try in 15 min"
    ]
    board.calls.clear()
    assert mirror(ports, now=NOW + timedelta(minutes=5)) == []
    assert board.calls == []
    board.fail = None
    mirror(ports, now=NOW + timedelta(minutes=16))
    assert etapas(board) == {1: "En cola"}
    assert sent.memory.failed_at is None


def test_a_partial_write_saves_only_what_was_written() -> None:
    board = FakeBoard(open=[row(1), row(2)], unwritten=[2])
    ports, sent = ports_with(board)
    mirror(ports)
    assert set(sent.memory.issues) == {1}
    board.unwritten.clear()
    board.written.clear()
    mirror(ports)
    assert {w.issue for w in board.written} == {2}


def test_a_new_project_discards_the_memory() -> None:
    board = FakeBoard(open=[row(1)])
    ports, sent = ports_with(board)
    mirror(ports)
    board.written.clear()
    board.project = "PVT_2"
    mirror(ports)
    assert etapas(board) == {1: "En cola"}
    assert sent.memory.project_id == "PVT_2"


def test_an_unreadable_memory_says_so_once_and_sends_everything() -> None:
    board = FakeBoard(open=[row(1)])
    ports, _ = ports_with(board, sent=FakeBoardSent(SentMemory(unreadable=True)))
    lines = mirror(ports)
    assert lines == ["agentic: poll: board: memory unreadable, sending everything again"]
    assert etapas(board) == {1: "En cola"}


def test_a_remembered_issue_that_closed_is_read_one_by_one_and_goes_hecha() -> None:
    board = FakeBoard(open=[row(1)])
    ports, _ = ports_with(board)
    mirror(ports)
    board.open, board.closed, board.written = [], [row(1, closed=date(2026, 10, 27))], []
    mirror(ports)
    assert etapas(board) == {1: "Hecha"}
    assert ("issue", 1) in board.calls
    board.calls.clear()
    mirror(ports)
    assert [name for name, _ in board.calls] == ["project_id", "open_issues"]
