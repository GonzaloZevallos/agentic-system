"""The plan asks for a promotion, it never applies one (GH-591, part 2): `plan.plan` stores the
Tech Lead's `promote` line as `plan_promote_request`, from the author's section, and writes no
`path` and no `promotion`. The serialized system step applies it later and, when the owner label
refuses, `promotion.refusal_notice` puts the one-line notice into the asking role's section."""

import pytest

from agentic.core.process.application import promotion
from agentic.core.process.application.plan import plan
from agentic.core.process.domain import rules, task_path
from agentic.core.process.domain.roles import SOLUTION_INTERVIEW, TECH_LEAD
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, memory_ports, planned

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "path": "light",
    "initial_path": "light",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


async def _plan(tmp_path, **plan_fields):
    result = planned()
    result.structured_output.update(plan_fields)
    cfg = Configuration(product_dir=tmp_path, skip_ephemeral=True)
    return await plan(TASK, cfg, memory_ports(ScriptedRunner(tech_lead=[result])))


async def test_the_plan_node_applies_the_request_after_the_model_ran(tmp_path):
    """The request is applied by the node's own system step, through `task_path.promote`, and
    never left in the document: only `path` and `promotion` are written."""
    patch = await _plan(tmp_path, promote="the data conversion is bigger than the issue says")
    assert patch["path"] == "full"
    assert patch["promotion"]["kind"] == "role"
    assert patch["promotion"]["from_role"] == TECH_LEAD.name
    assert patch["promotion"]["text"] == "the data conversion is bigger than the issue says"
    assert "plan_promote_request" not in patch
    assert "initial_path" not in patch


async def test_under_the_owner_label_nothing_is_promoted_and_the_notice_is_written(tmp_path):
    result = planned()
    result.structured_output.update(promote="bigger")
    cfg = Configuration(product_dir=tmp_path, skip_ephemeral=True)
    state = {**TASK, "path_label": "light"}
    patch = await plan(state, cfg, memory_ports(ScriptedRunner(tech_lead=[result])))
    assert "path" not in patch and "promotion" not in patch
    assert "path:light" in patch["sections"]["Tech Lead"]


async def test_a_plan_that_asks_nothing_writes_no_key(tmp_path):
    patch = await _plan(tmp_path)
    assert "plan_promote_request" not in patch


def test_the_notice_goes_into_the_plan_section_as_one_line():
    state = {**TASK, "sections": {**TASK["sections"], "Tech Lead": "### Solution\n\nHex."}}
    patch, notice = task_path.promote(
        {**state, "path_label": "light"}, TECH_LEAD.name, "role", "t", "why"
    )
    assert patch == {} and notice
    after = apply(state, promotion.refusal_notice(state, TECH_LEAD.name, notice))
    assert after["sections"]["Tech Lead"] == f"### Solution\n\nHex.\n\n{notice}"
    assert "\n" not in notice


def test_the_notice_goes_into_the_review_section_that_asked():
    section = rules.review_section("api")
    state = {**TASK, "sections": {section: "### Findings\n\nNone."}}
    after = apply(state, promotion.refusal_notice(state, section, "refused: owner label"))
    assert after["sections"][section].endswith("\n\nrefused: owner label")
    assert set(after["sections"]) == {section}


def test_no_notice_changes_nothing_and_a_missing_section_is_created():
    assert promotion.refusal_notice(TASK, "Tech Lead", None) == {}
    after = apply(TASK, promotion.refusal_notice(TASK, "Tech Lead", "refused"))
    assert after["sections"]["Tech Lead"] == "refused"


def _promoted_state(**extra):
    unchanged = {
        "id": "api",
        "title": "API",
        "brief": "Build it.",
        "seams": ["GET /x"],
        "depends_on": [],
        "status": "approved",
        "branch": "agentic/GH-12.api",
        "base": "b0",
        "reviewed_head": "h1",
    }
    return {
        **TASK,
        "path": "full",
        "initial_path": "light",
        "promotion": {"from_role": "system", "kind": "risk_area", "text": "x", "at": "t"},
        "subtasks": {"api": unchanged},
        **extra,
    }


def critique():
    from agentic.core.process.application.ports import RawResult

    return RawResult(
        {"markdown": "### Findings\n\nNone.", "findings": [], "invariants_proposed": []},
        0.1,
        3,
        "cr",
    )


async def test_a_replan_revises_and_keeps_an_unchanged_subtask_whole(tmp_path):
    """No interview section: the critic runs once; the subtask that keeps its id and brief is not
    in the patch (state and `reviewed_head` stay), a new one starts pending."""
    runner = ScriptedRunner(
        tech_lead=[planned({"id": "web", "title": "Web", "brief": "Show it.", "seams": ["page"]})]
        * 2,
        tech_lead_critic=[critique()],
    )
    cfg = Configuration(product_dir=tmp_path)
    patch = await plan(_promoted_state(), cfg, memory_ports(runner))
    assert "api" not in patch["subtasks"]
    assert patch["subtasks"]["web"]["status"] == "pending"
    assert patch["plan_route"]["after_promotion"] is True
    assert "plan_sizer" not in runner.slugs and "tech_lead_ephemeral" not in runner.slugs


async def test_a_replan_with_an_interview_section_resumes_it_and_never_starts_a_new_one(tmp_path):
    from tests.fakes import questions

    runner = ScriptedRunner(
        tech_lead_ephemeral=[questions(done=True, session="e1")], tech_lead=[planned()]
    )
    state = _promoted_state(sections={**TASK["sections"], SOLUTION_INTERVIEW: "### Round 1"})
    patch = await plan(state, Configuration(product_dir=tmp_path), memory_ports(runner))
    assert patch["plan_route"]["after_promotion"] is True
    # The only subtask is the final unit: unchanged and approved, it is rechecked against the PRD.
    api = patch["subtasks"]["api"]
    assert (api["status"], api["prd_recheck"], api["reviewed_head"]) == ("delivered", True, "h1")
    assert api["branch"] == "agentic/GH-12.api"
