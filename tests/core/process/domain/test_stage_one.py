"""The rest of stage 1 in the domain: the roles that build, the interviews, the
subtasks and how the path routes through them (ADR-0011, ADR-0013, ADR-0014)."""

import pytest

from agentic.core.process.domain import rules, schemas
from agentic.core.process.domain.roles import (
    DEVELOPER,
    DEVELOPER_INTEGRATOR,
    INTERVIEW_DEFINITION,
    INTERVIEW_SOLUTION,
    PM,
    PRODUCT_ANALYST,
    PROMPTS_DIR,
    ROLES,
    TECH_LEAD,
    TECH_LEAD_EPHEMERAL,
    TECH_LEAD_REVIEWER,
    TECH_LEAD_REVIEWER_LIGHT,
    Interview,
    Pair,
    pair_for,
)
from agentic.core.process.domain.schemas import TechPlan
from agentic.core.process.domain.task import INTEGRATION, REDUCERS, apply


def test_every_role_has_a_prompt_file_and_a_unique_slug():
    slugs = [role.slug for role in ROLES]
    assert len(slugs) == len(set(slugs))
    for role in ROLES:
        assert (PROMPTS_DIR / f"{role.slug}.md").exists(), role.slug


def test_no_role_is_given_a_skill_the_model_cannot_invoke():
    """`disable-model-invocation: true` skills are for a human at a prompt; a role
    that asks for one gets refused (ADR-0013)."""
    plugin = PROMPTS_DIR.parents[3] / "plugins" / "mattpocock-skills" / "skills"
    if not plugin.is_dir():
        pytest.skip("plugin submodule not checked out")
    for role in ROLES:
        for skill in role.skills:
            files = list(plugin.glob(f"*/{skill}/SKILL.md"))
            assert files, f"{role.slug}: skill {skill} not in the plugin"
            assert "disable-model-invocation: true" not in files[0].read_text(), (role.slug, skill)


def test_only_developer_executes_and_tech_lead_only_reads():
    assert DEVELOPER.executes and DEVELOPER_INTEGRATOR.executes
    assert {"Edit", "Write", "Bash"} <= set(DEVELOPER.tools)
    # `resolving-merge-conflicts` was the integrator's alone (ADR-0013); the
    # dependency-merge path gives it to a subtask's own Developer too (ADR-0013,
    # addendum).
    assert set(DEVELOPER.skills) == {"tdd", "resolving-merge-conflicts"}
    assert "resolving-merge-conflicts" in DEVELOPER_INTEGRATOR.skills
    for role in (TECH_LEAD, TECH_LEAD_REVIEWER):
        assert not role.executes
        assert "Bash(git diff:*)" in role.allowed and "Bash(git log:*)" in role.allowed
        assert not {"Edit", "Write"} & set(role.tools)
    assert "code-review" in TECH_LEAD_REVIEWER.skills
    # The review-arm's light reviewer (issue #28): a Validador too, no `code-review`.
    assert TECH_LEAD_REVIEWER_LIGHT.skills == ()
    assert {"codebase-design", "domain-modeling"} <= set(TECH_LEAD.skills)


def test_interviews_name_who_asks_who_decides_and_who_writes():
    assert isinstance(INTERVIEW_DEFINITION, Interview)
    assert INTERVIEW_DEFINITION.interviewer.name == PRODUCT_ANALYST.name
    assert INTERVIEW_DEFINITION.interviewee.name == PM.name
    assert INTERVIEW_DEFINITION.author is PRODUCT_ANALYST
    assert INTERVIEW_DEFINITION.section == "Interview: Product Analyst→PM"
    assert INTERVIEW_SOLUTION.interviewer is TECH_LEAD_EPHEMERAL
    assert INTERVIEW_SOLUTION.interviewee.name == TECH_LEAD.name
    assert INTERVIEW_SOLUTION.author is TECH_LEAD
    # The interviewer writes only the glossary and the ADRs (ADR-0014).
    interviewer = INTERVIEW_DEFINITION.interviewer
    assert set(interviewer.skills) == {"grilling", "domain-modeling"}
    assert {"Edit(CONTEXT.md)", "Write(docs/**)"} <= set(interviewer.allowed)
    assert "Bash" not in interviewer.tools
    # Both sides of an interview see the same rounds; the interviewee decides on Opus.
    assert INTERVIEW_DEFINITION.interviewee.kind == "verifier"
    assert INTERVIEW_SOLUTION.interviewer.kind == "verifier"


def test_pair_per_subtask_is_tech_lead_to_developer():
    assert pair_for("s1") == Pair(by=TECH_LEAD, to=DEVELOPER, subtask="s1")
    assert pair_for("s1").key == "Tech Lead→Developer:s1"


def test_tech_plan_subtasks_are_slugs_with_seams_and_known_dependencies():
    plan = {
        "markdown": "### Solution",
        "subtasks": [
            {"id": "domain", "title": "Domain", "brief": "b", "seams": ["Dashboard.compute"]},
            {
                "id": "api",
                "title": "API",
                "brief": "b",
                "seams": ["GET /gyms/{id}/dashboard"],
                "depends_on": ["domain"],
            },
        ],
    }
    data = schemas.validate(TechPlan, plan, {})
    assert data["subtasks"][0]["depends_on"] == []
    assert data["subtasks"][1]["depends_on"] == ["domain"]

    with pytest.raises(ValueError, match="slug"):
        schemas.plan_check({**plan, "subtasks": [{**plan["subtasks"][0], "id": "Bad Id"}]})
    with pytest.raises(ValueError, match="unknown subtask"):
        schemas.plan_check({**plan, "subtasks": [{**plan["subtasks"][1], "depends_on": ["nope"]}]})
    with pytest.raises(ValueError, match="seams"):
        schemas.plan_check({**plan, "subtasks": [{**plan["subtasks"][0], "seams": []}]})
    with pytest.raises(ValueError, match="reserved"):
        schemas.plan_check({**plan, "subtasks": [{**plan["subtasks"][0], "id": INTEGRATION}]})
    with pytest.raises(ValueError, match="at least one"):
        schemas.plan_check({**plan, "subtasks": []})


def test_subtasks_merge_per_key_like_the_other_maps():
    assert REDUCERS["subtasks"].__name__ == "merge"
    state = {"subtasks": {"a": {"status": "pending"}, "b": {"status": "pending"}}}
    merged = apply(state, {"subtasks": {"a": {"status": "delivered"}}})
    assert merged["subtasks"] == {"a": {"status": "delivered"}, "b": {"status": "pending"}}


def sub(status, depends_on=()):
    return {"status": status, "depends_on": list(depends_on)}


def test_ready_subtasks_wait_for_their_dependencies():
    state = {
        "subtasks": {
            "a": sub("pending"),
            "b": sub("pending", ["a"]),
            "c": sub("returned", ["a"]),
            "d": sub("approved"),
            "e": sub("pending", ["d"]),
        }
    }
    assert rules.ready_subtasks(state) == ["a", "e"]
    state["subtasks"]["a"] = sub("approved")
    assert rules.ready_subtasks(state) == ["b", "c", "e"]
    assert rules.delivered_subtasks({"subtasks": {"a": sub("delivered"), "b": sub("pending")}}) == [
        "a"
    ]


def test_all_approved_ignores_nothing_and_needs_integration_only_for_several():
    assert not rules.all_approved({"subtasks": {"a": sub("approved"), "b": sub("delivered")}})
    assert rules.all_approved({"subtasks": {"a": sub("approved"), "b": sub("approved")}})
    assert not rules.needs_integration({"subtasks": {"a": sub("approved")}})
    assert rules.needs_integration({"subtasks": {"a": sub("approved"), "b": sub("approved")}})
    # Already there: no second integration.
    assert not rules.needs_integration(
        {"subtasks": {"a": sub("approved"), "b": sub("approved"), INTEGRATION: sub("pending")}}
    )


def test_routing_through_the_technical_stage():
    assert rules.after_validate({"status": "definition_ready"}) == "plan"
    # The plan hands the first wave to the fan-in; every wave comes back to it.
    assert rules.after_plan({"status": "planned"}) == "integrate"
    assert rules.after_plan({"escalated": "x"}) is None
    # Inside a branch: an escalation ends the branch, it never reaches the human here.
    assert rules.after_develop({}) == "review"
    assert rules.after_develop({"escalated": "x"}) is None
    # Something to build, rebuild or review: another wave; everything approved: deliver.
    assert rules.after_integrate({"subtasks": {"a": sub("returned")}}) == "pipeline"
    assert rules.after_integrate({"subtasks": {"a": sub("delivered")}}) == "pipeline"
    assert rules.after_integrate({"subtasks": {"a": sub("approved"), "b": sub("pending")}}) == (
        "pipeline"
    )
    assert rules.after_integrate({"subtasks": {"a": sub("approved")}}) == "deliver"
    assert rules.after_integrate({"subtasks": {"a": sub("approved"), "b": sub("approved")}}) == (
        "deliver"
    )
    assert rules.after_integrate({"subtasks": {"a": sub("returned")}, "escalated": "x"}) is None
    assert rules.after_deliver({}) is None


def test_subtask_sections_and_branches_are_named_after_the_subtask():
    assert rules.developer_section("api") == "Developer: api"
    assert rules.review_section("api") == "Tech Lead: review api"
    assert rules.developer_section(INTEGRATION) == "Developer: integration"
    assert rules.task_branch("GH-12") == "agentic/GH-12"
    assert rules.subtask_branch("GH-12", "api") == "agentic/GH-12.api"
