"""Application tests of `Stage0Escalation` (GH-632, part 2e): `run` over in-memory doubles of
the tracker, the inbox and the store. The doubles record what reaches the tracker, so the
tests can say what it did and did not receive."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application.stage0_escalation import (
    ESCALATED_MARKER,
    ISSUE_MARKER,
    ISSUE_TITLE,
    UNAVAILABLE_MARKER,
    Stage0Escalation,
    has_line,
    render_marker,
)
from agentic.core.process.domain.landing import IssueRef

MODULE = Path(__file__).parents[4] / "agentic/core/process/application/stage0_escalation.py"

A = "a" * 40
B = "b" * 40
URL = "https://github.com/o/agentic-system/issues/42"
HOSTILE = "rm -rf; token=abc"


class Tracker:
    """Records every call; `fail` names the methods that raise."""

    def __init__(self, *, comments: list[str] | None = None, fail: tuple[str, ...] = ()) -> None:
        self.comments = list(comments or [])
        self.fail = list(fail)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _call(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.fail:
            if name == "comment_on_issue":
                self.fail.remove(name)  # a comment fails once, as a flaky network does
            raise ValueError(f"{name} failed: {HOSTILE}")

    def open_issue(self, title: str, body: str, *, label: str | None = None) -> str:
        self._call("open_issue", title, body, label)
        return URL

    def issue_comments(self, task_id: str) -> list[str]:
        self._call("issue_comments", task_id)
        return list(self.comments)

    def comment_on_issue(self, reference: str, text: str) -> None:
        self._call("comment_on_issue", reference, text)
        self.comments.append(text)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def posted(self) -> list[str]:
        return [args[1] for name, args in self.calls if name == "comment_on_issue"]


class Inbox:
    def __init__(self, refs: list[IssueRef] | None = None, *, fail: bool = False) -> None:
        self.refs = refs or []
        self.fail = fail
        self.calls = 0

    def open_issue_refs(self) -> list[IssueRef]:
        self.calls += 1
        if self.fail:
            raise ValueError(f"limit reached {HOSTILE}")
        return list(self.refs)


class Store:
    def __init__(
        self,
        *,
        rejected: set[str] | None = None,
        record: tuple[str, int, str] | None = None,
        issue: str | None = None,
        fail: tuple[str, ...] = (),
    ) -> None:
        self.rejected_set = rejected or set()
        self.record = record
        self.issue = issue
        self.fail = fail
        self.written: list[str] = []

    def rejected(self) -> frozenset[str]:
        if "rejected" in self.fail:
            raise OSError(HOSTILE)
        return frozenset(self.rejected_set)

    def unavailable_record(self) -> tuple[str, int, str] | None:
        return self.record

    def escalation_issue(self) -> str | None:
        return self.issue

    def write_escalation_issue(self, url: str) -> None:
        if "write" in self.fail:
            raise OSError(HOSTILE)
        self.written.append(url)
        self.issue = url


def build(tracker: Any, inbox: Any, store: Any) -> tuple[Stage0Escalation, list[str]]:
    out: list[str] = []
    return Stage0Escalation(tracker, inbox, store, out.append), out


def ref(title: str, body: str, url: str = URL) -> IssueRef:
    return IssueRef(url=url, title=title, body=body)


def test_nothing_pending_makes_no_call() -> None:
    tracker, inbox = Tracker(fail=("open_issue", "issue_comments")), Inbox(fail=True)
    escalation, out = build(tracker, inbox, Store(record=(A, 2, "uv")))

    escalation.run()

    assert tracker.calls == [] and inbox.calls == 0 and out == []


def test_the_rejected_text_names_no_cause_and_says_how_to_undo_it() -> None:
    tracker, inbox = Tracker(), Inbox()
    escalation, _ = build(tracker, inbox, Store(rejected={A}))

    escalation.run()

    (text,) = tracker.posted()
    assert f"rejected commit {A}." in text
    assert "its smoke failed beside" not in text
    assert "remove the sha from the rejected list to allow it again" in text


def test_a_rejected_sha_escalates_exactly_once_over_three_passes() -> None:
    tracker, inbox = Tracker(fail=("comment_on_issue",)), Inbox()
    store = Store(rejected={A})
    escalation, out = build(tracker, inbox, store)

    escalation.run()
    assert out == ["escalation: opened", "escalation: failed"]
    assert len(tracker.posted()) == 1

    out.clear()
    escalation.run()
    assert out == [f"escalation: escalated {A}"]
    assert len(tracker.posted()) == 2

    out.clear()
    escalation.run()
    assert out == []
    assert len(tracker.posted()) == 2
    assert tracker.names().count("open_issue") == 1
    assert store.written == [URL]
    assert has_line(tracker.posted()[1], render_marker(ESCALATED_MARKER, A))


def test_the_issue_is_opened_with_its_marker_and_no_label() -> None:
    tracker, inbox = Tracker(), Inbox()
    escalation, _ = build(tracker, inbox, Store(rejected={A}))

    escalation.run()

    (title, body, label) = next(args for name, args in tracker.calls if name == "open_issue")
    assert title == ISSUE_TITLE and label is None
    assert has_line(body, ISSUE_MARKER)
    assert ("issue_comments", ("GH-42",)) in tracker.calls


def test_an_issue_found_by_title_and_marker_line_is_reused_and_stored() -> None:
    found = "https://github.com/o/agentic-system/issues/7"
    tracker, inbox = Tracker(), Inbox([ref(ISSUE_TITLE, f"text\n{ISSUE_MARKER}\n", found)])
    store = Store(rejected={A})
    escalation, out = build(tracker, inbox, store)

    escalation.run()

    assert "open_issue" not in tracker.names()
    assert store.written == [found]
    assert ("issue_comments", ("GH-7",)) in tracker.calls
    assert tracker.calls[-1][1][0] == found
    assert out == [f"escalation: escalated {A}"]


@pytest.mark.parametrize(
    ("title", "body"),
    [
        (ISSUE_TITLE, "see agentic-stage0-escalations"),
        ("another title", ISSUE_MARKER),
        (ISSUE_TITLE, f"quoted `{ISSUE_MARKER}` in a line"),
    ],
)
def test_a_decoy_issue_is_not_adopted(title: str, body: str) -> None:
    decoy = "https://x/issues/9"
    tracker, inbox = Tracker(), Inbox([ref(title, body, decoy)])
    store = Store(rejected={A})
    escalation, _ = build(tracker, inbox, store)

    escalation.run()

    assert "open_issue" in tracker.names()
    assert store.written == [URL]


def test_a_reached_bound_opens_no_issue() -> None:
    tracker, inbox = Tracker(), Inbox(fail=True)
    escalation, out = build(tracker, inbox, Store(rejected={A}))

    escalation.run()

    assert tracker.calls == []
    assert out == ["escalation: failed"]


def test_unavailable_at_three_comments_once_and_at_two_not_at_all() -> None:
    tracker, inbox = Tracker(), Inbox()
    escalation, out = build(tracker, inbox, Store(record=(A, 3, "uv")))

    escalation.run()
    escalation.run()

    assert out == ["escalation: opened", f"escalation: unavailable {A}"]
    assert len(tracker.comments) == 1
    assert has_line(tracker.comments[0], render_marker(UNAVAILABLE_MARKER, A))
    assert "uv" in tracker.comments[0]

    quiet, quiet_inbox = Tracker(), Inbox()
    build(quiet, quiet_inbox, Store(record=(A, 2, "uv")))[0].run()
    assert quiet.calls == [] and quiet_inbox.calls == 0


def test_an_already_marked_sha_is_not_commented_again() -> None:
    marked = f"rejected\n\n  {render_marker(ESCALATED_MARKER, A)}  "
    tracker = Tracker(comments=[marked])
    escalation, out = build(tracker, Inbox(), Store(rejected={A}, issue=URL))

    escalation.run()

    assert tracker.posted() == [] and out == []


def test_a_marker_quoted_inside_a_line_does_not_count() -> None:
    quoted = f"I saw `{render_marker(ESCALATED_MARKER, A)}` earlier"
    tracker = Tracker(comments=[quoted])
    escalation, _ = build(tracker, Inbox(), Store(rejected={A}, issue=URL))

    escalation.run()

    assert len(tracker.posted()) == 1


def test_only_the_unmarked_sha_is_commented() -> None:
    tracker = Tracker(comments=[render_marker(ESCALATED_MARKER, A)])
    escalation, out = build(tracker, Inbox(), Store(rejected={A, B}, issue=URL))

    escalation.run()

    assert out == [f"escalation: escalated {B}"]


def test_hostile_values_never_reach_the_tracker_or_out() -> None:
    short = "abc123"
    tracker = Tracker()
    store = Store(rejected={short, "A" * 40, B}, record=(A, 3, HOSTILE), issue=URL)
    escalation, out = build(tracker, Inbox(), store)

    escalation.run()

    seen = [str(args) for _, args in tracker.calls] + out
    assert not any(HOSTILE in s or short in s or "A" * 40 in s for s in seen)
    assert out.count("escalation: failed") == 1
    assert any("is unknown" in text for text in tracker.posted())
    assert len(tracker.posted()) == 2  # B escalated, A unavailable with an unknown cause


def test_a_sha_that_is_not_40_hex_alone_is_failed_without_calls() -> None:
    tracker, inbox = Tracker(), Inbox()
    escalation, out = build(tracker, inbox, Store(record=("abc1234", 5, "uv")))

    escalation.run()

    assert out == ["escalation: failed"]
    assert tracker.calls == [] and inbox.calls == 0


@pytest.mark.parametrize("stored", ["https://x/issues/abc", "https://x/issues/4/extra", "nope"])
def test_a_url_without_an_issue_number_fails_without_a_comment(stored: str) -> None:
    tracker = Tracker()
    escalation, out = build(tracker, Inbox(), Store(rejected={A}, issue=stored))

    escalation.run()

    assert out == ["escalation: failed"] and tracker.calls == []


def test_unreadable_comments_on_a_stored_url_end_the_pass_with_no_search_or_second_issue() -> None:
    tracker, inbox = Tracker(fail=("issue_comments",)), Inbox()
    escalation, out = build(tracker, inbox, Store(rejected={A}, issue=URL))

    escalation.run()

    assert out == ["escalation: failed"]
    assert inbox.calls == 0
    assert tracker.names() == ["issue_comments"]


def test_a_failed_comment_continues_with_the_next_sha() -> None:
    tracker = Tracker(fail=("comment_on_issue",))
    escalation, out = build(tracker, Inbox(), Store(rejected={A, B}, issue=URL))

    escalation.run()

    assert out == ["escalation: failed", f"escalation: escalated {B}"]


@pytest.mark.parametrize(
    ("tracker_fail", "store_fail"),
    [
        ((), ("rejected",)),
        (("issue_comments",), ()),
        (("open_issue",), ()),
        ((), ("write",)),
    ],
)
def test_a_port_that_raises_never_makes_run_raise(
    tracker_fail: tuple[str, ...], store_fail: tuple[str, ...]
) -> None:
    tracker = Tracker(fail=tracker_fail)
    escalation, out = build(tracker, Inbox(), Store(rejected={A}, fail=store_fail))

    escalation.run()

    assert "escalation: failed" in out
    assert not any(HOSTILE in line for line in out)


def test_the_module_imports_only_ports_rotate_and_domain() -> None:
    tree = ast.parse(MODULE.read_text())
    relative = {
        "." * node.level + (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    }
    assert relative == {"..domain.stage0_rotation", ".ports", ".stage0_rotate"}
    absolute = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert absolute <= {"__future__", "re", "typing"}
