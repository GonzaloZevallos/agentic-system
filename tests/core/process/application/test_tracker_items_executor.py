"""`run_system_items` (GH-689) with the in-memory tracker: a comment item is posted once on the
task's own issue, the record is written only after the post succeeded, and a lost record is
found again by the marker.

Seam: `application.tracker_items.run_system_items` with a fake `Ports` tracker."""

import pytest

from agentic.core.process.application.tracker_items import run_system_items
from agentic.core.process.domain import tracker_items as items
from tests.fakes import MemoryDocuments, MemoryTracker, memory_ports

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
