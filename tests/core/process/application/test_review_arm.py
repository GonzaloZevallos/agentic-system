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
