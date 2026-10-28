"""The review in the shape of apostrophe #432 (GH-371): the issue's done criteria name a CI
check and a file the plan must change, neither happened, and the reviewer approves anyway.
The system writes its own blocking findings from the plan's `ci_required` and
`required_paths`, so the verdict never depends on the model noticing. The fake reviewer
approves with no findings in every case here."""

import pytest

from agentic.core.process.application.ports import GitError
from agentic.core.process.application.review import review
from agentic.core.process.domain.ci import CiCheck, CiReport
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

PR = "https://github.com/o/r/pull/9"
HEAD = "a" * 40
WEB_LINK = "https://github.com/o/r/actions/runs/5/job/50"
STACK_FILE = ".agentic/stack.yaml"
SECTION = "Tech Lead: review integration"


class FullIdWorkspace(MemoryWorkspace):
    def full_head(self, path):
        return HEAD


def subtask(name):
    return {
        "id": name,
        "title": name,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "delivered",
        "branch": f"agentic/GH-371.{name}",
        "base": "base1",
    }


def state(**plan):
    return {
        "id": "GH-371",
        "title": "Done criteria",
        "description": "Meet them.",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtask_id": "integration",
        "subtasks": {"web": subtask("web"), "integration": subtask("integration")},
        "pull_request": PR,
        **plan,
    }


def report(*checks):
    return CiReport(
        head_sha=HEAD, base_ref="master", base_sha="b" * 40, opened_at=None, checks=tuple(checks)
    )


def ports_for(*, ci=(), touched=(), head=None, workspace=None):
    tracker = MemoryTracker()
    if head is not None:
        tracker.ci_reports[PR] = head
    workspace = workspace or FullIdWorkspace()
    workspace.diff_paths = tuple(touched)
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    return memory_ports(runner, tracker=tracker, stack=MemoryStack(ci=ci), workspace=workspace)


async def run(task, ports):
    return await review(task, Configuration(product_dir=MemoryWorkspace.ROOT), ports)


async def test_an_integrated_diff_that_misses_every_required_path_is_returned_over_an_approval():
    ports = ports_for(touched=["web/src/app.ts"])
    patch = await run(state(required_paths=[STACK_FILE]), ports)
    assert patch["subtasks"]["integration"]["status"] == "returned"
    assert patch["subtasks"]["integration"]["change_category"] == "spec-scope"
    [row] = patch["findings"]
    assert row["severity"] == "blocking" and row["scope"] == "subtask"
    assert row["location"] == STACK_FILE
    assert row["claim"] == "the integrated diff touches none of the plan's required paths"
    assert row["status"] == "open"  # it carries evidence: a block without any is dismissed
    assert "**returned** (`spec-scope`)" in patch["sections"][SECTION]


async def test_the_same_review_with_the_required_path_touched_is_approved():
    ports = ports_for(touched=["web/src/app.ts", STACK_FILE])
    patch = await run(state(required_paths=[STACK_FILE]), ports)
    assert patch["subtasks"]["integration"]["status"] == "approved"
    assert patch["findings"] == []


async def test_the_diff_is_read_from_the_task_base_to_the_reviewed_head():
    workspace = FullIdWorkspace()
    await run(state(required_paths=[STACK_FILE]), ports_for(workspace=workspace))
    [(base, head)] = workspace.diff_reads
    assert head == HEAD and base == "HEAD"


async def test_a_subtask_that_is_not_the_integration_does_not_owe_the_required_paths():
    task = state(required_paths=[STACK_FILE])
    task["subtask_id"] = "web"
    workspace = FullIdWorkspace()
    patch = await run(task, ports_for(workspace=workspace))
    assert patch["subtasks"]["web"]["status"] == "approved"
    assert workspace.diff_reads == []


async def test_a_lone_subtask_is_the_reviewed_unit_and_owes_the_required_paths():
    task = state(required_paths=[STACK_FILE])
    task["subtasks"] = {"web": subtask("web")}
    task["subtask_id"] = "web"
    patch = await run(task, ports_for(touched=["web/src/app.ts"]))
    assert patch["subtasks"]["web"]["status"] == "returned"


async def test_a_diff_that_cannot_be_read_does_not_fail_the_review():
    class Unreadable(FullIdWorkspace):
        def changed_paths(self, base, head):
            raise GitError("git diff failed")

    patch = await run(state(required_paths=[STACK_FILE]), ports_for(workspace=Unreadable()))
    assert patch["subtasks"]["integration"]["status"] == "approved"


async def test_a_plan_check_red_on_the_head_returns_a_review_of_a_stack_without_ci():
    head = report(CiCheck(name="web", status="COMPLETED", conclusion="FAILURE", link=WEB_LINK))
    ports = ports_for(ci=(), head=head)
    patch = await run(state(ci_required=["web"]), ports)
    assert patch["subtasks"]["integration"]["status"] == "returned"
    [row] = patch["findings"]
    assert row["severity"] == "blocking" and row["location"] == "CI: web"
    assert row["status"] == "open"
    assert WEB_LINK in ports.runner.prompts("tech_lead_review")[0]


async def test_a_plan_check_green_on_the_head_adds_no_finding():
    head = report(CiCheck(name="web", status="COMPLETED", conclusion="SUCCESS", link=WEB_LINK))
    patch = await run(state(ci_required=["web"]), ports_for(head=head))
    assert patch["subtasks"]["integration"]["status"] == "approved"
    assert patch["findings"] == []


async def test_a_task_with_no_required_checks_or_paths_reads_nothing_and_is_reviewed_as_before():
    tracker_ports = ports_for(head=report(), touched=[])
    patch = await run(state(), tracker_ports)
    assert patch["subtasks"]["integration"]["status"] == "approved"
    assert patch["findings"] == []
    assert tracker_ports.tracker.ci_reads == []
    assert tracker_ports.workspace.diff_reads == []
