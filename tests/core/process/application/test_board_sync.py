"""`board_sync.mirror` (GH-375, ADR-0073): the issue rows `gh issue list` gives, plus real-shaped
task documents, in; the card writes and the sent-memory out. The Project is never read back."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from typing import get_args

import pytest

from agentic.core.process.application import board_sync
from agentic.core.process.application.ports import (
    IssueRow,
    SentEntry,
    SentMemory,
    TrackerCallFailed,
    TrackerTurnRefused,
)
from agentic.core.process.domain import board as domain
from agentic.core.process.domain import rules
from tests.fakes import FakeBoard, FakeBoardSent, MemoryDocuments, memory_ports

TE_NECESITA, EN_CURSO, PROXIMO, EN_PAUSA, HECHO = get_args(domain.Etapa)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
DAY = date(2026, 10, 27)
AGENTIC = frozenset({"agentic"})


def row(number: int, labels: frozenset[str] = AGENTIC, closed: date | None = None) -> IssueRow:
    return IssueRow(number, "closed" if closed else "open", labels, closed)


def mirror(ports, *, live=frozenset(), blockers=None, now=NOW):
    return board_sync.mirror(ports, now, live_issues=live, blockers=blockers or {})


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
        i1={"status": "ready_for_review"},
        i2={"status": "ready_for_review"},
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
    mirror(ports, live=frozenset({4}), blockers=blockers)
    assert etapas(board) == {
        1: EN_CURSO,
        2: EN_CURSO,
        3: TE_NECESITA,
        4: EN_CURSO,
        5: TE_NECESITA,
        6: TE_NECESITA,
        7: TE_NECESITA,
        8: EN_PAUSA,
        9: PROXIMO,
        10: PROXIMO,
        11: HECHO,
    }
    motivo = {w.issue: w.value for w in board.written if w.field == "motivo"}
    assert (
        motivo[9] == f"{domain.BLOCKER_PREFIX}#3"
        and motivo[5] == f"{domain.WAITING_ANSWER}: Which scope?"
    )
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
    assert etapas(board) == {1: EN_CURSO}


@pytest.mark.parametrize("failure", [TrackerCallFailed("x"), ValueError("x"), OSError("x")])
def test_a_failing_board_is_one_line_and_rests_fifteen_minutes(failure: Exception) -> None:
    board = FakeBoard(open=[row(1)], fail=failure)
    ports, sent = ports_with(board)
    lines = mirror(ports)
    name = type(failure).__name__
    shown = f"{name}: x" if isinstance(failure, ValueError) else name
    assert lines == [f"agentic: poll: board: not updated ({shown}), next try in 15 min"]
    board.calls.clear()
    assert mirror(ports, now=NOW + timedelta(minutes=5)) == []
    assert board.calls == []
    board.fail = None
    mirror(ports, now=NOW + timedelta(minutes=16))
    assert etapas(board) == {1: PROXIMO}
    assert sent.memory.failed_at is None


def failure_line(failure: Exception) -> str:
    return mirror(ports_with(FakeBoard(open=[row(1)], fail=failure))[0])[0]


def test_a_multi_line_value_error_shows_on_one_line() -> None:
    line = failure_line(ValueError(f"the board project o/3\n  has no\tEtapa option '{HECHO}'"))
    assert line == (
        "agentic: poll: board: not updated "
        f"(ValueError: the board project o/3 has no Etapa option '{HECHO}'), next try in 15 min"
    )


def test_a_long_value_error_is_cut_to_160_characters_with_an_ellipsis() -> None:
    line = failure_line(ValueError("y" * 300))
    assert f"(ValueError: {'y' * 159}…)" in line
    exact = failure_line(ValueError("z" * 160))
    assert f"(ValueError: {'z' * 160})" in exact


def test_a_json_decode_error_shows_its_subclass_name_and_message() -> None:
    line = failure_line(json.JSONDecodeError("Expecting value", "", 0))
    assert "(JSONDecodeError: Expecting value: line 1 column 1 (char 0))" in line


def test_an_os_error_with_a_message_shows_only_its_type() -> None:
    line = failure_line(OSError("/home/zeta/secret/path: permission denied"))
    assert "(OSError)" in line and "secret" not in line


def test_a_partial_write_saves_only_what_was_written() -> None:
    board = FakeBoard(open=[row(1), row(2)], unwritten=[2])
    ports, sent = ports_with(board)
    mirror(ports)
    assert set(sent.memory.issues) == {1}
    board.unwritten.clear()
    board.written.clear()
    mirror(ports)
    assert {w.issue for w in board.written} == {2}


def test_a_partial_transport_failure_keeps_what_was_sent_and_rests_the_rest() -> None:
    board = FakeBoard(open=[row(1), row(2)], unwritten=[2], complete=False)
    ports, sent = ports_with(board)
    assert mirror(ports) == []
    assert set(sent.memory.issues) == {1}
    assert sent.memory.failed_at == NOW
    board.written.clear()
    assert mirror(ports, now=NOW + timedelta(minutes=5)) == []
    assert board.written == []  # resting: no write
    board.complete = True
    board.unwritten.clear()
    mirror(ports, now=NOW + timedelta(minutes=16))
    assert {w.issue for w in board.written} == {2} and sent.memory.failed_at is None


def test_an_error_naming_an_issue_saves_partially_and_does_not_rest() -> None:
    board = FakeBoard(open=[row(1), row(2)], unwritten=[2], complete=True)
    ports, sent = ports_with(board)
    mirror(ports)
    assert set(sent.memory.issues) == {1} and sent.memory.failed_at is None


def test_nothing_written_and_incomplete_is_still_a_whole_failure() -> None:
    board = FakeBoard(open=[row(1)], unwritten=[1], complete=False)
    ports, sent = ports_with(board)
    assert mirror(ports) == ["agentic: poll: board: not updated (WriteFailed), next try in 15 min"]
    assert sent.memory.failed_at == NOW


def test_a_new_project_discards_the_memory() -> None:
    board = FakeBoard(open=[row(1)])
    ports, sent = ports_with(board)
    mirror(ports)
    board.written.clear()
    board.project = "PVT_2"
    mirror(ports)
    assert etapas(board) == {1: PROXIMO}
    assert sent.memory.project_id == "PVT_2"


def test_an_unreadable_memory_says_so_once_and_sends_everything() -> None:
    board = FakeBoard(open=[row(1)])
    ports, _ = ports_with(board, sent=FakeBoardSent(SentMemory(unreadable=True)))
    lines = mirror(ports)
    assert lines == ["agentic: poll: board: memory unreadable, sending everything again"]
    assert etapas(board) == {1: PROXIMO}


def test_a_remembered_issue_that_closed_is_read_one_by_one_and_goes_hecho() -> None:
    board = FakeBoard(open=[row(1)])
    ports, _ = ports_with(board)
    mirror(ports)
    board.open, board.closed, board.written = [], [row(1, closed=date(2026, 10, 27))], []
    mirror(ports)
    assert etapas(board) == {1: HECHO}
    assert ("issue", 1) in board.calls
    board.calls.clear()
    mirror(ports)
    assert [name for name, _ in board.calls] == ["project_id", "open_issues"]


def test_a_board_that_writes_nothing_keeps_the_cards_out_of_board_sent_and_the_next_round_resends():
    board = FakeBoard(open=[row(1)], unwritten={1})
    ports, sent = ports_with(board)
    mirror(ports)
    assert 1 not in sent.memory.issues
    board.unwritten = set()
    board.calls.clear()
    mirror(ports, now=NOW + timedelta(minutes=16))
    assert etapas(board) == {1: PROXIMO}
    assert 1 in sent.memory.issues


def test_a_refused_write_turn_rests_the_board_and_records_no_card():
    board = FakeBoard(open=[row(1)], fail=TrackerTurnRefused("gh api graphql write turn refused"))
    ports, sent = ports_with(board)
    lines = mirror(ports)
    assert len(lines) == 1 and "next try in 15 min" in lines[0]
    assert 1 not in sent.memory.issues


def closed_row(number: int, days_ago: int) -> IssueRow:
    return row(number, closed=DAY - timedelta(days=days_ago))


def test_the_day_is_the_utc_day_of_now() -> None:
    board = FakeBoard(open=[], closed=[closed_row(1, 7)])
    ports, _ = ports_with(board)
    late = datetime(
        2026, 10, 27, 23, 30, tzinfo=timezone(timedelta(hours=-3))
    )  # 02:30Z on the 28th
    mirror(ports, now=late)
    assert board.written == []  # closed on the 20th: 8 days on the 28th, cleared
    mirror(ports_with(FakeBoard(closed=[closed_row(1, 7)]))[0], now=NOW)


def test_a_full_read_adds_only_the_live_issue_not_the_old_closed_ones() -> None:
    board = FakeBoard(open=[row(1)], closed=[closed_row(n, 30) for n in (2, 3, 4)])
    ports, sent = ports_with(board)
    mirror(ports, live=frozenset({1}))
    assert {w.issue for w in board.written} == {1}
    assert set(sent.memory.issues) == {1, 2, 3, 4}  # cleared cards are remembered, not sent


def test_an_issue_remembered_open_and_closed_between_rounds_ends_hecho() -> None:
    board = FakeBoard(open=[row(1)])
    ports, sent = ports_with(board)
    mirror(ports)
    board.open, board.closed, board.written = [], [closed_row(1, 0)], []
    mirror(ports)
    assert etapas(board) == {1: HECHO}
    assert sent.memory.issues[1].hecho_until == DAY + timedelta(days=7)


def test_a_hecho_card_expires_after_seven_days_and_only_its_etapa_is_cleared() -> None:
    board = FakeBoard(closed=[closed_row(1, 0)])
    ports, sent = ports_with(board)
    mirror(ports)
    board.written.clear()
    mirror(ports, now=NOW + timedelta(days=7))
    assert board.written == []
    mirror(ports, now=NOW + timedelta(days=8))
    assert [(w.issue, w.field, w.value) for w in board.written] == [(1, "etapa", None)]
    assert sent.memory.issues[1].hecho_until is None
    board.calls.clear()
    mirror(ports, now=NOW + timedelta(days=9))
    assert [name for name, _ in board.calls] == ["project_id", "open_issues"]


def seeded(numbers, unread=None):
    until = DAY - timedelta(days=1)
    entries = {
        n: SentEntry("x", frozenset({"etapa", "fin"}), until, (unread or {}).get(n, 0))
        for n in numbers
    }
    return FakeBoardSent(SentMemory(project_id="PVT_1", issues=entries))


class FlakyBoard(FakeBoard):
    """Fails `issue(n)` for the numbers in `broken`."""

    broken: set[int]

    def issue(self, number: int) -> IssueRow:
        if number in self.broken:
            self._enter("issue", number)
            raise TrackerCallFailed("gone")
        return super().issue(number)


def test_an_unreadable_issue_says_so_once_and_is_remembered_as_unread() -> None:
    board = FlakyBoard(closed=[closed_row(2, 3)])
    board.broken = {1}
    ports, sent = ports_with(board, sent=seeded([1]))
    assert mirror(ports) == ["agentic: poll: board: #1 not read (TrackerCallFailed)"]
    assert sent.memory.issues[1].unread == 1
    assert mirror(ports) == []
    assert sent.memory.issues[1].unread == 2


def test_with_twelve_due_the_ten_oldest_unreadable_round_two_reads_the_two_readable_first() -> None:
    board = FlakyBoard(closed=[closed_row(n, 10) for n in (11, 12)])
    board.broken = set(range(1, 11))
    entries = {
        n: SentEntry("x", frozenset({"etapa", "fin"}), DAY - timedelta(days=20 - n))
        for n in range(1, 13)
    }
    ports, sent = ports_with(board, sent=FakeBoardSent(SentMemory("PVT_1", issues=entries)))
    mirror(ports)
    first = [n for name, n in board.calls if name == "issue"]
    assert first == list(range(1, 11))
    board.calls.clear()
    mirror(ports)
    second = [n for name, n in board.calls if name == "issue"]
    assert second == [11, 12, *range(1, 9)]  # the readable two first, then the cap's rest
    assert [sent.memory.issues[n].unread for n in range(1, 11)] == [2] * 8 + [1] * 2


def test_the_third_failed_read_drops_the_entry_once_and_it_is_not_read_again() -> None:
    board = FlakyBoard()
    board.broken = {1}
    ports, sent = ports_with(board, sent=seeded([1], unread={1: 2}))
    assert mirror(ports) == ["agentic: poll: board: #1 dropped from memory after 3 failed reads"]
    assert 1 not in sent.memory.issues
    board.calls.clear()
    assert mirror(ports) == []
    assert ("issue", 1) not in board.calls


def test_a_number_that_reappears_open_in_the_round_of_its_third_failure_keeps_its_entry() -> None:
    board = FlakyBoard(open=[row(1)])
    board.broken = {1}
    entries = {1: SentEntry("x", frozenset({"etapa"}), None, 2)}
    ports, sent = ports_with(board, sent=FakeBoardSent(SentMemory("PVT_1", issues=entries)))
    assert mirror(ports) == []
    assert sent.memory.issues[1].unread == 0 and "etapa" in sent.memory.issues[1].present


def test_a_card_due_with_no_writes_is_remembered_without_calling_write() -> None:
    board = FakeBoard(closed=[closed_row(1, 30)])
    ports, sent = ports_with(board)
    assert mirror(ports) == []
    assert "write" not in [name for name, _ in board.calls]
    assert 1 in sent.memory.issues


def test_a_memory_of_an_older_version_is_resent_in_full(tmp_path) -> None:
    from agentic.core.process.adapters.filesystem.board_sent import FilesystemBoardSent

    path = tmp_path / "board.json"
    issues = {"1": {"fingerprint": "x", "present": ["etapa"]}}
    path.write_text(json.dumps({"project_id": "PVT_1", "failed_at": None, "issues": issues}))
    board = FakeBoard(open=[row(1)])
    ports = memory_ports(board=board, board_sent=FilesystemBoardSent(path))
    assert mirror(ports) == ["agentic: poll: board: memory unreadable, sending everything again"]
    assert etapas(board) == {1: PROXIMO}
    assert FilesystemBoardSent(path).load().issues[1].fingerprint != "x"


def test_a_pause_the_poller_retakes_itself_reads_se_retoma_sola_through_the_document() -> None:
    pause_log = [
        {"kind": "failure", "value": "account_limit", "section": "Developer: api"},
        {"kind": "escalation", "value": "failure"},
    ]
    docs = documents(
        i1={"status": "waiting_on_human", "escalated": "limit", "pause_log": pause_log}
    )
    board = FakeBoard(open=[row(1)])
    ports, _ = ports_with(board, docs)
    mirror(ports)
    by_field = {w.field: w.value for w in board.written}
    assert (by_field["etapa"], by_field["motivo"]) == (TE_NECESITA, domain.RETAKES_ITSELF)
