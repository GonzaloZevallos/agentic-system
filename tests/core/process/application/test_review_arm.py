"""The review-arm subtask (issue #28): the light reviewer role, the `INTEGRATION`
condition that always keeps the full reviewer, and the `experiment` snapshot `review`
stamps on its own patch. `test_technical_stage.py` stays byte-identical: this is where
the arm's own behaviour is proven instead."""

import pytest

from agentic.core.process.application.review import review
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryWorkspace, ScriptedRunner, memory_ports, reviewed

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-28",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


def sub(id_, status="delivered", **more):
    return {
        "id": id_,
        "title": id_,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": status,
        **more,
    }


def two_delivered() -> dict:
    return {
        **TASK,
        "subtasks": {
            "api": sub("api", branch="agentic/GH-28.api", base="base1"),
            "domain": sub("domain", branch="agentic/GH-28.domain", base="base1"),
        },
        "sections": {**TASK["sections"], "Developer: api": "x", "Developer: domain": "y"},
    }


def integration_state() -> dict:
    return {
        **TASK,
        "subtasks": {INTEGRATION: sub(INTEGRATION, branch="agentic/GH-28", base="b")},
        "subtask_id": INTEGRATION,
    }


async def test_a_default_review_stamps_no_experiment():
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT)
    state = {**two_delivered(), "subtask_id": "api"}
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    patch = await review(state, cfg, memory_ports(runner))
    assert patch["experiment"] == {}
    assert runner.slugs == ["tech_lead_review"]


async def test_a_light_review_stamps_the_three_keys_with_the_resolved_model():
    cfg = Configuration(
        product_dir=MemoryWorkspace.ROOT,
        review_mode="light",
        model_overrides={"tech_lead_review_light": "claude-cheap-5"},
    )
    state = {**two_delivered(), "subtask_id": "api"}
    runner = ScriptedRunner(tech_lead_review_light=[reviewed("approved")])
    patch = await review(state, cfg, memory_ports(runner))
    assert patch["experiment"] == {
        "review_mode": "light",
        "skip_ephemeral": "False",
        "reviewer_model": "claude-cheap-5",
    }
    assert runner.slugs == ["tech_lead_review_light"]


async def test_the_integration_always_gets_the_full_reviewer_even_under_light_mode():
    # The plan's own invariant: without this, arm B has no expensive pass anywhere.
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT, review_mode="light")
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    patch = await review(integration_state(), cfg, memory_ports(runner))
    assert runner.slugs == ["tech_lead_review"]
    assert "code-review" in runner.calls[0]["role"].skills
    # A non-empty snapshot still, because `review_mode` differs from its default even
    # though the integration itself ran the full reviewer.
    assert patch["experiment"]["review_mode"] == "light"


async def test_a_returned_light_review_still_lowers_an_unbacked_block():
    # The light reviewer shares `Review`'s schema and `rules.lower_unbacked_blocks`:
    # this arm changes the prompt and the role, never the verdict-derivation rule.
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT, review_mode="light")
    state = {**two_delivered(), "subtask_id": "api"}
    unbacked = {
        "location": "x.py:1",
        "claim": "looks off",
        "evidence": "",
        "severity": "blocking",
        "scope": "subtask",
        "category": "spec-behaviour",
    }
    runner = ScriptedRunner(tech_lead_review_light=[reviewed("returned", findings=[unbacked])])
    patch = await review(state, cfg, memory_ports(runner))
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "Lowered by the system" in patch["sections"]["Tech Lead: review api"]


def test_reviewer_for_light_mode_cannot_be_reached_by_a_bad_subtask_id_alone():
    """The reject case for the INTEGRATION condition: mode alone must never be enough
    to hand out the light reviewer, whatever string happens to name the subtask -- only
    the exact `task.INTEGRATION` constant keeps the full one."""
    from agentic.core.process.domain.roles import TECH_LEAD_REVIEWER, reviewer_for

    reviewer = reviewer_for({}, "not-actually-integration", mode="light")
    assert reviewer.slug != TECH_LEAD_REVIEWER.slug


# -- the affected-only form, shown to the reviewer (ADR-0046) ---------------------------


def _with_stack(**declared) -> tuple[dict, MemoryWorkspace, object]:
    from agentic.core.process.domain.rules import task_branch
    from tests.fakes import MemoryStack

    workspace = MemoryWorkspace()
    workspace.existing_branches[task_branch("GH-28")] = ("head0", "taskbase1")
    return {**two_delivered(), "subtask_id": "api"}, workspace, MemoryStack(**declared)


async def test_the_reviewer_is_shown_the_affected_form_with_the_task_base_and_may_run_it():
    state, workspace, stack = _with_stack(
        checks={"tests": "pytest"},
        affected_checks={"tests": "pytest --since {base}"},
        affected_fallback=("uv.lock",),
    )
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT)
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    await review(state, cfg, memory_ports(runner, workspace=workspace, stack=stack))
    call = runner.calls[0]
    assert "affected-only form: `pytest --since taskbase1`" in call["system_prompt"]
    assert "{base}" not in call["system_prompt"]
    assert "Bash(pytest --since:*)" in call["role"].allowed
    assert "Bash(pytest:*)" in call["role"].allowed


async def test_a_product_with_no_affected_form_gives_the_reviewer_today_s_checks_and_rules():
    from agentic.core.process.domain import presubmit
    from agentic.core.process.domain.roles import reviewer_for

    state, workspace, stack = _with_stack(checks={"tests": "pytest"})
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT)
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    await review(state, cfg, memory_ports(runner, workspace=workspace, stack=stack))
    call = runner.calls[0]
    assert presubmit.describe_checks({"tests": "pytest"}) in call["system_prompt"]
    assert "affected-only" not in call["system_prompt"]
    assert call["role"].allowed == reviewer_for({"tests": "pytest"}, "api").allowed


async def test_the_light_reviewer_gets_the_affected_rule_too():
    state, workspace, stack = _with_stack(
        checks={"tests": "pytest"}, affected_checks={"tests": "pytest --since {base}"}
    )
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT, review_mode="light")
    runner = ScriptedRunner(tech_lead_review_light=[reviewed("approved")])
    await review(state, cfg, memory_ports(runner, workspace=workspace, stack=stack))
    assert "Bash(pytest --since:*)" in runner.calls[0]["role"].allowed
    assert "affected-only form: `pytest --since taskbase1`" in runner.calls[0]["system_prompt"]


async def test_the_diff_base_stays_the_reviewed_head_while_the_affected_base_is_the_tasks():
    # The increment under review and the base the affected form selects against are two
    # different things: the second is the whole branch, whatever head was reviewed last.
    state, workspace, stack = _with_stack(
        checks={"tests": "pytest"}, affected_checks={"tests": "pytest --since {base}"}
    )
    state["subtasks"]["api"]["reviewed_head"] = "reviewed9"
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT)
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    await review(state, cfg, memory_ports(runner, workspace=workspace, stack=stack))
    prompt = runner.calls[0]["system_prompt"]
    assert "pytest --since taskbase1" in prompt
    assert "pytest --since reviewed9" not in prompt
