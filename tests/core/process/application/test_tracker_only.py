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
from agentic.core.process.domain import tracker_items as items
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
    resumed = apply(paused, rules.resumed(paused, None, at="2026-11-06T12:00:00+00:00"))
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
    resumed = apply(rebuilt, rules.resumed(rebuilt, None, at="2026-11-06T12:00:00+00:00"))
    assert rules.resume_target(resumed) == "tracker_only"

    recorded = {"at": "2026-10-24T10:00:00+00:00", "hash": rules.tracker_hash(ITEMS)}
    rebuilt_done = state_from(
        document.parse(document.render({**resumed, "tracker_checklist_posted": recorded}))
    )
    assert rebuilt_done["tracker_checklist_posted"] == recorded
    again = memory_ports()
    out = await tracker_only(rebuilt_done, cfg(tmp_path), again)
    assert again.tracker.comments == [] and out["status"] == "tracker_only"


@pytest.mark.parametrize("recorded", [{}, {"at": "x"}])
def test_an_empty_or_partial_record_is_nothing_posted_and_posts_once(recorded):
    ports = memory_ports()
    record = post_tracker_checklist(ports, "GH-307", ITEMS, recorded)
    assert record is not None and record["hash"] == rules.tracker_hash(ITEMS)
    assert len(ports.tracker.comments) == 1


def test_a_ready_comment_carrying_the_failure_block_does_not_stop_the_post():
    ports = memory_ports()
    block = rules.tracker_failure_block(ITEMS, "OSError", "p.md")
    ports.tracker.earlier = [f"Ready for review: https://x/pull/1\n\n{block}"]
    assert post_tracker_checklist(ports, "GH-307", ITEMS, {}) is not None
    assert len(ports.tracker.comments) == 1


async def test_the_node_with_an_empty_record_posts_and_ends_tracker_only(tmp_path):
    ports = memory_ports()
    patch = await tracker_only({**STATE, "tracker_checklist_posted": {}}, cfg(tmp_path), ports)
    assert len(ports.tracker.comments) == 1
    assert patch["status"] == "tracker_only"
    assert patch["tracker_checklist_posted"]["hash"] == rules.tracker_hash(ITEMS)


async def test_a_stored_legacy_checklist_posts_the_old_comment_byte_for_byte(tmp_path):
    ports = memory_ports()
    await tracker_only(STATE, cfg(tmp_path), ports)
    ((_, text),) = ports.tracker.comments
    assert text == (
        f"{TRACKER_WORK}the system has no access to do this part, so it is yours.\n\n"
        "- [ ] Post the verdict table on #307\n- [ ] Close the follow-ups that are done\n\n"
        "Task document: `.agentic/tasks/GH-307.md`\n\n" + rules.tracker_marker("GH-307", ITEMS)
    )


_ITEM = {"kind": "human", "question": "Ship it?", "recommendation": "Yes"}


async def test_unanswered_typed_items_escalate_and_post_nothing(tmp_path):
    state = {k: v for k, v in STATE.items() if k != "tracker_checklist"}
    ports = memory_ports()
    patch = await tracker_only({**state, "tracker_items": [_ITEM]}, cfg(tmp_path), ports)
    assert patch["status"] == "waiting_on_human" and patch["escalation_class"] == "questions"
    assert ports.tracker.comments == []


async def test_typed_items_all_answered_end_tracker_only_without_posting(tmp_path):
    from agentic.core.process.domain import tracker_items

    h = tracker_items.item_hash(_ITEM)
    state = {k: v for k, v in STATE.items() if k != "tracker_checklist"}
    state = {
        **state,
        "tracker_items": [_ITEM],
        "tracker_actions_done": {h: {"at": "t", "answered": "t"}},
    }
    ports = memory_ports()
    patch = await tracker_only(state, cfg(tmp_path), ports)
    assert patch["status"] == "tracker_only" and ports.tracker.comments == []


async def test_a_legacy_checklist_next_to_unanswered_typed_items_waits_for_the_decision(tmp_path):
    ports = memory_ports()
    patch = await tracker_only({**STATE, "tracker_items": [_ITEM]}, cfg(tmp_path), ports)
    assert patch["status"] == "waiting_on_human" and patch["escalation_class"] == "questions"
    assert not any(text.startswith(TRACKER_WORK) for _, text in ports.tracker.comments)


# --- GH-705: the node performs the plan's system items, reports them, then raises the human ones


NOTE = {"kind": "comment", "text": "Superseded by the newer item."}
SECOND = {"kind": "comment", "text": "The retry is tracked elsewhere."}
TYPED = {k: v for k, v in STATE.items() if k != "tracker_checklist"}


def typed(*plan):
    return {**TYPED, "tracker_items": list(plan)}


def done_line(*plan):
    return items.done_marker("GH-307", [items.item_hash(item) for item in plan])


class FailingOnCall(MemoryTracker):
    """`comment` raises `ValueError` on the calls (0-based) in `fails_on`, with a text that must
    never reach an escalation; every `issue_comments` read is counted."""

    SECRET = "gh said: token ghp_SECRET rejected"

    def __init__(self, fails_on):
        super().__init__()
        self.fails_on = set(fails_on)
        self.calls = 0
        self.reads = 0

    def comment(self, task_id, text):
        call, self.calls = self.calls, self.calls + 1
        if call in self.fails_on:
            raise ValueError(self.SECRET)
        super().comment(task_id, text)

    def issue_comments(self, task_id):
        self.reads += 1
        return super().issue_comments(task_id)


async def test_a_comment_and_a_human_item_post_the_note_and_the_report_then_raise_the_human(
    tmp_path,
):
    ports = memory_ports()
    patch = await tracker_only(typed(NOTE, _ITEM), cfg(tmp_path), ports)

    (_, note), (task_id, report) = ports.tracker.comments
    assert note == f"{items.PLAN_NOTE}{NOTE['text']}\n\n{items.marker(NOTE, 'GH-307')}"
    assert task_id == "GH-307"
    assert report == (f"{items.DONE}\n- comment posted on this issue\n\n{done_line(NOTE)}")
    assert report.startswith(items.DONE)
    assert patch["status"] == "waiting_on_human" and patch["escalation_class"] == "questions"
    done = patch["tracker_actions_done"]
    assert done[items.item_hash(NOTE)]["kind"] == "comment"
    assert set(done[items.item_hash(_ITEM)]) == {"at"}
    assert patch["tracker_human"] == [items.item_hash(_ITEM)]
    assert set(done) == {items.item_hash(NOTE), items.item_hash(_ITEM)}


async def test_a_plan_of_only_comment_items_ends_tracker_only_without_escalating(tmp_path):
    ports = memory_ports()
    patch = await tracker_only(typed(NOTE, SECOND), cfg(tmp_path), ports)

    assert patch["status"] == "tracker_only" and patch["current_role"] == "PjM"
    assert "escalation_class" not in patch and "tracker_human" not in patch
    assert set(patch["tracker_actions_done"]) == {items.item_hash(NOTE), items.item_hash(SECOND)}
    *_, (_, report) = ports.tracker.comments
    assert report.count("- comment posted on this issue") == 2
    assert report.splitlines()[-1] == done_line(NOTE, SECOND)
    assert ports.tracker.pull_requests == [] and "delivery_outcome" not in patch


async def test_a_second_run_over_the_resulting_state_posts_nothing(tmp_path):
    ports = memory_ports()
    first = await tracker_only(typed(NOTE, _ITEM), cfg(tmp_path), ports)
    posted = list(ports.tracker.comments)
    again = await tracker_only(apply(typed(NOTE, _ITEM), first), cfg(tmp_path), ports)
    assert ports.tracker.comments == posted and len(posted) == 2
    assert again["status"] == "tracker_only" and "escalation_class" not in again


async def test_a_lost_checkpoint_records_the_items_and_the_report_without_posting(tmp_path):
    ports = memory_ports()
    await tracker_only(typed(NOTE), cfg(tmp_path), ports)  # its record and checkpoint are lost
    assert len(ports.tracker.comments) == 2
    patch = await tracker_only(typed(NOTE), cfg(tmp_path), ports)
    assert len(ports.tracker.comments) == 2
    assert patch["tracker_actions_done"][items.item_hash(NOTE)]["kind"] == "comment"
    assert patch["status"] == "tracker_only"


@pytest.mark.parametrize(
    ("call", "step", "recorded"),
    [
        (0, "posting a plan note", 0),
        (1, "posting a plan note", 1),
        (2, "posting the done report", 2),
    ],
)
async def test_a_failed_post_escalates_failure_and_the_failed_step_leaves_no_record(
    tmp_path, call, step, recorded
):
    tracker = FailingOnCall({call})
    ports = memory_ports(tracker=tracker)
    patch = await tracker_only(typed(NOTE, SECOND, _ITEM), cfg(tmp_path), ports)

    assert patch["status"] == "waiting_on_human" and patch["escalation_class"] == "failure"
    assert step in patch["escalated"] and "ValueError" in patch["escalated"]
    assert FailingOnCall.SECRET not in str(patch)  # invariant: the class only, never the text
    assert len(patch.get("tracker_actions_done", {})) == recorded
    assert items.item_hash(_ITEM) not in patch.get("tracker_actions_done", {})
    assert "tracker_human" not in patch  # the human items are not raised on a failure
    if call == 2:
        assert len(ports.tracker.comments) == 2  # both notes are on the issue, no DONE
        assert all(not text.startswith(items.DONE) for _, text in ports.tracker.comments)
    resumed = apply(typed(NOTE, SECOND, _ITEM), patch)
    done = await tracker_only(resumed, cfg(tmp_path), ports)
    assert done["escalation_class"] == "questions"
    assert [t for _, t in ports.tracker.comments if t.startswith(items.DONE)] != []


async def test_a_plan_of_only_human_items_makes_no_tracker_call_at_all(tmp_path):
    tracker = FailingOnCall(set())
    ports = memory_ports(tracker=tracker)
    patch = await tracker_only(typed(_ITEM), cfg(tmp_path), ports)
    assert tracker.reads == 0 and tracker.calls == 0 and ports.tracker.comments == []
    assert patch["tracker_actions_done"] == {
        items.item_hash(_ITEM): {"at": patch["pause_log"][0]["at"]}
    }


async def test_nothing_is_read_when_no_system_item_was_done(tmp_path):
    tracker = FailingOnCall(set())
    answered = {items.item_hash(_ITEM): {"at": "t", "answered": "t"}}
    state = {**typed(_ITEM), "tracker_actions_done": answered}
    patch = await tracker_only(state, cfg(tmp_path), memory_ports(tracker=tracker))
    assert tracker.reads == 0 and patch["status"] == "tracker_only"


# --- GH-690: the `open_issue` kind through the node ----------------------------------------

OPEN_ISSUE = {"kind": "open_issue", "title": "Track the retry", "body": "The retry is lost."}


async def test_an_open_issue_and_a_human_item_open_the_issue_link_it_then_raise_the_human(
    tmp_path,
):
    ports = memory_ports()
    ports.tracker.issue_numbers_from = 61
    patch = await tracker_only(typed(OPEN_ISSUE, _ITEM), cfg(tmp_path), ports)

    ((title, body),) = ports.tracker.opened_issues
    assert title == "Track the retry" and ports.tracker.opened_labels == [items.TRIAGE_LABEL]
    assert body.endswith(f"From task: #307\n\n{items.marker(OPEN_ISSUE, 'GH-307')}")
    ((task_id, report),) = ports.tracker.comments
    url = "https://github.com/o/r/issues/61"
    assert task_id == "GH-307"
    assert report == f"{items.DONE}\n- issue opened ({url})\n\n{done_line(OPEN_ISSUE)}"
    assert patch["status"] == "waiting_on_human" and patch["escalation_class"] == "questions"
    done = patch["tracker_actions_done"]
    assert done[items.item_hash(OPEN_ISSUE)]["url"] == url
    assert set(done[items.item_hash(_ITEM)]) == {"at"}
