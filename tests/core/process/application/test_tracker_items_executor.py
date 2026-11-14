"""`run_system_items` (GH-689) with the in-memory tracker: a comment item is posted once on the
task's own issue, the record is written only after the post succeeded, and a lost record is
found again by the marker.

Seam: `application.tracker_items.run_system_items` with a fake `Ports` tracker."""

import pytest

from agentic.core.process.adapters.evaluation import EvaluationTracker
from agentic.core.process.application.tracker_items import run_system_items
from agentic.core.process.domain import tracker_items as items
from agentic.core.process.domain.landing import IssueRef
from tests.fakes import MemoryDocuments, MemoryInbox, MemoryTracker, memory_ports

FIRST = {"kind": "comment", "text": "Superseded by the newer item."}
SECOND = {"kind": "comment", "text": "The retry is tracked elsewhere."}
HUMAN = {"kind": "human", "question": "Ship it?", "recommendation": "Yes."}
STATE = {"id": "GH-9", "tracker_items": [HUMAN, FIRST, SECOND], "tracker_actions_done": {}}


class SaveFails(MemoryDocuments):
    def save(self, state):
        raise OSError("disk full")


class UnreadableTracker(MemoryTracker):
    def issue_comments(self, task_id):
        raise ValueError("gh issue view failed: scripted")


def test_every_comment_is_posted_on_the_tasks_issue_with_the_prefix_and_its_marker():
    ports = memory_ports()
    run = run_system_items(STATE, ports)
    assert [task_id for task_id, _ in ports.tracker.comments] == ["GH-9", "GH-9"]
    for (_, body), item in zip(ports.tracker.comments, [FIRST, SECOND], strict=True):
        assert body == f"{items.PLAN_NOTE}{item['text']}\n\n{items.marker(item, 'GH-9')}"
    assert run.failed is None and run.left == [] and run.lapses == []
    done = run.state["tracker_actions_done"]
    assert set(done) == {items.item_hash(FIRST), items.item_hash(SECOND)}
    assert all(record["kind"] == "comment" and record["at"] for record in done.values())
    assert ports.documents.saved[-1]["tracker_actions_done"] == done


def test_a_human_item_is_neither_posted_nor_recorded():
    ports = memory_ports()
    run = run_system_items({**STATE, "tracker_items": [HUMAN]}, ports)
    assert ports.tracker.comments == [] and run.state["tracker_actions_done"] == {}


def test_an_item_with_a_record_is_skipped_and_the_issue_is_not_read():
    class Unread(MemoryTracker):
        def issue_comments(self, task_id):
            pytest.fail("the issue was read although every item has a record")

    done = {items.item_hash(i): {"at": "t", "kind": "comment"} for i in (FIRST, SECOND)}
    ports = memory_ports(tracker=Unread())
    run = run_system_items({**STATE, "tracker_actions_done": done}, ports)
    assert ports.tracker.comments == [] and run.failed is None and run.left == []


def test_a_found_marker_posts_nothing_and_records_the_item():
    tracker = MemoryTracker()
    tracker.earlier = [f"{items.PLAN_NOTE}x\n\n{items.marker(FIRST, 'GH-9')}"]
    ports = memory_ports(tracker=tracker)
    run = run_system_items({**STATE, "tracker_items": [FIRST]}, ports)
    assert ports.tracker.comments == []
    assert items.item_hash(FIRST) in run.state["tracker_actions_done"]


def test_a_failed_post_leaves_no_record_and_stops_with_the_rest_left():
    tracker = MemoryTracker()
    tracker.comment_failures = 1
    ports = memory_ports(tracker=tracker)
    run = run_system_items(STATE, ports)
    assert isinstance(run.failed, ValueError)
    assert ports.tracker.comments == [] and run.state["tracker_actions_done"] == {}
    assert run.left == [FIRST, SECOND]
    assert ports.documents.saved == []


def test_a_failure_after_one_post_keeps_its_record_and_leaves_the_next():
    tracker = MemoryTracker()
    ports = memory_ports(tracker=tracker)
    state = run_system_items({**STATE, "tracker_items": [FIRST]}, ports).state
    tracker.comment_failures = 1
    run = run_system_items({**state, "tracker_items": [FIRST, SECOND]}, ports)
    assert run.left == [SECOND] and items.item_hash(FIRST) in run.state["tracker_actions_done"]
    assert items.item_hash(SECOND) not in run.state["tracker_actions_done"]


def test_a_failed_read_stops_and_posts_nothing():
    ports = memory_ports(tracker=UnreadableTracker())
    run = run_system_items(STATE, ports)
    assert isinstance(run.failed, ValueError) and ports.tracker.comments == []
    assert run.left == [FIRST, SECOND]


def test_a_failed_save_is_a_lapse_and_a_rerun_finds_the_marker_and_posts_nothing():
    tracker = MemoryTracker()
    run = run_system_items(
        {**STATE, "tracker_items": [FIRST]}, memory_ports(tracker=tracker, documents=SaveFails())
    )
    assert run.failed is None and run.lapses == ["tracker record save: OSError"]
    assert len(tracker.comments) == 1
    # The record was lost with the failed save: the rerun starts from the state without it.
    again = run_system_items({**STATE, "tracker_items": [FIRST]}, memory_ports(tracker=tracker))
    assert len(tracker.comments) == 1
    assert items.item_hash(FIRST) in again.state["tracker_actions_done"]


# --- GH-690: the `open_issue` kind ---------------------------------------------------------


ISSUE = {"kind": "open_issue", "title": "Track the retry", "body": "The retry is lost.\nLook."}
OTHER = {"kind": "open_issue", "title": "Rename the flag", "body": "It is confusing."}
OPEN_STATE = {"id": "GH-9", "tracker_items": [ISSUE], "tracker_actions_done": {}}


def _ports(tracker=None, inbox=None):
    return memory_ports(tracker=tracker or MemoryTracker(), inbox=inbox or MemoryInbox())


def test_an_open_issue_is_opened_with_the_system_label_footer_and_marker_then_recorded():
    ports = _ports()
    run = run_system_items(OPEN_STATE, ports)
    ((title, body),) = ports.tracker.opened_issues
    assert title == ISSUE["title"]
    assert body == f"{ISSUE['body']}\n\nFrom task: #9\n\n{items.marker(ISSUE, 'GH-9')}"
    assert ports.tracker.opened_labels == [items.TRIAGE_LABEL]
    assert ports.tracker.created_labels == [items.TRIAGE_LABEL]
    record = run.state["tracker_actions_done"][items.item_hash(ISSUE)]
    assert record == {"at": record["at"], "kind": "open_issue", "url": ports.tracker.url}
    assert run.failed is None and run.left == [] and run.lapses == []
    assert ports.documents.saved[-1]["tracker_actions_done"] == run.state["tracker_actions_done"]


def test_a_marker_in_an_open_issue_opens_nothing_and_records_that_issue():
    marked = IssueRef(
        "https://github.com/o/r/issues/41", "Old", f"text\n\n{items.marker(ISSUE, 'GH-9')}"
    )
    inbox = MemoryInbox()
    inbox.refs = [
        IssueRef("https://github.com/o/r/issues/2", "Track the retry", "no marker"),
        marked,
    ]
    ports = _ports(inbox=inbox)
    run = run_system_items(OPEN_STATE, ports)
    assert ports.tracker.opened_issues == [] and ports.tracker.created_labels == []
    record = run.state["tracker_actions_done"][items.item_hash(ISSUE)]
    assert record["url"] == marked.url and record["kind"] == "open_issue"
    assert (
        items.report([ISSUE], run.state["tracker_actions_done"]) == f"- issue opened ({marked.url})"
    )


def test_a_reworded_body_with_the_same_title_finds_its_issue_by_the_marker():
    inbox = MemoryInbox()
    inbox.refs = [IssueRef("https://github.com/o/r/issues/5", "x", items.marker(ISSUE, "GH-9"))]
    reworded = {**ISSUE, "title": "  track THE   retry ", "body": "Another body."}
    ports = _ports(inbox=inbox)
    run = run_system_items({**OPEN_STATE, "tracker_items": [reworded]}, ports)
    assert ports.tracker.opened_issues == []
    assert run.state["tracker_actions_done"][items.item_hash(reworded)]["url"].endswith("/5")


def test_an_issue_with_the_same_title_and_no_marker_is_not_adopted():
    inbox = MemoryInbox()
    inbox.refs = [IssueRef("https://github.com/o/r/issues/5", ISSUE["title"], "no marker here")]
    ports = _ports(inbox=inbox)
    run_system_items(OPEN_STATE, ports)
    assert len(ports.tracker.opened_issues) == 1


def test_a_cut_list_opens_nothing_ensures_no_label_and_records_nothing():
    inbox = MemoryInbox()
    inbox.refs_error = ValueError("the list may be cut")
    ports = _ports(inbox=inbox)
    run = run_system_items(OPEN_STATE, ports)
    assert isinstance(run.failed, ValueError)
    assert ports.tracker.opened_issues == [] and ports.tracker.created_labels == []
    assert run.state["tracker_actions_done"] == {} and ports.documents.saved == []
    assert run.left == [ISSUE]


def test_a_task_without_an_issue_opens_nothing_and_records_nothing():
    ports = _ports()
    run = run_system_items({**OPEN_STATE, "id": "EVAL-1"}, ports)
    assert isinstance(run.failed, ValueError)
    assert ports.tracker.opened_issues == [] and ports.tracker.created_labels == []
    assert run.state["tracker_actions_done"] == {}


def test_a_failed_label_or_open_leaves_no_record():
    for attribute, value in (("ensure_label_error", "no label"), ("open_issue_fails_on", {0})):
        tracker = MemoryTracker()
        setattr(tracker, attribute, value)
        ports = _ports(tracker=tracker)
        run = run_system_items(OPEN_STATE, ports)
        assert isinstance(run.failed, ValueError)
        assert tracker.opened_issues == [] and run.state["tracker_actions_done"] == {}


def test_two_open_issues_read_the_list_once_and_open_two_issues():
    inbox = MemoryInbox()
    tracker = MemoryTracker()
    tracker.issue_numbers_from = 50
    ports = _ports(tracker=tracker, inbox=inbox)
    run = run_system_items({**OPEN_STATE, "tracker_items": [ISSUE, OTHER]}, ports)
    assert inbox.refs_calls == 1
    assert [title for title, _ in tracker.opened_issues] == [ISSUE["title"], OTHER["title"]]
    urls = [r["url"] for r in run.state["tracker_actions_done"].values()]
    assert urls == ["https://github.com/o/r/issues/50", "https://github.com/o/r/issues/51"]


def test_the_footer_names_the_number_the_inbox_gives_and_the_body_is_bounded():
    class Part(MemoryInbox):
        def issue_of(self, task_id):
            return 77

    body = "\n".join(["x" * 100] * 19)  # under MAX_BODY_CHARS
    item = {**ISSUE, "body": body}
    assert len(items.parse([item])[0]["body"]) <= items.MAX_BODY_CHARS
    ports = _ports(inbox=Part())
    run_system_items({**OPEN_STATE, "tracker_items": [item]}, ports)
    ((_, posted),) = ports.tracker.opened_issues
    footer = f"\n\nFrom task: #77\n\n{items.marker(item, 'GH-9')}"
    assert posted == body + footer
    assert len(posted) <= items.MAX_BODY_CHARS + len(footer)


def test_a_comment_and_a_human_item_never_reach_the_open_issue_calls():
    class Strict(MemoryInbox):
        def open_issue_refs(self):
            pytest.fail("a plan without an open_issue read the open issues")

        def issue_of(self, task_id):
            pytest.fail("a plan without an open_issue asked for its issue")

    ports = _ports(inbox=Strict())
    run = run_system_items({**STATE, "tracker_items": [HUMAN, FIRST]}, ports)
    assert run.failed is None and ports.tracker.created_labels == []


def test_an_open_issue_item_with_a_record_is_skipped_without_reading_the_list():
    inbox = MemoryInbox()
    done = {items.item_hash(ISSUE): {"at": "t", "kind": "open_issue", "url": "https://x/1"}}
    ports = _ports(inbox=inbox)
    run = run_system_items({**OPEN_STATE, "tracker_actions_done": done}, ports)
    assert inbox.refs_calls == 0 and ports.tracker.opened_issues == [] and run.failed is None


def test_a_failed_save_after_the_open_is_a_lapse_and_the_rerun_adopts_the_issue_by_marker():
    tracker = MemoryTracker()
    inbox = MemoryInbox()
    run = run_system_items(
        OPEN_STATE, memory_ports(tracker=tracker, inbox=inbox, documents=SaveFails())
    )
    assert run.lapses == ["tracker record save: OSError"] and len(tracker.opened_issues) == 1
    # The issue is now listed, as the tracker would list it, and the record was lost.
    ((_, body),) = tracker.opened_issues
    inbox.refs = [IssueRef("https://github.com/o/r/issues/8", ISSUE["title"], body)]
    again = run_system_items(OPEN_STATE, memory_ports(tracker=tracker, inbox=inbox))
    assert len(tracker.opened_issues) == 1
    assert again.state["tracker_actions_done"][items.item_hash(ISSUE)]["url"].endswith("/8")


def test_an_evaluation_tracker_opens_nothing_and_leaves_no_record():
    inner = MemoryTracker()
    ports = _ports(tracker=EvaluationTracker(inner))
    run = run_system_items(OPEN_STATE, ports)
    assert isinstance(run.failed, ValueError) and "write nothing" in str(run.failed)
    assert inner.opened_issues == [] and inner.created_labels == [] and inner.open_issue_calls == 0
    assert run.state["tracker_actions_done"] == {}
