"""The board mirror's use case (GH-375, ADR-0073): once per poll round, put each issue's card
on the GitHub Project, only when it changed since the last send.

The Project is an output. This module reads issues off the tracker, builds `IssueFacts` from
the issue's own state and `today`, asks `domain.board` for the card and for what is due, and
writes only that. The memory of what was sent (`BoardSent`) is what makes a quiet round cost
one read. A failing Project call is one log line and a 15 minute rest, never the round's end.
The `try` wraps port calls only; the domain is called outside it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

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


@dataclass(frozen=True)
class _Read:
    rows: list[IssueRow]
    limit_reached: bool
    failed: dict[int, str]
    """The remembered issues whose read failed this round, and the exception type's name."""


def _read(board: Board, memory: SentMemory, today: date) -> _Read:
    """The issues to build cards for. An empty memory reads everything once; afterwards the
    open issues, plus each remembered issue that left the open list without a Hecho card and
    each remembered Hecho card due to expire (`domain.expiry_due`, capped). An issue that
    cannot be read is skipped and named in `failed`; a failure of the lists fails the round."""
    if not memory.issues:
        rows, limit_reached = board.all_issues()
        return _Read(rows, limit_reached, {})
    rows = board.open_issues()
    open_now = {row.number for row in rows}
    # `fin` is on a card only when the issue was closed: that card is Hecho.
    left = [
        number
        for number, entry in memory.issues.items()
        if number not in open_now and "fin" not in entry.present
    ]
    expiring = [n for n in domain.expiry_due(memory.issues, today) if n not in open_now]
    failed: dict[int, str] = {}
    for number in dict.fromkeys([*left, *expiring]):
        try:
            rows.append(board.issue(number))
        except _FAILURES as exc:
            failed[number] = type(exc).__name__
    return _Read(rows, False, failed)


def _facts(
    ports: Ports,
    rows: Sequence[IssueRow],
    live_issues: frozenset[int],
    blockers: Mapping[int, rules.BlockerReport],
    today: date,
) -> list[domain.IssueFacts]:
    from .resume import state_from  # at call time: `resume` reaches `poll`, which imports this

    facts = []
    for row in rows:
        document = ports.documents.load(ports.inbox.task_id(row.number))
        facts.append(
            domain.IssueFacts(
                number=row.number,
                closed=row.state == "closed",
                closed_at=row.closed_at,
                labels=row.labels,
                live=row.number in live_issues,
                blocker=blockers.get(row.number),
                document=document,
                today=today,
                resumes_itself=document is not None and rules.poller_retakes(state_from(document)),
            )
        )
    return facts


def _entry(card: domain.Card) -> SentEntry:
    until = domain.hecho_until(card.finished) if card.etapa == "Hecho" and card.finished else None
    return SentEntry(domain.fingerprint(card), domain.present(card), until)


def _settle(issues: Mapping[int, SentEntry], read: _Read, lines: list[str]) -> dict[int, SentEntry]:
    """The memory's entries after this round's reads: a good read (the issue is in the rows)
    zeroes `unread` first, then a failed one adds to it and an entry that reached the limit is
    dropped. Each is said once: the first failure of a run, and the drop."""
    settled = dict(issues)
    for row in read.rows:
        entry = settled.get(row.number)
        if entry is not None and entry.unread:
            settled[row.number] = replace(entry, unread=0)
    for number, name in read.failed.items():
        entry = settled[number]
        if entry.unread == 0:
            lines.append(f"agentic: poll: board: #{number} not read ({name})")
        settled[number] = replace(entry, unread=entry.unread + 1)
    for number in [n for n, entry in settled.items() if domain.dropped(entry.unread)]:
        del settled[number]
        lines.append(f"agentic: poll: board: #{number} dropped from memory after 3 failed reads")
    return settled


def _commit(sent: BoardSent, before: SentMemory, after: SentMemory, lines: list[str]) -> list[str]:
    """The one save point after the read: saves when the round's memory differs from the one
    loaded, and says so when it cannot."""
    if after != before:
        try:
            sent.save(after)
        except OSError as exc:
            lines.append(f"agentic: poll: board: memory not saved ({type(exc).__name__})")
    return lines


def mirror(
    ports: Ports,
    now: datetime,
    *,
    live_issues: frozenset[int],
    blockers: Mapping[int, rules.BlockerReport],
) -> list[str]:
    """One round of the mirror: the log lines it has to say (none when idle). No board
    configured, or a recent whole failure still resting, makes no call at all."""
    board, sent = ports.board, ports.board_sent
    if board is None or sent is None:
        return []
    try:
        loaded = sent.load()
    except OSError as exc:
        return [_failed_line(type(exc).__name__)]
    memory = loaded
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
    today = now.astimezone(UTC).date()
    try:
        read = _read(board, memory, today)
    except _FAILURES as exc:
        return [*lines, *_remember_failure(sent, memory, now, _reason(exc))]
    if read.limit_reached:
        lines.append("agentic: poll: board: issue list reached its limit, older ones not mirrored")
    issues = _settle(memory.issues, read, lines)
    cards = [domain.card(f) for f in _facts(ports, read.rows, live_issues, blockers, today)]
    pending = domain.due(cards, {n: e.fingerprint for n, e in issues.items()})
    writes: dict[int, list[domain.FieldWrite]] = {}
    for card in pending:
        previous = issues[card.number].present if card.number in issues else None
        if card_writes := domain.writes(card, previous):
            writes[card.number] = card_writes
        else:
            issues[card.number] = _entry(card)
    result = None
    if writes:
        failure: str | None
        try:
            result = board.write([w for card_writes in writes.values() for w in card_writes])
        except _FAILURES as exc:
            failure = _reason(exc)
        else:
            failure = None if result.written else _NOTHING_WRITTEN
        if failure is not None:
            kept = replace(memory, issues=issues)
            return [*lines, *_remember_failure(sent, kept, now, failure)]
    if result is not None:
        issues.update({c.number: _entry(c) for c in pending if c.number in result.written})
    # A transport cause cut the round short: what was sent is kept, and the rest rests
    # 15 minutes instead of hammering GitHub every round (GH-438).
    failed_at = now if result is not None and not result.complete else None
    after = SentMemory(project_id=project, failed_at=failed_at, issues=issues)
    return _commit(sent, loaded, after, lines)
