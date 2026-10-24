"""The `tracker_only` node (GH-336) with the fake tracker: a plan whose only work is on the
issue tracker posts a checklist for the human once and ends `tracker_only`, with no role,
no pull request and no `delivery_outcome`; a cut `gh` resumes into the node, not into
`plan`; the comment the code posts is never read back as the human's decision."""

from datetime import datetime

import pytest

from agentic.core.process.application.resume import SYSTEM_COMMENTS, human_decision, state_from
from agentic.core.process.application.tracker_only import (
    TRACKER_WORK,
    post_tracker_checklist,
    tracker_only,
)
from agentic.core.process.domain import document, rules
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryTracker, memory_ports

pytestmark = pytest.mark.anyio

ITEMS = ["Post the verdict table on #307", "Close the follow-ups that are done"]
STATE = {
    "id": "GH-307",
    "title": "Triage the follow-ups",
    "description": "Triage.",
    "type": "feature",
    "status": "planned",
    "tracker_checklist": ITEMS,
}


class UnreadableTracker(MemoryTracker):
    def issue_comments(self, task_id: str) -> list[str]:
        pytest.fail("the issue was read although the record holds the hash")


def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_the_node_posts_one_comment_and_ends_with_no_delivery(tmp_path):
    ports = memory_ports()
    patch = await tracker_only(STATE, cfg(tmp_path), ports)

    ((task_id, text),) = ports.tracker.comments
    assert task_id == "GH-307"
    assert text.startswith(TRACKER_WORK)
    assert "- [ ] Post the verdict table on #307\n- [ ] Close the follow-ups that are done" in text
    assert ".agentic/tasks/GH-307.md" in text
    assert text.splitlines()[-1] == rules.tracker_marker("GH-307", ITEMS)
    assert patch["status"] == "tracker_only" and patch["current_role"] == "PjM"
    assert datetime.fromisoformat(patch["finished_at"]).tzinfo is not None
    assert patch["tracker_checklist_posted"]["hash"] == rules.tracker_hash(ITEMS)
    # Invariant 3: no role, no pull request, no delivery outcome.
    assert ports.tracker.pull_requests == [] and ports.tracker.closes_issue_calls == []
    assert "delivery_outcome" not in patch and "pull_request" not in patch
    saved = ports.documents.saved[-1]
    assert saved["status"] == "tracker_only"
    assert saved["tracker_checklist_posted"] == patch["tracker_checklist_posted"]


def test_the_comment_starts_with_a_registered_system_prefix_and_is_no_decision():
    ports = memory_ports()
    post_tracker_checklist(ports, "GH-307", ITEMS, None)
    ((_, text),) = ports.tracker.comments
    assert TRACKER_WORK in SYSTEM_COMMENTS
    assert human_decision([text]) is None
    # A human's answer after it is the decision, and the checklist is not part of it.
    assert human_decision([text, "Done, resume."]) == "Done, resume."


async def test_running_twice_with_the_same_items_posts_once(tmp_path):
    ports = memory_ports()
    first = await tracker_only(STATE, cfg(tmp_path), ports)
    again = await tracker_only(apply(STATE, first), cfg(tmp_path), ports)
    assert len(ports.tracker.comments) == 1
    assert again["status"] == "tracker_only"


async def test_a_lost_record_does_not_post_again_because_the_marker_is_on_the_issue(tmp_path):
    ports = memory_ports()
    await tracker_only(STATE, cfg(tmp_path), ports)  # the checkpoint and the record are lost
    patch = await tracker_only(STATE, cfg(tmp_path), ports)
    assert len(ports.tracker.comments) == 1
    assert patch["tracker_checklist_posted"]["hash"] == rules.tracker_hash(ITEMS)


def test_a_recorded_hash_reads_nothing_and_posts_nothing():
    ports = memory_ports(tracker=UnreadableTracker())
    recorded = {"at": "2026-10-24T10:00:00+00:00", "hash": rules.tracker_hash(ITEMS)}
    assert post_tracker_checklist(ports, "GH-307", ITEMS, recorded) is None
    assert ports.tracker.comments == []


def test_a_task_id_alone_never_decides_it_was_posted():
    """Invariant 8: a marker of the same task with other items does not count."""
    ports = memory_ports()
    ports.tracker.earlier = [f"{TRACKER_WORK}old\n\n{rules.tracker_marker('GH-307', ['other'])}"]
    patch = post_tracker_checklist(ports, "GH-307", ITEMS, None)
    assert patch is not None and len(ports.tracker.comments) == 1


async def test_a_changed_checklist_posts_a_second_comment_that_says_it_replaces(tmp_path):
    ports = memory_ports()
    first = await tracker_only(STATE, cfg(tmp_path), ports)
    changed = {**apply(STATE, first), "tracker_checklist": [*ITEMS, "Retitle #14"]}
    await tracker_only(changed, cfg(tmp_path), ports)

    (_, one), (_, two) = ports.tracker.comments
    assert "replaces" not in one.splitlines()[0]
    assert "replaces" in two.splitlines()[0] and two.startswith(TRACKER_WORK)
    assert two.splitlines()[-1] == rules.tracker_marker("GH-307", [*ITEMS, "Retitle #14"])


async def test_a_failed_comment_escalates_failure_and_the_resume_ends_with_one_comment(tmp_path):
    ports = memory_ports()
    ports.tracker.comment_failures = 1
    stopped = await tracker_only(STATE, cfg(tmp_path), ports)

    assert stopped["status"] == "waiting_on_human" and stopped["escalation_class"] == "failure"
    assert "tracker_checklist_posted" not in stopped
    assert ports.tracker.comments == []
    paused = apply(STATE, stopped)
    assert paused["tracker_checklist"] == ITEMS  # the plan stays in the state
    resumed = apply(paused, rules.resumed(paused, None))
    assert rules.resume_target(resumed) == "tracker_only"

    done = await tracker_only(resumed, cfg(tmp_path), ports)
    assert done["status"] == "tracker_only" and len(ports.tracker.comments) == 1


async def test_a_reconstructed_paused_document_resumes_into_the_node_and_a_record_blocks_it(
    tmp_path,
):
    ports = memory_ports()
    ports.tracker.comment_failures = 1
    stopped = await tracker_only({**STATE, "tracker_checklist": ITEMS}, cfg(tmp_path), ports)
    paused = apply(STATE, stopped)
    rebuilt = state_from(document.parse(document.render(paused)))
    assert rebuilt["tracker_checklist"] == ITEMS
    resumed = apply(rebuilt, rules.resumed(rebuilt, None))
    assert rules.resume_target(resumed) == "tracker_only"

    recorded = {"at": "2026-10-24T10:00:00+00:00", "hash": rules.tracker_hash(ITEMS)}
    rebuilt_done = state_from(
        document.parse(document.render({**resumed, "tracker_checklist_posted": recorded}))
    )
    assert rebuilt_done["tracker_checklist_posted"] == recorded
    again = memory_ports()
    out = await tracker_only(rebuilt_done, cfg(tmp_path), again)
    assert again.tracker.comments == [] and out["status"] == "tracker_only"
