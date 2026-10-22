"""The `split` node (ADR-0053) with a fake tracker and a fake inbox: a plan that does not
fit becomes chained, labelled issues and the task ends `split`; a cut `gh` resumes without
duplicating; a task that is itself a part never splits again."""

import pytest

from agentic.core.process.application.split import split
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


def test_the_reader_of_blockers_loses_every_blocker_after_the_first_line():
    # Why a part lists all its blockers on one line (invariant 8 cannot hold literally):
    # `rules.blockers_from_body` reads the first non-empty line only.
    assert rules.blockers_from_body("Blocked by: #1\nBlocked by: #2\n\nbody") == (1,)
    assert rules.blockers_from_body("Blocked by: #1, #2\n\nbody") == (1, 2)
