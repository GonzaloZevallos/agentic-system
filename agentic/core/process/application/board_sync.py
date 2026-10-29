"""The board mirror's use case (GH-375, ADR-0073): once per poll round, put each issue's card
on the GitHub Project, only when it changed since the last send.

The Project is an output. This module reads issues off the tracker, builds `IssueFacts` from
what the poller already holds, asks `domain.board` for the card and for what is due, and
writes only that. The memory of what was sent (`BoardSent`) is what makes a quiet round cost
one read. A failing Project call is one log line and a 15 minute rest, never the round's end.
The `try` wraps port calls only; the domain is called outside it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from ..domain import board as domain
from ..domain import rules
from .ports import Board, BoardSent, IssueRow, Ports, SentEntry, SentMemory, TrackerCallFailed

_FAILURES = (TrackerCallFailed, ValueError, OSError)
_NOTHING_WRITTEN = "WriteFailed"


def _failed_line(reason: str) -> str:
    minutes = int(domain.BOARD_RETRY_AFTER.total_seconds() // 60)
    return f"agentic: poll: board: not updated ({reason}), next try in {minutes} min"


_REASON_LIMIT = 160


def _reason(exc: BaseException) -> str:
    """What goes in the warning's parentheses. A `ValueError` is raised by our own code or the
    JSON parser, so its text is safe to show (one line, cut); any other exception may carry
    `gh` output or paths, so only its type name shows."""
    name = type(exc).__name__
    if not isinstance(exc, ValueError):
        return name
    text = " ".join(str(exc).split())
    if len(text) > _REASON_LIMIT:
        text = text[: _REASON_LIMIT - 1] + "…"
    return f"{name}: {text}"


def _remember_failure(sent: BoardSent, memory: SentMemory, now: datetime, reason: str) -> list[str]:
    """Records `failed_at` so the next rounds stay away from GitHub, and says it once."""
    lines = [_failed_line(reason)]
    try:
        sent.save(replace(memory, failed_at=now, unreadable=False))
    except OSError as exc:
        lines.append(f"agentic: poll: board: memory not saved ({type(exc).__name__})")
    return lines


def _read(board: Board, memory: SentMemory) -> tuple[list[IssueRow], bool]:
    """The issues to build cards for. An empty memory reads everything once; afterwards the
    open issues, plus each remembered issue that left the open list without a Hecha card."""
    if not memory.issues:
        return board.all_issues()
    rows = board.open_issues()
    open_now = {row.number for row in rows}
    for number, entry in memory.issues.items():
        # `fin` is on a card only when the issue was closed: that card is Hecha.
        if number not in open_now and "fin" not in entry.present:
            rows.append(board.issue(number))
    return rows, False


def _facts(
    ports: Ports,
    rows: Sequence[IssueRow],
    live_issues: frozenset[int],
    blockers: Mapping[int, rules.BlockerReport],
    eval_set: frozenset[int] | None,
) -> list[domain.IssueFacts]:
    return [
        domain.IssueFacts(
            number=row.number,
            closed=row.state == "closed",
            closed_at=row.closed_at,
            labels=row.labels,
            live=row.number in live_issues,
            blocker=blockers.get(row.number),
            document=ports.documents.load(ports.inbox.task_id(row.number)),
            in_eval_set=None if eval_set is None else row.number in eval_set,
        )
        for row in rows
    ]


def mirror(
    ports: Ports,
    now: datetime,
    *,
    live_issues: frozenset[int],
    blockers: Mapping[int, rules.BlockerReport],
    eval_set: frozenset[int] | None,
) -> list[str]:
    """One round of the mirror: the log lines it has to say (none when idle). No board
    configured, or a recent whole failure still resting, makes no call at all."""
    board, sent = ports.board, ports.board_sent
    if board is None or sent is None:
        return []
    try:
        memory = sent.load()
    except OSError as exc:
        return [_failed_line(type(exc).__name__)]
    lines: list[str] = []
    if memory.unreadable:
        lines.append("agentic: poll: board: memory unreadable, sending everything again")
    if not domain.retry_due(memory.failed_at, now):
        return lines
    try:
        project = board.project_id()
    except _FAILURES as exc:
        return [*lines, *_remember_failure(sent, memory, now, _reason(exc))]
    if memory.project_id != project:
        memory = SentMemory(project_id=project)
    try:
        rows, limit_reached = _read(board, memory)
    except _FAILURES as exc:
        return [*lines, *_remember_failure(sent, memory, now, _reason(exc))]
    if limit_reached:
        lines.append("agentic: poll: board: issue list reached its limit, older ones not mirrored")
    cards = [domain.card(f) for f in _facts(ports, rows, live_issues, blockers, eval_set)]
    pending = domain.due(cards, {n: e.fingerprint for n, e in memory.issues.items()})
    if not pending:
        return lines
    writes = [
        w
        for card in pending
        for w in domain.writes(
            card, memory.issues[card.number].present if card.number in memory.issues else None
        )
    ]
    try:
        result = board.write(writes)
    except _FAILURES as exc:
        return [*lines, *_remember_failure(sent, memory, now, _reason(exc))]
    if not result.written:
        return [*lines, *_remember_failure(sent, memory, now, _NOTHING_WRITTEN)]
    issues = dict(memory.issues)
    for card in pending:
        if card.number in result.written:
            issues[card.number] = SentEntry(domain.fingerprint(card), domain.present(card))
    try:
        # A transport cause cut the round short: what was sent is kept, and the rest rests
        # 15 minutes instead of hammering GitHub every round (GH-438).
        failed_at = None if result.complete else now
        sent.save(SentMemory(project_id=project, failed_at=failed_at, issues=issues))
    except OSError as exc:
        lines.append(f"agentic: poll: board: memory not saved ({type(exc).__name__})")
    return lines
