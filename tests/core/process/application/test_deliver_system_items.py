"""`deliver` runs the plan's system items (GH-689, ADR-0062 addendum): the `comment` is posted on
the task's own issue before the pull request, reported in READY, and a failure is a lapse plus a
block in the body. A resume does not read the system's prefixes as a decision.

Seams: `application.deliver.deliver` with fake `Ports`; `application.resume` (`SYSTEM_COMMENTS`,
`human_decision`)."""

import pytest

from agentic.core.process.application.deliver import READY, deliver
from agentic.core.process.application.resume import SYSTEM_COMMENTS, human_decision
from agentic.core.process.application.tracker_only import TRACKER_WORK
from agentic.core.process.domain import tracker_items as items
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

pytestmark = pytest.mark.anyio

NOTE = {"kind": "comment", "text": "Superseded by the newer item."}
TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
    "tracker_items": [NOTE],
}
REPORT = "comment posted on this issue"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def _plan_notes(ports):
    return [body for _, body in ports.tracker.comments if body.startswith(items.PLAN_NOTE)]


def _assert_system_comments(ports):
    for task_id, body in ports.tracker.comments:
        assert task_id == "GH-12"
        assert body.startswith(SYSTEM_COMMENTS)


async def test_the_plan_note_is_posted_before_the_pull_request_and_reported_in_ready(cfg):
    ports = memory_ports()
    patch = await deliver(TASK, cfg, ports)

    assert patch["status"] == "ready_for_review"
    (note,) = _plan_notes(ports)
    assert note == f"{items.PLAN_NOTE}{NOTE['text']}\n\n{items.marker(NOTE, 'GH-12')}"
    assert ports.tracker.comments[0][1] == note
    (ready,) = [b for _, b in ports.tracker.comments if b.startswith(READY)]
    assert REPORT in ready
    assert len(ports.tracker.pull_requests) == 1
    assert set(patch["tracker_actions_done"]) == {items.item_hash(NOTE)}
    _assert_system_comments(ports)


async def test_a_failed_post_is_a_lapse_and_a_block_in_the_body_and_the_pull_request_opens(cfg):
    ports = memory_ports()
    ports.tracker.comment_failures = 1
    patch = await deliver(TASK, cfg, ports)

    assert patch["status"] == "ready_for_review" and "escalation_class" not in patch
    assert "tracker_actions_done" not in patch
    assert [e["value"] for e in patch["pause_log"]] == ["tracker item comment: ValueError"]
    body = ports.tracker.pull_requests[0][2]
    assert "(`ValueError`); they are not done" in body
    assert items.summary_line(NOTE) in body
    assert NOTE["text"] in body and "Plan note" not in body
    (ready,) = [b for _, b in ports.tracker.comments if b.startswith(READY)]
    assert REPORT not in ready and "they are not done" in ready


async def test_a_push_refusal_keeps_the_record_and_a_redelivery_posts_no_second_note(cfg):
    ports = memory_ports()
    ports.workspace.push_failures = 1
    refused = await deliver(TASK, cfg, ports)
    assert refused["escalation_class"] == "failure"
    assert set(refused["tracker_actions_done"]) == {items.item_hash(NOTE)}
    assert len(_plan_notes(ports)) == 1

    again = await deliver(
        {**TASK, **refused, "tracker_actions_done": refused["tracker_actions_done"]}, cfg, ports
    )

    assert again["status"] == "ready_for_review"
    assert len(_plan_notes(ports)) == 1
    ready = [b for _, b in ports.tracker.comments if b.startswith(READY)]
    assert len(ready) == 1 and REPORT in ready[0]
    _assert_system_comments(ports)


async def test_a_lost_record_is_found_by_its_marker_and_posts_nothing_again(cfg):
    ports = memory_ports()
    first = await deliver(TASK, cfg, ports)
    lost = {k: v for k, v in first.items() if k != "tracker_actions_done"}

    await deliver({**TASK, **{k: v for k, v in lost.items() if k in ("pull_request",)}}, cfg, ports)

    assert len(_plan_notes(ports)) == 1


async def test_the_checklist_comment_is_posted_only_for_a_legacy_checklist(cfg):
    ports = memory_ports()
    await deliver(TASK, cfg, ports)
    assert all(not body.startswith(TRACKER_WORK) for _, body in ports.tracker.comments)

    legacy = memory_ports()
    await deliver({**TASK, "tracker_items": [], "tracker_checklist": ["Close #4"]}, cfg, legacy)
    assert any(body.startswith(TRACKER_WORK) for _, body in legacy.tracker.comments)


@pytest.mark.parametrize("prefix", [items.PLAN_NOTE, items.DONE])
def test_a_resume_does_not_read_a_system_prefix_as_a_decision(prefix):
    assert prefix in SYSTEM_COMMENTS
    comments = [
        "Ready for review: https://example/pr/1",
        f"{prefix}something the system wrote",
        "Go ahead.",
    ]
    assert human_decision(comments) == "Go ahead."


def test_a_human_comment_holding_the_prefix_mid_text_is_a_decision():
    comments = ["Ready for review: x", "I read the Plan note: it is fine.", "Also fine."]
    assert human_decision(comments) == "I read the Plan note: it is fine.\n\nAlso fine."
