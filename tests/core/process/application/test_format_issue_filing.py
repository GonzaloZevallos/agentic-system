"""`FormatFiling.file` over a fake `SystemRepo` and `tracker_failure_class` (GH-1030).

The locks and the filed pairs are instance state: the concurrency test covers the threads of
one process only, never two processes."""

from __future__ import annotations

import threading
import time

import pytest

from agentic.core.process.application.format_issue import FormatFiling
from agentic.core.process.application.ports import (
    ListMayBeCut,
    SystemRepo,
    TrackerCallFailed,
    TrackerRateLimited,
    TrackerRefused,
    TrackerTimedOut,
    TrackerTransient,
    TrackerUnreachable,
    tracker_failure_class,
)
from agentic.core.process.domain import format_issue as fi
from agentic.core.process.domain.landing import IssueRef
from agentic.core.process.domain.task import Failure, FormatIssueRecord
from tests.fakes import MemoryInbox, MemoryTracker

VALUE = "SEEDED-REFUSED-VALUE"
ERROR = "SEEDED-ERROR-TEXT"
DETAIL = "SEEDED-DETAIL-TEXT"
SECRET = "SEEDED-EXCEPTION-MESSAGE"
NOW = "2026-11-20T10:00:00Z"


class FakeInbox(MemoryInbox):
    def __init__(self, refs: list[IssueRef] | None = None, error: Exception | None = None) -> None:
        super().__init__()
        self.refs = refs or []
        self.error = error
        self.reads = 0

    def open_issue_refs(self) -> list[IssueRef]:
        self.reads += 1
        if self.error is not None:
            raise self.error
        return list(self.refs)


class FakeTracker(MemoryTracker):
    def __init__(self, inbox: FakeInbox, error: Exception | None = None) -> None:
        super().__init__()
        self.inbox = inbox
        self.error = error
        self.opened: list[tuple[str, str, tuple[str, ...]]] = []
        self.commented: list[tuple[str, str]] = []
        self.delay = 0.0

    def open_issue(
        self, title: str, body: str, *, label: str | None = None, labels: tuple[str, ...] = ()
    ) -> str:
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        assert label is None
        self.opened.append((title, body, tuple(labels)))
        url = f"https://github.com/o/agentic-system/issues/{40 + len(self.opened)}"
        self.inbox.refs.append(IssueRef(url=url, title=title, body=body))
        return url

    def comment_on_issue(self, reference: str, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.commented.append((reference, text))


class Built:
    def __init__(
        self,
        refs: list[IssueRef] | None = None,
        read_error: Exception | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self.inbox = FakeInbox(refs, read_error)
        self.tracker = FakeTracker(self.inbox, write_error)
        self.lines: list[str] = []
        self.filing = FormatFiling(
            SystemRepo(self.tracker, self.inbox),
            self.lines.append,
            lambda: NOW,
        )

    def file(
        self,
        failures: list[Failure] | None = None,
        *,
        task_id: str = "GH-5",
        product: str = "gym-saas",
        existing: list[FormatIssueRecord] | None = None,
    ) -> tuple[FormatIssueRecord | None, list]:
        return self.filing.file(
            task_id=task_id,
            issue_number=5,
            product=product,
            role="Tech Lead",
            failures=failures if failures is not None else [failure()],
            existing=existing or [],
        )


def failure(rule: str = "parts_when_fits", **extra: object) -> Failure:
    row: dict[str, object] = {
        "role": "Tech Lead",
        "attempt": 1,
        "error": ERROR,
        "cost_usd": 0.0,
        "session_id": "s",
        "reason": "schema",
        "rule": rule,
        "field": "parts",
        "detail": DETAIL,
        "value": VALUE,
        **extra,
    }
    return row


def marked(rule: str, number: int) -> IssueRef:
    body = f"text\n\n{fi.marker(rule)}"
    return IssueRef(
        url=f"https://github.com/o/agentic-system/issues/{number}", title="t", body=body
    )


def test_a_new_issue_is_opened_labelled_needs_triage_and_ends_with_the_marker():
    b = Built()
    record, rows = b.file([failure(value_len=7)])
    assert rows == []
    assert len(b.tracker.opened) == 1
    title, body, labels = b.tracker.opened[0]
    assert labels == ("needs-triage",) and "agentic" not in labels
    assert body.rstrip().splitlines()[-1] == fi.marker("parts_when_fits")
    assert "value length: 7" in body
    assert record == {
        "rule": "parts_when_fits",
        "url": "https://github.com/o/agentic-system/issues/41",
        "at": NOW,
    }
    assert b.lines == ["agentic: format issue: parts_when_fits: opened"]


def test_an_existing_marked_issue_is_commented_not_reopened_using_the_lowest_number():
    b = Built(
        [
            marked("parts_when_fits", 30),
            marked("parts_when_fits", 12),
            marked("estimate_does_not_fit", 3),
        ]
    )
    record, rows = b.file()
    assert rows == [] and b.tracker.opened == []
    assert [url for url, _ in b.tracker.commented] == [
        "https://github.com/o/agentic-system/issues/12"
    ]
    assert b.tracker.commented[0][1].rstrip().endswith(fi.marker("parts_when_fits"))
    assert record is not None and record["url"].endswith("/12")
    assert b.lines == ["agentic: format issue: parts_when_fits: commented"]


def test_a_read_that_may_be_cut_writes_nothing_and_returns_no_row():
    b = Built(read_error=ListMayBeCut(SECRET))
    assert b.file() == (None, [])
    assert b.tracker.opened == [] and b.tracker.commented == []
    assert b.lines == ["agentic: format issue: parts_when_fits: read failed"]


def test_a_read_that_timed_out_is_called_once_and_no_write_follows():
    b = Built(read_error=TrackerTimedOut(SECRET))
    assert b.file() == (None, [])
    assert b.inbox.reads == 1
    assert b.tracker.opened == [] and b.tracker.commented == []


@pytest.mark.parametrize(
    ("error", "cls"),
    [
        (TrackerTimedOut(SECRET), "timed_out"),
        (TrackerUnreachable(SECRET), "unreachable"),
        (TrackerTransient(SECRET), "transient"),
        (TrackerRateLimited(SECRET), "rate_limited"),
        (TrackerRefused(SECRET), "refused"),
        (ValueError(SECRET), "refused"),
        (OSError(SECRET), "unreachable"),
        (KeyError(SECRET), "refused"),
    ],
)
def test_a_failed_open_is_one_issue_row_and_never_raises(error, cls):
    b = Built(write_error=error)
    record, rows = b.file()
    assert record is None
    assert rows == [{"target": "issue", "cls": cls, "at": NOW}]
    assert b.lines == ["agentic: format issue: parts_when_fits: write failed"]


def test_a_failed_comment_is_a_comment_row():
    b = Built([marked("parts_when_fits", 9)], write_error=TrackerRefused(SECRET))
    record, rows = b.file()
    assert record is None
    assert rows == [{"target": "comment", "cls": "refused", "at": NOW}]


def test_a_failed_write_does_not_mark_the_pair_filed_so_the_next_call_writes():
    b = Built(write_error=OSError(SECRET))
    assert b.file()[0] is None
    b.tracker.error = None
    record, rows = b.file()
    assert rows == [] and record is not None
    assert len(b.tracker.opened) == 1


def test_a_rule_already_filed_for_the_task_is_skipped_without_reading():
    b = Built()
    existing: list[FormatIssueRecord] = [
        {"rule": "parts_when_fits", "url": "https://x/issues/1", "at": NOW}
    ]
    assert b.file(existing=existing) == (None, [])
    assert b.inbox.reads == 0
    assert b.lines == ["agentic: format issue: parts_when_fits: skipped"]


def test_a_second_call_for_the_same_pair_is_skipped_without_reading_again():
    b = Built()
    assert b.file()[0] is not None
    assert b.file() == (None, [])
    assert b.inbox.reads == 1 and len(b.tracker.opened) == 1
    assert b.lines[-1] == "agentic: format issue: parts_when_fits: skipped"


def test_what_reaches_the_tracker_is_the_evidence_and_never_the_seeded_text():
    b = Built()
    row = failure(value_len=3, earlier="x")
    b.file([failure("estimate_does_not_fit"), row])
    title, body, _ = b.tracker.opened[0]
    assert (title, body) == fi.evidence(
        "parts_when_fits", "parts", "Tech Lead", "gym-saas", 5, 3, 2, ["estimate_does_not_fit"]
    )
    for seeded in (VALUE, ERROR, DETAIL):
        assert seeded not in title and seeded not in body

    c = Built([marked("parts_when_fits", 2)])
    c.file([failure("estimate_does_not_fit"), row])
    assert c.tracker.commented[0][1] == body
    assert all(s not in c.tracker.commented[0][1] for s in (VALUE, ERROR, DETAIL))


@pytest.mark.parametrize("where", ["read", "open", "comment"])
def test_the_message_of_an_exception_is_in_no_log_line(where):
    if where == "read":
        b = Built(read_error=TrackerCallFailed(SECRET))
    elif where == "open":
        b = Built(write_error=TrackerCallFailed(SECRET))
    else:
        b = Built([marked("parts_when_fits", 2)], write_error=TrackerCallFailed(SECRET))
    b.file()
    assert b.lines
    for line in b.lines:
        assert SECRET not in line and "http" not in line and "Tracker" not in line
        assert line.startswith("agentic: format issue: parts_when_fits: ")


def test_an_unknown_rule_never_raises_and_logs_unknown():
    b = Built()
    assert b.file([failure("not_a_rule")]) == (None, [])
    assert b.lines == ["agentic: format issue: unknown: write failed"]
    assert b.tracker.opened == []
    assert b.file([]) == (None, [])
    assert b.lines[-1] == "agentic: format issue: unknown: write failed"


def test_a_product_outside_the_slug_form_becomes_unknown_in_the_body():
    b = Built()
    b.file(product="Gym SaaS\n<script>")
    assert "- product: unknown" in b.tracker.opened[0][1]
    assert "Gym" not in b.tracker.opened[0][1]


def test_a_last_failure_without_value_len_says_unknown_length():
    b = Built()
    b.file()
    assert "- value length: unknown" in b.tracker.opened[0][1]


def test_two_concurrent_calls_for_one_rule_open_one_issue_in_this_process():
    b = Built()
    b.tracker.delay = 0.05
    results: list[tuple[FormatIssueRecord | None, list]] = []
    start = threading.Barrier(2)

    def call(task: str) -> None:
        start.wait()
        results.append(b.file(task_id=task))

    threads = [threading.Thread(target=call, args=(t,)) for t in ("GH-5", "GH-6")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(b.tracker.opened) == 1
    assert len(b.tracker.commented) == 1
    assert all(record is not None for record, _ in results)


def test_tracker_failure_class_maps_each_tracker_class_by_type():
    assert tracker_failure_class(TrackerTimedOut("x")) == "timed_out"
    assert tracker_failure_class(TrackerUnreachable("x")) == "unreachable"
    assert tracker_failure_class(TrackerTransient("x")) == "transient"
    assert tracker_failure_class(TrackerRateLimited("x")) == "rate_limited"
    assert tracker_failure_class(TrackerRefused("x")) == "refused"
    assert tracker_failure_class(ValueError("timed out")) is None
    assert tracker_failure_class(ListMayBeCut("x")) is None
