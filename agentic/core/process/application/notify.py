"""The email notifier's round (GH-1055, part 3b-1): what to send now and where the activity read
resumes, over ports. Nothing calls `notify` yet: the poll does in part 3b-2.

One round: gate on `retry_due`, prune, save `started_at`, then up to `SENDS_PER_ROUND` mails in the
order waiting, releases, digest, each saved before the next. Waiting events come from the open
tray's documents; releases only from `Activity.closed_issues`, whose rows name the issue (a pull
request number never loads a document). The cursor moves only through `advance_read_at`, each
row's settlement coming from `row_settlement`. The first port failure stops the round: `failed_at`
is saved once, guarded, and the only line is the exception type name. No module state and no
environment: `NotifyCarry` and `NotifyKinds` arrive from the caller.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from ..domain import escalation_prompts, notify_message, rules, tracker_refs
from ..domain.notify import (
    SENDS_PER_ROUND,
    DigestEvent,
    EventKey,
    NotifiedRecord,
    NotifyKey,
    NotifyKinds,
    Outcome,
    PartFact,
    ReleaseEvent,
    SplitFacts,
    advance_read_at,
    digest_event,
    pending,
    prune,
    release_event,
    retry_due,
    row_settlement,
    start,
    waiting_event,
)
from ..domain.stack_signals import MERGED_OUTCOMES
from ..domain.task import TaskState
from .ports import ActivityPage, ActivityRow, Mail, Ports

__all__ = ["NotifyCarry", "notify", "split_facts_of"]

SHOWN = 50
"""Rows of one Activity read that are used; the read asks for one more, to know where it cut."""

Doc = dict[str, Any]


@dataclass
class NotifyCarry:
    """What the caller keeps between rounds so a line is said once per process."""

    dropped_said: bool = False
    repo_said: bool = False
    skipped_said: set[tuple[int, str]] = field(default_factory=set)


class _Stop(Exception):
    """A port call failed: only the type name of the cause travels."""

    def __init__(self, cause: str):
        super().__init__(cause)
        self.cause = cause


def _port[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except Exception as exc:
        raise _Stop(type(exc).__name__) from None


def _instant(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("instant is not text")
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("instant has no offset")
    return at.astimezone(UTC)


def _parent_of(doc: Doc) -> int | None:
    ids = rules.split_marker_ids(str(doc.get("description") or ""))
    if not ids:
        return None
    number = tracker_refs.issue_number(ids[0]) if len(ids) == 1 else None
    if number is None:
        raise ValueError("split marker is not one task id")
    return number


def _part_numbers(doc: Doc) -> list[int]:
    issues = doc.get("split_issues") or {}
    if not isinstance(issues, dict):
        raise ValueError("split_issues is not a mapping")
    numbers: list[int] = []
    for key in sorted(issues, key=int):
        url = issues[key]
        if not isinstance(url, str):
            raise ValueError("part url is not text")
        numbers.append(int(url.rstrip("/").rsplit("/", 1)[-1]))
    return numbers


def split_facts_of(number: int, docs: Mapping[int, Doc | None]) -> SplitFacts | None:
    """The split tree around task `number` from raw documents, never from the tracker: `None`
    when the task or a part has no document (facts not read, never read as no parent).
    `ValueError` for a shape or timestamp that is wrong. The parent is the one the stored
    request's marker names (`rules.split_marker_ids`), followed up while documents exist."""
    doc = docs.get(number)
    if doc is None:
        return None
    parents: list[int] = []
    node: Doc | None = doc
    while node is not None and len(parents) < 3:
        parent = _parent_of(node)
        if parent is None or parent in parents or parent == number:
            break
        parents.append(parent)
        node = docs.get(parent)
    parts: list[PartFact] = []
    queue = [(n, number) for n in _part_numbers(doc)]
    seen = {number}
    while queue:
        n, below = queue.pop(0)
        if n in seen:
            raise ValueError("a part repeats")
        seen.add(n)
        part = docs.get(n)
        if part is None:
            return None
        own = _parent_of(part)
        if own is None:
            raise ValueError("part has no split marker")
        url = part.get("pull_request")
        pr = tracker_refs.pull_request_number(str(url)) if url else None
        merged = part.get("delivery_outcome") in MERGED_OUTCOMES
        parts.append(PartFact(n, merged, pr, own))
        queue.extend((m, n) for m in _part_numbers(part))
    return SplitFacts(tuple(parents), _instant(doc.get("split_closed_at")), tuple(parts))


class _Docs(Mapping[int, Doc | None]):
    """The round's documents by issue number, each loaded once, on first use."""

    def __init__(self, load: Callable[[int], Doc | None]) -> None:
        self._load = load
        self._cache: dict[int, Doc | None] = {}

    def __getitem__(self, number: int) -> Doc | None:
        if number not in self._cache:
            self._cache[number] = _port(lambda: self._load(number))
        return self._cache[number]

    def __iter__(self) -> Iterator[int]:
        return iter(self._cache)

    def __len__(self) -> int:
        return len(self._cache)


@dataclass(frozen=True)
class _Row:
    """One Activity row for the cursor: its instant, the release key it owes (if any) and
    whether it was judged to owe nothing."""

    instant: datetime
    release: EventKey | None
    judged: bool


_NONE = ActivityPage((), None)


def _read(read: Callable[[datetime, int], ActivityPage], since: datetime) -> ActivityPage:
    """One wrapped Activity read of at most `SHOWN` rows; the page says where it cut. Every instant
    must be aware UTC and after `since`, otherwise the read is the round's failure."""
    page = read(since, SHOWN)
    instants = [row.instant for row in page.rows]
    if page.cut_at is not None:
        instants.append(page.cut_at)
    for instant in instants:
        if instant.utcoffset() != timedelta(0) or instant <= since:
            raise ValueError("activity row instant")
    return page


def _state(raw: Any) -> TaskState:
    """A loaded document as the rules read it: `TaskDocuments.load` returns an untyped `dict`."""
    state: TaskState = raw
    return state


def _mail(message: notify_message.Message) -> Mail:
    return Mail(message.subject, message.text)


class _Round:
    def __init__(self, ports: Ports, now: datetime, kinds: NotifyKinds, product: str, repo: str):
        assert ports.notifier is not None
        assert ports.notified is not None
        assert ports.activity is not None
        self.ports, self.notifier = ports, ports.notifier
        self.notified, self.activity = ports.notified, ports.activity
        self.now, self.kinds, self.product, self.repo = now, kinds, product, repo
        self.record: NotifiedRecord | None = None
        self.rows: list[_Row] | None = None
        self.cut_at: datetime | None = None
        self.digest_sent: DigestEvent | None = None

    def _set(self, keys: dict[NotifyKey, Outcome]) -> None:
        assert self.record is not None
        self.record = replace(self.record, keys={**self.record.keys, **keys})

    def _save(self, record: NotifiedRecord) -> None:
        _port(lambda: self.notified.save(record))
        self.record = record

    def cursor(self) -> datetime | None:
        assert self.record is not None
        read_at = self.record.activity_read_at
        if self.rows is None or (self.kinds.digest and self.digest_sent is None):
            return read_at
        candidates = [
            (
                row.instant,
                row_settlement(
                    self.record,
                    instant=row.instant,
                    digest=self.digest_sent,
                    release=row.release,
                    judged=row.judged,
                ),
            )
            for row in self.rows
            if self.cut_at is None or row.instant <= self.cut_at
        ]
        return advance_read_at(read_at, candidates, cut_at=self.cut_at)

    def fail(self, lines: list[str], cause: str) -> None:
        lines.append(f"notify: round failed ({cause})")
        if self.record is not None:
            failed = replace(self.record, failed_at=self.now, activity_read_at=self.cursor())
            with contextlib.suppress(Exception):
                self.notified.save(failed)

    def run(self, carry: NotifyCarry, lines: list[str], tz: tzinfo, hour: int) -> None:
        loaded = _port(self.notified.load)
        if not retry_due(loaded, self.now):
            return
        pruned, dropped = prune(loaded, self.now, tz)
        if dropped and not carry.dropped_said:
            carry.dropped_said = True
            lines.append(f"notify: {dropped} keys past the horizon dropped")
        self.record = pruned
        self._save(start(pruned, self.now))
        assert self.record is not None and self.record.started_at is not None
        since = self.record.activity_read_at or self.record.started_at
        waiting, tray_docs = self._waiting()
        closed, merged = _NONE, _NONE
        if self.kinds.release or self.kinds.digest:
            closed = _port(lambda: _read(self.activity.closed_issues, since))
        if self.kinds.digest:
            merged = _port(lambda: _read(self.activity.merged_prs, since))
        cuts = [page.cut_at for page in (closed, merged) if page.cut_at is not None]
        self.cut_at = min(cuts) if cuts else None
        releases = self._releases(list(closed.rows), carry, lines)
        self.rows = [*releases[1], *(_Row(r.instant, None, False) for r in merged.rows)]
        sends = [*waiting, *releases[0]]
        for key, mail in sends[:SENDS_PER_ROUND]:
            self._send(key, mail)
        digest = digest_event(self.now, tz, hour, self.record, self.product)
        if self.kinds.digest and digest is not None and len(sends) < SENDS_PER_ROUND:
            view = self._view(digest, closed, merged, tray_docs)
            self._send(digest.key, _mail(notify_message.digest_message(view, self.repo)))
            self.digest_sent = digest
        self._save(replace(self.record, activity_read_at=self.cursor()))

    def _send(self, key: NotifyKey, mail: Mail) -> None:
        assert self.record is not None
        _port(lambda: self.notifier.send(mail))
        self._save(replace(self.record, keys={**self.record.keys, key: "sent"}))

    def _waiting(self) -> tuple[list[tuple[NotifyKey, Mail]], dict[int, Doc]]:
        assert self.record is not None
        sends: list[tuple[NotifyKey, Mail]] = []
        tray: dict[int, Doc] = {}
        if not (self.kinds.waiting or self.kinds.digest):
            return sends, tray
        for number in _port(self.ports.tracker.queue):
            task_id = self.ports.inbox.task_id(number)
            doc = _port(lambda: self.ports.documents.load(task_id))
            if doc is None:
                continue
            tray[number] = doc
            if not self.kinds.waiting:
                continue
            event = waiting_event(_state(doc), self.product, number, issue_closed=False)
            action = None if event is None else pending(event, self.record, self.now)
            if event is None or action is None:
                continue
            mail = None
            if action == "send":
                mail = self._waiting_mail(number, doc)
            if mail is not None:
                sends.append((event.key, mail))
            else:
                self._set({event.key: "superseded" if action == "superseded" else "unbuildable"})
        return sends, tray

    def _waiting_mail(self, number: int, doc: Doc) -> Mail | None:
        state = _state(doc)
        cls = state.get("escalation_class")
        if cls is None or (cls, None) not in escalation_prompts.ESCALATION_PROMPTS:
            return None
        return _mail(
            notify_message.waiting_message(
                product=self.product,
                issue=number,
                title=str(doc.get("title") or ""),
                cls=cls,
                cause=state.get("escalation_cause"),
                reason=str(doc.get("escalated") or ""),
                open_rows=state.get("drawing_departures", []),
                usd=None,
                repo=self.repo,
            )
        )

    def _releases(
        self, closed: list[ActivityRow], carry: NotifyCarry, lines: list[str]
    ) -> tuple[list[tuple[NotifyKey, Mail]], list[_Row]]:
        sends: list[tuple[NotifyKey, Mail]] = []
        rows: list[_Row] = []
        docs = _Docs(lambda n: self.ports.documents.load(self.ports.inbox.task_id(n)))
        for row in closed:
            if not self.kinds.release:
                rows.append(_Row(row.instant, None, False))
                continue
            doc = docs.get(row.number)
            if doc is None:
                rows.append(_Row(row.instant, None, True))
                continue
            event, readable = self._event(row, doc, docs, carry, lines)
            if not readable:
                rows.append(_Row(row.instant, None, False))
                continue
            if event is None:
                rows.append(_Row(row.instant, None, True))
                continue
            assert self.record is not None
            action = pending(event, self.record, self.now)
            if action == "covered":
                self._set({event.key: "covered"})
            if action == "send":
                mail = _mail(
                    notify_message.release_message(
                        product=self.product,
                        event=event,
                        titles=_titles(docs, event),
                        departures=[],
                        repo=self.repo,
                    )
                )
                sends.append((event.key, mail))
            rows.append(_Row(row.instant, event.key, action != "send"))
        return sends, rows

    def _event(
        self, row: ActivityRow, doc: Doc, docs: _Docs, carry: NotifyCarry, lines: list[str]
    ) -> tuple[ReleaseEvent | None, bool]:
        """The release event of a closed issue and whether its split facts could be read; a
        `ValueError` of the document's shape or timestamps skips this row only."""
        try:
            facts = split_facts_of(row.number, docs)
            if facts is None:
                return None, False
            return release_event(
                _state(doc),
                self.product,
                row.number,
                facts,
                auto_merged_at=_instant(doc.get("auto_merged_at")),
            ), True
        except ValueError as exc:
            name = type(exc).__name__
            if (row.number, name) not in carry.skipped_said:
                carry.skipped_said.add((row.number, name))
                lines.append(f"notify: #{row.number} skipped ({name})")
            return None, True

    def _view(
        self,
        digest: DigestEvent,
        closed: ActivityPage,
        merged: ActivityPage,
        tray: Mapping[int, Doc],
    ) -> notify_message.DigestView:
        waiting: list[notify_message.DigestRow] = []
        paused: list[notify_message.DigestRow] = []
        running: list[notify_message.DigestRow] = []
        for number, doc in tray.items():
            title = str(doc.get("title") or "")
            if doc.get("status") == "waiting_on_human":
                if rules.pause_kind(rules.current_epoch(_state(doc))) == "human":
                    waiting.append(notify_message.DigestRow(number, title))
                else:
                    reason = str(doc.get("escalated") or "") or None
                    paused.append(notify_message.DigestRow(number, title, reason))
            elif doc.get("status") == "developing":
                running.append(notify_message.DigestRow(number, title))
        return notify_message.DigestView(
            self.product,
            digest.key.local_date,
            SHOWN,
            tuple(waiting),
            tuple(paused),
            tuple(running),
            self._window(merged, digest),
            self._window(closed, digest),
        )

    def _window(self, page: ActivityPage, digest: DigestEvent) -> notify_message.WindowedSection:
        cut = page.cut
        same = all(r.instant == self.cut_at for r in page.rows)
        single = cut and page.cut_at == self.cut_at and same
        kept = [
            r
            for r in page.rows
            if r.instant <= digest.instant
            and (single or self.cut_at is None or r.instant < self.cut_at)
        ]
        shown = tuple(notify_message.DigestRow(r.number, r.title) for r in kept)
        return notify_message.WindowedSection(shown, cut, single)


def _titles(docs: Mapping[int, Doc | None], event: ReleaseEvent) -> dict[int, str]:
    numbers = {event.key.issue, *(leaf.issue for leaf in event.leaves)}
    return {n: str((docs.get(n) or {}).get("title") or "") for n in numbers}


def notify(
    ports: Ports,
    now: datetime,
    *,
    carry: NotifyCarry,
    kinds: NotifyKinds,
    tz: tzinfo,
    hour: int,
    product: str,
    repo: str,
) -> list[str]:
    """The poll note lines of one notifier round; `[]` without a notifier, a record store or an
    activity read. A port failure ends the round with one line, the type name. A `repo` that is not
    a GitHub repository URL ends it before any read or write, said once per `carry`: no message
    could be built, so no key may be recorded as `unbuildable`."""
    if ports.notifier is None or ports.notified is None or ports.activity is None:
        return []
    lines: list[str] = []
    if not notify_message.is_repo_url(repo):
        if not carry.repo_said:
            carry.repo_said = True
            lines.append("notify: repo is not a GitHub repository URL")
        return lines
    run = _Round(ports, now, kinds, product, repo)
    try:
        run.run(carry, lines, tz, hour)
    except _Stop as stop:
        run.fail(lines, stop.cause)
    return lines
