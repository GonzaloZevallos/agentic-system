"""The `split` node (ADR-0054) with a fake tracker and a fake inbox: a plan that does not
fit becomes chained, labelled issues and the task ends `split`; a cut `gh` resumes without
duplicating; a task that is itself a part never splits again."""

from datetime import datetime

import pytest

from agentic.core.process.application.split import SPLIT, split
from agentic.core.process.application.tracker_only import TRACKER_WORK
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

pytestmark = pytest.mark.anyio

PARTS = [
    {"title": "Catalogue", "body": "Extract the strings.", "depends_on": []},
    {"title": "Screens", "body": "Translate the screens.", "depends_on": [1]},
    {"title": "Emails", "body": "Translate the emails.", "depends_on": [1]},
    {"title": "Cleanup", "body": "Drop the old strings.", "depends_on": [2, 3]},
]
STATE = {
    "id": "GH-53",
    "title": "The whole app in English",
    "description": "Make the app English.",
    "status": "planned",
    "parts": PARTS,
    "invariants": ["No string is hard coded.", "Every part ships alone."],
}


def ports_with_numbered_issues():
    ports = memory_ports()
    ports.tracker.issue_numbers_from = 101
    return ports


async def test_a_plan_that_does_not_fit_opens_chained_labelled_issues_and_ends_split(tmp_path):
    ports = ports_with_numbered_issues()
    patch = await split(STATE, Configuration(product_dir=tmp_path), ports)

    assert patch["status"] == "split"
    assert ports.tracker.opened_labels == ["agentic"] * 4
    assert [title for title, _ in ports.tracker.opened_issues] == [p["title"] for p in PARTS]
    bodies = [body for _, body in ports.tracker.opened_issues]
    # The syntax the poller already reads, on the first line, one line naming every blocker.
    assert [rules.blockers_from_body(b) for b in bodies] == [(), (101,), (101,), (102, 103)]
    for number, body in enumerate(bodies, 1):
        assert rules.split_part_of(body) == ("GH-53", number)
        assert "1. No string is hard coded.\n2. Every part ships alone." in body
        assert ".agentic/tasks/GH-53.md" in body and "agentic/GH-53" in body
    assert patch["current_role"] == "PjM"
    assert datetime.fromisoformat(patch["finished_at"]).tzinfo is not None
    saved = ports.documents.saved[-1]
    assert saved["status"] == "split" and sorted(saved["split_issues"]) == ["1", "2", "3", "4"]
    ((task_id, text),) = ports.tracker.comments
    assert task_id == "GH-53" and "NOT relaunched" in text
    assert all(f"/issues/{n}" in text for n in (101, 102, 103, 104))


async def test_a_retry_after_a_cut_at_part_two_opens_only_what_is_missing(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    first = ports_with_numbered_issues()
    first.tracker.open_issue_fails_on = {1}
    cut = await split(STATE, cfg, first)

    assert cut["escalation_class"] == "failure" and cut["status"] == "waiting_on_human"
    assert "part 2" in cut["escalated"] and "1 of 4" in cut["escalated"]
    assert len(first.tracker.opened_issues) == 1 and first.tracker.comments == []
    paused = apply(first.documents.saved[-1], cut)
    assert paused["split_issues"] == {"1": "https://github.com/o/r/issues/101"}

    # The resumption: the state keeps part 1, and the inbox also shows part 2, which `gh`
    # created but the document never recorded.
    second = memory_ports()
    second.tracker.issue_numbers_from = 103
    part_two = (
        "https://github.com/o/r/issues/102",
        f"Blocked by: #101\n\nbody\n\n{rules.split_marker('GH-53', 2)}",
    )
    second.inbox._open_issues = [part_two]
    resumed = apply(paused, rules.resumed(paused, None))
    assert rules.resume_target(resumed) == "split"
    done = await split(resumed, cfg, second)

    assert done["status"] == "split"
    assert [title for title, _ in second.tracker.opened_issues] == ["Emails", "Cleanup"]
    final = second.documents.saved[-1]
    assert final["split_issues"]["2"] == "https://github.com/o/r/issues/102"
    # Cleanup is blocked by the screens issue found in the inbox and by the new emails one.
    assert rules.blockers_from_body(second.tracker.opened_issues[1][1]) == (102, 103)


async def test_a_part_of_a_partition_never_splits_again_and_escalates_estimate(tmp_path):
    ports = ports_with_numbered_issues()
    part = {
        **STATE,
        "type": "platform_enhancement",
        "description": f"Blocked by: #5\n\nBuild it.\n\n{rules.split_marker('GH-9', 2)}",
    }
    patch = await split(part, Configuration(product_dir=tmp_path), ports)

    assert ports.tracker.opened_issues == [] and ports.tracker.comments == []
    assert patch["escalation_class"] == "estimate" and patch["status"] == "waiting_on_human"
    assert "never split again" in patch["escalated"]
    # The same pause the plan stage raises (`rules.part_over_line`), figures unknown.
    assert patch["fit_question"] is True and "not known" in patch["escalated"]
    assert "proposed_estimate" not in patch
    # A human decision waives the fit and the plan is made again, whole.
    paused = apply(part, patch)
    resumption = rules.resumed(paused, "Build it whole.")
    assert resumption["fit_waived"] is True and resumption["parts"] == []
    assert rules.resume_target(apply(paused, resumption)) == "plan"


@pytest.mark.parametrize("failing", ["open_issues", "comment"])
async def test_a_gh_failure_outside_the_creation_escalates_failure_and_resumes_complete(
    failing, tmp_path
):
    cfg = Configuration(product_dir=tmp_path)
    ports = ports_with_numbered_issues()

    def broken(*_args):
        raise ValueError("gh: boom")

    target = ports.inbox if failing == "open_issues" else ports.tracker
    setattr(target, failing, broken)
    cut = await split(STATE, cfg, ports)

    assert cut["escalation_class"] == "failure" and "boom" in cut["escalated"]
    assert (failing == "comment") == (len(ports.tracker.opened_issues) == 4)
    paused = apply(ports.documents.saved[-1], cut)
    assert paused.get("status") != "split"

    again = ports_with_numbered_issues()
    done = await split(apply(paused, rules.resumed(paused, None)), cfg, again)
    assert done["status"] == "split"
    assert len(again.tracker.comments) == 1
    created = len(ports.tracker.opened_issues) + len(again.tracker.opened_issues)
    assert created == 4


def test_the_reader_of_blockers_unites_every_blocked_by_line():
    # A part still lists all its blockers on one line; the reader no longer needs it to.
    assert rules.blockers_from_body("Blocked by: #1\nBlocked by: #2, #1\n\nbody") == (1, 2)
    assert rules.blockers_from_body("Blocked by: #1, #2\n\nbody") == (1, 2)


# The split posts the tracker checklist (GH-350, ADR-0062): the Tech Lead's items, then one
# the system derives for each open issue still blocked by the task that will never close.
BLOCKED = ("https://github.com/o/r/issues/77", "Blocked by: #53\n\nWaits for the whole task.")
WITH_CHECKLIST = {**STATE, "tracker_checklist": ["Close #12 as superseded"]}


def checklist_comments(ports):
    return [text for _, text in ports.tracker.comments if text.startswith(TRACKER_WORK)]


def split_comments(ports):
    return [text for _, text in ports.tracker.comments if text.startswith(SPLIT)]


def ports_with_a_blocked_issue():
    ports = ports_with_numbered_issues()
    ports.inbox._open_issues = [BLOCKED]
    return ports


async def test_a_split_posts_the_checklist_with_the_derived_items_before_the_split_comment(
    tmp_path,
):
    ports = ports_with_a_blocked_issue()
    patch = await split(WITH_CHECKLIST, Configuration(product_dir=tmp_path), ports)

    assert patch["status"] == "split"
    (posted,) = checklist_comments(ports)
    assert "- [ ] Close #12 as superseded" in posted
    # The last part is the fourth: nothing depends on it, so its issue is #104.
    assert (
        "- [ ] #77 is blocked by #53, which this split does not close: "
        "close #53 or re-point #77 to #104"
    ) in posted
    assert [text.split(":")[0] for _, text in ports.tracker.comments] == [
        TRACKER_WORK.split(":")[0],
        SPLIT.split(":")[0],
    ]
    saved = ports.documents.saved[-1]
    assert saved["tracker_checklist"][0] == "Close #12 as superseded"
    assert len(saved["tracker_checklist"]) == 2
    assert saved["tracker_checklist_posted"]["hash"] == rules.tracker_hash(
        saved["tracker_checklist"]
    )
    assert rules.tracker_marker("GH-53", saved["tracker_checklist"]) in posted


async def test_a_split_without_a_checklist_and_without_blocked_issues_posts_only_the_split(
    tmp_path,
):
    ports = ports_with_numbered_issues()
    ports.inbox._open_issues = [("https://github.com/o/r/issues/5", "Blocked by: #9\n\nx")]
    patch = await split(STATE, Configuration(product_dir=tmp_path), ports)

    assert patch["status"] == "split"
    assert len(ports.tracker.comments) == 1 and checklist_comments(ports) == []
    assert "tracker_checklist" not in patch and "tracker_checklist_posted" not in patch
    assert "tracker_checklist" not in ports.documents.saved[-1]


async def test_the_system_items_alone_are_a_checklist_when_the_plan_wrote_none(tmp_path):
    ports = ports_with_a_blocked_issue()
    await split(STATE, Configuration(product_dir=tmp_path), ports)

    (posted,) = checklist_comments(ports)
    assert posted.count("- [ ]") == 1 and "#77 is blocked by #53" in posted


async def test_a_task_without_an_issue_number_derives_nothing(tmp_path):
    ports = ports_with_a_blocked_issue()
    state = {**STATE, "id": "local-1", "tracker_checklist": ["Close #12"]}
    await split(state, Configuration(product_dir=tmp_path), ports)

    (posted,) = checklist_comments(ports)
    assert posted.count("- [ ]") == 1


async def test_a_failed_checklist_post_resumes_with_one_checklist_and_one_split_comment(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    first = ports_with_a_blocked_issue()
    first.tracker.comment_failures = 1  # the checklist is the first comment
    cut = await split(WITH_CHECKLIST, cfg, first)

    assert cut["escalation_class"] == "failure" and "tracker checklist" in cut["escalated"]
    assert len(first.tracker.opened_issues) == 4 and first.tracker.comments == []
    paused = apply(first.documents.saved[-1], cut)
    assert len(paused["tracker_checklist"]) == 2  # saved before it was posted
    assert "tracker_checklist_posted" not in paused

    again = ports_with_a_blocked_issue()
    again.tracker.issue_numbers_from = 201  # a recomposition would show: it must not happen
    done = await split(apply(paused, rules.resumed(paused, None)), cfg, again)

    assert done["status"] == "split" and again.tracker.opened_issues == []
    assert len(checklist_comments(again)) == 1 and len(split_comments(again)) == 1
    assert "#77 is blocked by #53" in checklist_comments(again)[0]


async def test_a_failed_split_comment_after_the_checklist_resumes_without_posting_it_again(
    tmp_path,
):
    cfg = Configuration(product_dir=tmp_path)
    first = ports_with_a_blocked_issue()
    real = first.tracker.comment

    def comment(task_id, text):
        if text.startswith(SPLIT):
            raise ValueError("gh: boom")
        real(task_id, text)

    first.tracker.comment = comment
    cut = await split(WITH_CHECKLIST, cfg, first)

    assert cut["escalation_class"] == "failure" and "split comment" in cut["escalated"]
    assert len(checklist_comments(first)) == 1
    paused = apply(first.documents.saved[-1], cut)
    assert paused["tracker_checklist_posted"]["hash"] == rules.tracker_hash(
        paused["tracker_checklist"]
    )

    again = ports_with_a_blocked_issue()
    again.tracker.earlier = [checklist_comments(first)[0]]  # what the issue already holds
    done = await split(apply(paused, rules.resumed(paused, None)), cfg, again)

    assert done["status"] == "split"
    assert checklist_comments(again) == [] and len(split_comments(again)) == 1


async def test_a_resume_that_lost_the_posted_record_finds_the_marker_and_posts_nothing_again(
    tmp_path,
):
    cfg = Configuration(product_dir=tmp_path)
    first = ports_with_a_blocked_issue()
    real = first.tracker.comment

    def comment(task_id, text):
        if text.startswith(SPLIT):
            raise ValueError("gh: boom")
        real(task_id, text)

    first.tracker.comment = comment
    cut = await split(WITH_CHECKLIST, cfg, first)
    paused = apply(first.documents.saved[-1], cut)
    lost = {k: v for k, v in paused.items() if k != "tracker_checklist_posted"}

    again = ports_with_a_blocked_issue()
    again.tracker.earlier = [checklist_comments(first)[0]]
    done = await split(apply(lost, rules.resumed(lost, None)), cfg, again)

    assert done["status"] == "split"
    assert checklist_comments(again) == [] and len(split_comments(again)) == 1
    assert again.documents.saved[-1]["tracker_checklist_posted"]["hash"] == rules.tracker_hash(
        paused["tracker_checklist"]
    )
