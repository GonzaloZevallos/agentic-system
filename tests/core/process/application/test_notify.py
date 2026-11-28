"""GH-1059 part 3b-1: the notify round over fake Notifier, Notified and Activity ports."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from agentic.core.process.application.notify import NotifyCarry, notify, split_facts_of
from agentic.core.process.application.ports import ActivityRow, Ports
from agentic.core.process.domain import rules
from agentic.core.process.domain.notify import (
    NOTIFY_RETRY_AFTER,
    DigestKey,
    EventKey,
    NotifiedRecord,
    NotifyKinds,
)
from tests.fakes import (
    FakeActivity,
    FakeNotified,
    FakeNotifier,
    MemoryDocuments,
    MemoryTracker,
    memory_ports,
)

NOW = datetime(2026, 11, 20, 22, 0, tzinfo=UTC)
START = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
TZ = ZoneInfo("UTC")
ALL = NotifyKinds(waiting=True, release=True, digest=True)
RELEASE = NotifyKinds(waiting=False, release=True, digest=False)
REPO = "https://github.com/acme/shop"
SECRET = "hunter2-secret"


def _at(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def _row(number: int, minutes: int, title: str = "Title") -> ActivityRow:
    return ActivityRow(number, title, f"{REPO}/issues/{number}", _at(minutes))


def _doc(task_id: str, **fields: Any) -> dict[str, Any]:
    return {"id": task_id, "title": f"Task {task_id}", "description": "request", **fields}


def _ui(number: int, minutes: int = 60, **fields: Any) -> dict[str, Any]:
    flags = {"touches_ui": True}
    merged = _at(minutes).isoformat()
    return _doc(f"GH-{number}", flags=flags, auto_merged_at=merged, title="A\r\nB", **fields)


class Docs(MemoryDocuments):
    """Records every load and can make them raise."""

    def __init__(self) -> None:
        super().__init__()
        self.loaded: list[str] = []
        self.error: Exception | None = None

    def load(self, task_id: str) -> dict[str, object] | None:
        self.loaded.append(task_id)
        if self.error is not None:
            raise self.error
        return super().load(task_id)


class Setup:
    def __init__(self, *, docs: dict[str, Any] | None = None, **rows: Any) -> None:
        self.documents = Docs()
        for doc in (docs or {}).values():
            self.documents.save(doc)
        self.notifier = FakeNotifier()
        self.notified = FakeNotified(NotifiedRecord(START, None, None, {}))
        self.activity = FakeActivity(**rows)
        self.tracker = MemoryTracker()
        self.carry = NotifyCarry()

    @property
    def ports(self) -> Ports:
        return memory_ports(
            documents=self.documents,
            tracker=self.tracker,
            notifier=self.notifier,
            notified=self.notified,
            activity=self.activity,
        )

    def round(self, now: datetime = NOW, kinds: NotifyKinds = RELEASE) -> list[str]:
        return notify(
            self.ports,
            now,
            carry=self.carry,
            kinds=kinds,
            tz=TZ,
            hour=20,
            product="shop",
            repo=REPO,
        )


def test_without_a_notifier_the_round_is_empty() -> None:
    lines = notify(
        memory_ports(),
        NOW,
        carry=NotifyCarry(),
        kinds=ALL,
        tz=TZ,
        hour=20,
        product="shop",
        repo=REPO,
    )
    assert lines == []


def test_release_mail_once_with_title_and_link_then_not_again() -> None:
    s = Setup(docs={"GH-5": _ui(5)}, closed=[_row(5, 61)])
    assert s.round() == []
    assert len(s.notifier.sent) == 1
    mail = s.notifier.sent[0]
    assert mail.subject == "[shop] Released #5: AB"
    assert f"{REPO}/issues/5" in mail.body
    assert s.notified.record.keys == {EventKey("release", "shop", 5, _at(60)): "sent"}
    assert s.notified.record.activity_read_at == _at(61)
    s.round(NOW + timedelta(minutes=1))
    assert len(s.notifier.sent) == 1


def test_waiting_mail_once_and_superseded_key_without_send() -> None:
    log = [rules.escalation_entry("questions", _at(30).isoformat())]
    waiting = _doc("GH-7", escalation_class="questions", escalated="Which one?", pause_log=log)
    resumed = _doc("GH-8", pause_log=[*log, rules.resumed_entry(at=_at(40).isoformat())])
    s = Setup(docs={"GH-7": waiting, "GH-8": resumed})
    s.tracker._queue = {7, 8}
    s.round(kinds=NotifyKinds(True, False, False))
    assert [m.subject for m in s.notifier.sent] == ["[shop] #7 needs you: Task GH-7"]
    assert s.notified.record.keys == {
        EventKey("waiting", "shop", 7, _at(30)): "sent",
        EventKey("waiting", "shop", 8, _at(30)): "superseded",
    }
    assert s.activity.reads == []


def test_digest_sent_once_a_day_and_settles_rows_of_its_own_round() -> None:
    s = Setup(docs={"GH-5": _ui(5)}, closed=[_row(5, 61)], merged=[_row(200, 62)])
    s.activity.closed.append(_row(9, 12 * 60 + 5))
    s.round(kinds=ALL)
    subjects = [m.subject for m in s.notifier.sent]
    assert subjects == ["[shop] Released #5: AB", "[shop] Daily digest 2026-11-20"]
    record = s.notified.record
    assert DigestKey("shop", NOW.date()) in record.keys
    assert record.activity_read_at == _at(62)
    s.round(NOW + timedelta(hours=1), kinds=ALL)
    assert len(s.notifier.sent) == 2


def test_failing_send_is_tried_once_then_retried_after_the_wait() -> None:
    s = Setup(docs={"GH-5": _ui(5), "GH-6": _ui(6, 70)}, closed=[_row(5, 61), _row(6, 71)])
    s.notifier.fail = 1
    assert s.round() == ["notify: round failed (NotifyFailed)"]
    assert s.notifier.calls == 1
    assert s.notified.record.failed_at == NOW
    assert s.notified.record.activity_read_at is None
    s.round(NOW + timedelta(minutes=1))
    assert s.notifier.calls == 1
    s.round(NOW + NOTIFY_RETRY_AFTER)
    assert len(s.notifier.sent) == 2
    assert s.notified.record.activity_read_at == _at(71)


@pytest.mark.parametrize("where", ["activity", "documents"])
def test_port_errors_do_not_raise(where: str) -> None:
    s = Setup(closed=[_row(5, 61)])
    if where == "activity":
        s.activity.error = RuntimeError(f"boom {SECRET}")
    else:
        s.documents.error = RuntimeError(SECRET)
    lines = s.round()
    assert lines == ["notify: round failed (RuntimeError)"]
    assert s.notified.record.failed_at == NOW


def test_naive_instant_from_activity_is_the_rounds_failure() -> None:
    s = Setup(closed=[ActivityRow(5, "t", "u", datetime(2026, 11, 20, 9, 0))])
    assert s.round() == ["notify: round failed (ValueError)"]
    assert s.notified.record.failed_at == NOW


def test_save_failing_after_a_send_resends_exactly_once() -> None:
    s = Setup(docs={"GH-5": _ui(5)}, closed=[_row(5, 61)])
    s.notified.save_fails = {2}
    lines = s.round()
    assert lines == ["notify: round failed (OSError)"]
    assert "secret-path" not in "".join(lines)
    assert len(s.notifier.sent) == 1
    s.round(NOW + NOTIFY_RETRY_AFTER)
    s.round(NOW + 2 * NOTIFY_RETRY_AFTER)
    assert len(s.notifier.sent) == 2


def test_missing_document_is_judged_not_a_failure_and_no_title_leaks() -> None:
    s = Setup(closed=[_row(5, 61, SECRET)])
    assert s.round() == []
    assert s.notifier.sent == []
    assert s.notified.record.failed_at is None
    assert s.notified.record.activity_read_at == _at(61)
    assert SECRET not in repr(s.notified.record)


def test_pr_number_never_loads_a_document_and_issue_gives_the_key() -> None:
    s = Setup(docs={"GH-150": _ui(150)}, closed=[_row(150, 61)], merged=[_row(200, 62)])
    s.round(kinds=ALL)
    assert "GH-200" not in s.documents.loaded
    assert EventKey("release", "shop", 150, _at(60)) in s.notified.record.keys


def _part(number: int, parent: int, **fields: Any) -> dict[str, Any]:
    marker = rules.split_marker(f"GH-{parent}", 1, 1)
    return _doc(f"GH-{number}", description=f"request {marker}", **fields)


def test_wrong_parent_parts_skip_only_their_row_with_one_line() -> None:
    root = _doc(
        "GH-10", split_issues={"1": f"{REPO}/issues/11"}, split_closed_at=_at(50).isoformat()
    )
    docs = {"GH-10": root, "GH-11": _part(11, 99, delivery_outcome="merged"), "GH-5": _ui(5)}
    s = Setup(docs=docs, closed=[_row(5, 61), _row(10, 62)])
    assert s.round() == ["notify: #10 skipped (ValueError)"]
    assert s.round(NOW + timedelta(minutes=1)) == []
    assert [m.subject for m in s.notifier.sent] == ["[shop] Released #5: AB"]
    assert s.notified.record.activity_read_at == _at(62)


def test_release_only_cursor_passes_sixty_closed_issues_over_two_rounds() -> None:
    rows = [_row(1000 + n, n + 1) for n in range(60)]
    s = Setup(closed=rows)
    s.round()
    first = s.notified.record.activity_read_at
    assert first is not None and first < rows[-1].instant
    s.round(NOW + timedelta(minutes=1))
    assert s.notified.record.activity_read_at == rows[-1].instant
    assert not any(isinstance(k, DigestKey) for k in s.notified.record.keys)


def test_digest_on_cursor_waits_for_the_digest_and_cut_digest_leaves_the_rest() -> None:
    rows = [_row(1000 + n, n + 1) for n in range(80)]
    s = Setup(closed=rows)
    s.round(datetime(2026, 11, 20, 19, tzinfo=UTC), kinds=ALL)
    assert s.notified.record.activity_read_at is None
    s.round(NOW, kinds=ALL)
    assert s.notified.record.activity_read_at == rows[49].instant
    s.round(NOW + timedelta(hours=1), kinds=ALL)
    assert s.notified.record.activity_read_at == rows[49].instant
    s.round(NOW + timedelta(days=1), kinds=ALL)
    assert s.notified.record.activity_read_at == rows[79].instant


def test_row_closed_after_the_sent_digest_stays_unsettled() -> None:
    s = Setup(closed=[_row(1, 61), _row(2, 15 * 60)])
    s.round(kinds=ALL)
    assert s.notified.record.activity_read_at == _at(61)


def test_merged_cut_at_fifty_still_judges_older_closed_rows() -> None:
    s = Setup(closed=[_row(5, 61)], merged=[_row(2000 + n, 100 + n) for n in range(51)])
    s.round(kinds=NotifyKinds(False, True, True))
    assert s.activity.reads == [("closed", START), ("merged", START)]
    assert "Closed issues (1)" in s.notifier.sent[0].body
    assert "Merged pull requests (50+)" in s.notifier.sent[0].body
    assert s.notified.record.activity_read_at == _at(149)


def test_rows_before_started_at_are_never_read() -> None:
    s = Setup(closed=[_row(5, -30)])
    s.round()
    assert s.activity.reads == [("closed", START)]
    assert s.notifier.sent == []
    assert s.notified.record.activity_read_at is None


def test_first_round_saves_started_at_before_any_send() -> None:
    s = Setup(docs={"GH-5": _ui(5, 1)}, closed=[_row(5, 2)])
    s.notified.record = NotifiedRecord(None, None, None, {})
    s.round()
    assert s.notified.saves[0].started_at == NOW
    assert s.notifier.sent == []


def test_at_most_five_sends_a_round() -> None:
    docs = {f"GH-{n}": _ui(n, n) for n in range(10, 18)}
    s = Setup(docs=docs, closed=[_row(n, n + 1) for n in range(10, 18)])
    s.round()
    assert len(s.notifier.sent) == 5


TWO_MARKERS = "<!-- agentic-split: GH-2 part 1 --> <!-- agentic-split: GH-3 part 1 -->"


@pytest.mark.parametrize(
    "doc",
    [
        _doc("GH-1", split_issues="x"),
        _doc("GH-1", split_issues={"1": 5}),
        _doc("GH-1", split_issues={"a": f"{REPO}/issues/2"}),
        _doc("GH-1", split_closed_at="2026-11-20T10:00:00"),
        _doc("GH-1", split_closed_at=12),
        _doc("GH-1", description=TWO_MARKERS),
        _doc("GH-1", description="<!-- agentic-split: nope part 1 -->"),
        _doc("GH-1", split_issues={"1": f"{REPO}/issues/1"}),
    ],
)
def test_split_facts_of_rejects_malformed_documents(doc: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        split_facts_of(1, {1: doc, 2: _part(2, 1)})


def test_split_facts_of_reads_parent_and_parts_without_the_tracker() -> None:
    root = _doc("GH-1", split_issues={"1": f"{REPO}/issues/2"}, split_closed_at=_at(5).isoformat())
    part = _part(2, 1, delivery_outcome="auto_merged", pull_request=f"{REPO}/pull/9")
    facts = split_facts_of(1, {1: root, 2: part})
    assert facts is not None
    assert facts.split_closed_at == _at(5)
    assert [(p.issue, p.merged, p.pr, p.parent_of) for p in facts.parts] == [(2, True, 9, 1)]
    child = split_facts_of(2, {2: part, 1: root})
    assert child is not None and child.parents == (1,)
    assert split_facts_of(1, {1: None}) is None
    assert split_facts_of(1, {1: root, 2: None}) is None


ALLOWED_IMPORTERS: frozenset[str] = frozenset()
# Part 3b-2 adds agentic/core/process/application/poll.py here, with the poll's call.


def _imports_notify_use_case(path: Path, tree: ast.AST) -> bool:
    in_application = path.parent.name == "application"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.endswith("application.notify") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith("application.notify") or (in_application and module == "notify"):
                return True
            package = module.endswith("application") or (in_application and not module)
            if package and node.level and any(a.name == "notify" for a in node.names):
                return True
            if module.endswith("application") and any(a.name == "notify" for a in node.names):
                return True
    return False


def test_nothing_imports_the_notify_use_case_yet() -> None:
    root = Path(__file__).resolve().parents[4] / "agentic"
    own = root / "core" / "process" / "application" / "notify.py"
    importers = {
        str(path.relative_to(root.parent))
        for path in root.rglob("*.py")
        if path != own and _imports_notify_use_case(path, ast.parse(path.read_text()))
    }
    assert importers == ALLOWED_IMPORTERS
    sample = ast.parse("from agentic.core.process.application.notify import notify")
    assert _imports_notify_use_case(Path("x/other.py"), sample)
