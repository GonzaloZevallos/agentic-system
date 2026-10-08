"""The review's CI evidence (GH-216): a `CI` section the system writes for the head under
review, and the findings it writes itself for a declared check that is red on that head.
Run with the fake tracker; the reviewer here approves in every case, so what changes the
verdict is the code, never the reviewer."""

import pytest

from agentic.core.process.application.review import review
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    ScriptedRunner,
    memory_ports,
    reviewed,
)

pytestmark = pytest.mark.anyio

PR = "https://github.com/o/r/pull/7"
HEAD_LINK = "https://github.com/o/r/actions/runs/1/job/10"
MASTER_LINK = "https://github.com/o/r/actions/runs/2/job/20"
SECTION = "Tech Lead: review api"

STATE = {
    "id": "GH-216",
    "title": "CI",
    "description": "Trust CI.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nx"},
    "subtask_id": "api",
    "subtasks": {
        "api": {
            "id": "api",
            "title": "api",
            "brief": "b",
            "seams": ["s"],
            "depends_on": [],
            "status": "delivered",
            "branch": "agentic/GH-216.api",
            "base": "base1",
        },
        "domain": {
            "id": "domain",
            "title": "domain",
            "brief": "b",
            "seams": ["s"],
            "depends_on": [],
            "status": "delivered",
            "branch": "agentic/GH-216.domain",
            "base": "base1",
        },
    },
}


def check(name, conclusion, link, status="COMPLETED"):
    return CiCheck(name=name, status=status, conclusion=conclusion, link=link)


def report(*checks, sha="base"):
    # "base" is what `MemoryWorkspace.full_head` answers for a path nobody moved.
    return CiReport(
        head_sha=sha, base_ref="master", base_sha="b" * 40, opened_at=None, checks=tuple(checks)
    )


def setup(
    head: CiReport | CiUnreadable | None, master: CiReport | CiUnreadable | None, *, ci=("e2e",)
):
    tracker = MemoryTracker()
    if head is not None:
        tracker.ci_reports[PR] = head
    if master is not None:
        tracker.branch_reports["master"] = master
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    state = {**STATE, "pull_request": PR}
    ports = memory_ports(runner, tracker=tracker, stack=MemoryStack(ci=ci))
    return state, runner, tracker, ports


async def run(state, ports):
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT)
    return await review(state, cfg, ports)


async def test_an_approval_over_red_ci_on_the_head_comes_out_returned():
    state, runner, _, ports = setup(
        report(check("e2e", "FAILURE", HEAD_LINK)), report(check("e2e", "SUCCESS", MASTER_LINK))
    )
    patch = await run(state, ports)
    assert patch["subtasks"]["api"]["status"] == "returned"
    assert patch["subtasks"]["api"]["change_category"] == "process-suite-red"
    [row] = patch["findings"]
    assert row["severity"] == "blocking" and row["location"] == "CI: e2e"
    assert "**returned** (`process-suite-red`)" in patch["sections"][SECTION]
    # What the reviewer read: the CI section with the head's link and master's state.
    prompt = runner.prompts("tech_lead_review")[0]
    assert HEAD_LINK in prompt and MASTER_LINK in prompt


async def test_red_on_the_head_and_on_master_stays_approved_with_an_observation():
    state, _, _, ports = setup(
        report(check("e2e", "FAILURE", HEAD_LINK)), report(check("e2e", "FAILURE", MASTER_LINK))
    )
    patch = await run(state, ports)
    assert patch["subtasks"]["api"]["status"] == "approved"
    [row] = patch["findings"]
    assert row["severity"] == "observation" and row["category"] == "process-suite-red"


async def test_green_ci_on_the_head_adds_no_finding():
    state, _, _, ports = setup(
        report(check("e2e", "SUCCESS", HEAD_LINK)), report(check("e2e", "SUCCESS", MASTER_LINK))
    )
    patch = await run(state, ports)
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert patch["findings"] == []


async def test_a_product_without_ci_is_reviewed_as_before_and_nothing_is_read():
    state, runner, tracker, ports = setup(report(check("e2e", "FAILURE", HEAD_LINK)), None, ci=())
    patch = await run(state, ports)
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert patch["findings"] == []
    assert tracker.ci_reads == []
    assert "CI" not in "".join(
        line for line in runner.prompts("tech_lead_review")[0].splitlines() if line.startswith("##")
    )


async def test_a_report_for_another_head_is_not_counted_and_the_section_says_so():
    state, runner, tracker, ports = setup(
        report(check("e2e", "FAILURE", HEAD_LINK), sha="c" * 40), None
    )
    patch = await run(state, ports)
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert tracker.ci_reads == [PR]  # master is not read for a report that is not this head's
    assert "not read" in runner.prompts("tech_lead_review")[0]
    assert HEAD_LINK not in runner.prompts("tech_lead_review")[0]


async def test_an_unreadable_report_is_not_a_finding_and_the_section_says_so():
    state, runner, _, ports = setup(CiUnreadable("gh_unreadable"), None)
    patch = await run(state, ports)
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "gh_unreadable" in runner.prompts("tech_lead_review")[0]


async def test_a_task_without_a_pull_request_says_none_exists_and_reads_nothing():
    state, runner, tracker, ports = setup(None, None)
    del state["pull_request"]
    patch = await run(state, ports)
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert tracker.ci_reads == []
    assert "no pull request" in runner.prompts("tech_lead_review")[0]


async def test_a_link_the_reviewer_already_quoted_is_not_recorded_twice():
    state, _, _, ports = setup(report(check("e2e", "FAILURE", HEAD_LINK)), None)
    quoted = {
        "location": "web/x.ts:3",
        "claim": "e2e fails",
        "evidence": f"red: {HEAD_LINK}",
        "severity": "blocking",
        "scope": "subtask",
        "category": "process-suite-red",
    }
    ports.runner.queues["tech_lead_review"] = [reviewed("returned", findings=[quoted])]
    patch = await run(state, ports)
    assert [row["location"] for row in patch["findings"]] == ["web/x.ts:3"]
