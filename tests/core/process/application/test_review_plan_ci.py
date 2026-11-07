"""The review in the shape of apostrophe #432 (GH-371): the issue's done criteria name a CI
check and a file the plan must change, neither happened, and the reviewer approves anyway.
The system writes its own blocking findings from the plan's `ci_required` and
`required_paths`, so the verdict never depends on the model noticing. The fake reviewer
approves with no findings in every case here."""

from pathlib import Path

import pytest
import yaml

from agentic.core.process.application.ports import GitError
from agentic.core.process.application.review import _required_paths_findings, review
from agentic.core.process.domain import rules
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


# GH-538: the review of the reviewed unit may move or drop required paths after a human decision.
GH_538 = yaml.safe_load(
    (Path(__file__).parents[1] / "domain" / "fixtures" / "GH-538.yaml").read_text(encoding="utf-8")
)
PLANNED = GH_538["plan_required_paths"]
MOVED = GH_538["touched"]
DECIDED = "### Decision 1\n\nEscalated: x\n\n" + GH_538["decision"]


def decided(*extra_decisions, **plan):
    human = "\n\n".join([DECIDED, *extra_decisions])
    task = state(required_paths=PLANNED, **plan)
    task["sections"] = {**task["sections"], "Human": human}
    return task


def revising(paths, decision=1, reason=GH_538["decision"]):
    result = reviewed("approved")
    result.structured_output.update(
        revised_required_paths=list(paths), revision_decision=decision, revision_reason=reason
    )
    return result


async def revise(task, result, touched=MOVED):
    ports = ports_for(touched=touched)
    ports.runner.queues["tech_lead_review"] = [result]
    return await run(task, ports)


async def test_gh_538_without_a_revision_the_claim_holds_and_the_evidence_names_the_decision():
    # The record the document keeps has no evidence column, so the row is read where it is built.
    [row] = _required_paths_findings(decided(), "HEAD", HEAD, [], ports_for(touched=MOVED))
    assert row["claim"] == "the integrated diff touches none of the plan's required paths"
    assert "a human decision (1) is on record and no revision cites it" in row["evidence"]
    assert "`revision_decision: 1`" in row["evidence"]


async def test_gh_538_a_revision_citing_the_decision_moves_the_path_and_adds_no_row():
    patch = await revise(decided(), revising(MOVED))
    assert patch["findings"] == []
    assert patch["subtasks"]["integration"]["status"] == "approved"
    [record] = patch["required_paths_revisions"]
    assert record["revises"] == PLANNED and record["paths"] == MOVED and record["decision"] == 1
    assert record["reason"] == GH_538["decision"] and record["at"]


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda t: (t, "web"), "not_reviewed_unit"),
        (lambda t: ({**t, "required_paths": []}, "integration"), "nothing_to_revise"),
        (lambda t: ({**t, "sections": {"PM": "x"}}, "integration"), "no_decision"),
        (lambda t: (t, "integration"), "not_latest"),
        (lambda t: (t, "integration"), "decision_used"),
        (lambda t: (t, "integration"), "unchanged"),
        (lambda t: (t, "integration"), "adds_paths"),
        (lambda t: (t, "integration"), "forbidden_path"),
    ],
)
def test_path_revision_refuses_with_one_closed_reason(make, reason):
    task = decided("### Decision 2\n\nlater")
    task["required_paths_revisions"] = [
        {"revises": PLANNED, "paths": PLANNED, "decision": 1, "reason": "q", "at": "t"}
    ]
    task, unit = make(task)
    paths, decision = MOVED, 2
    if reason == "not_latest":
        decision = 1
    if reason == "decision_used":
        task["required_paths_revisions"][0]["decision"] = 2
    if reason == "unchanged":
        paths = PLANNED
    if reason == "adds_paths":
        paths = [*MOVED, "extra"]
    if reason == "forbidden_path":
        paths = [".agentic/tasks/x"]
    assert rules.path_revision(task, unit, paths, decision, "q", "t") == reason


def test_a_same_size_move_and_a_drop_are_both_accepted():
    task = decided()
    moved = rules.path_revision(task, "integration", MOVED, 1, "q", "t")
    dropped = rules.path_revision(task, "integration", [], 1, "q", "t")
    assert moved["paths"] == MOVED and moved["revises"] == PLANNED
    assert dropped["paths"] == [] and dropped["revises"] == PLANNED


def test_a_revision_never_outlives_the_plan_it_revised_and_survives_an_identical_replan():
    task = decided("### Decision 2\n\nsecond")
    record = rules.path_revision(task, "integration", MOVED[:1], 2, "q", "t")
    task["required_paths_revisions"] = [record]
    assert rules.required_paths_in_force(task) == MOVED[:1]
    assert rules.required_paths_in_force({**task, "required_paths": list(PLANNED)}) == MOVED[:1]
    assert rules.required_paths_in_force({**task, "required_paths": PLANNED[:2]}) == PLANNED[:2]
    # the order of `revises` is part of the match
    assert rules.required_paths_in_force({**task, "required_paths": PLANNED[::-1]}) == PLANNED[::-1]


def test_without_revisions_the_list_in_force_is_exactly_the_plans():
    assert rules.required_paths_in_force({"required_paths": PLANNED}) == PLANNED
    assert rules.required_paths_in_force({}) == []
    assert rules.revision_hint({"required_paths": PLANNED}) == ""


async def test_a_refused_revision_leaves_a_system_line_and_the_list_in_force():
    patch = await revise(decided(), revising(MOVED, decision=3))
    assert "required_paths_revisions" not in patch
    assert "Path revision refused by the system: `not_latest`" in patch["sections"][SECTION]
    assert [row["claim"] for row in patch["findings"]] == [
        "the integrated diff touches none of the plan's required paths"
    ]


async def test_a_shadow_review_that_carries_a_revision_writes_none():
    from tests.core.process.application.test_shadow_review import cfg, integration_state

    task = integration_state(required_paths=PLANNED)
    task["sections"] = {**task["sections"], "Human": DECIDED}
    runner = ScriptedRunner(
        tech_lead_review=[reviewed("approved")],
        **{f"tech_lead_review__shadow-{n}": [revising(MOVED)] for n in ("api", "domain", "ui")},
    )
    ports = memory_ports(runner)
    patch = await review(task, cfg(), ports)
    # the shadows did run, so the absence below is not a vacuous pass
    assert {r["subtask"] for r in patch["shadow_reviews"]} == {"api", "domain", "ui"}
    assert {r["status"] for r in patch["shadow_reviews"]} == {"ran"}
    assert "required_paths_revisions" not in patch


async def test_the_review_prompt_shows_the_list_in_force_and_the_decision_on_the_unit_only():
    task = decided()
    ports = ports_for(touched=MOVED)
    await run(task, ports)
    prompt = ports.runner.calls[0]["system_prompt"]
    assert PLANNED[0] in prompt and "latest human decision is 1, uncited" in prompt
    task["subtask_id"] = "web"
    ports = ports_for(touched=MOVED)
    await run(task, ports)
    assert "latest human decision" not in ports.runner.calls[0]["system_prompt"]
