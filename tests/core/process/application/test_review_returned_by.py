"""Who returned a subtask (GH-318, ADR-0060): the review resets any other value of
`returned_by` to `review` the moment it returns the subtask itself, so the next Developer
reads its delta from the review section, and an approval leaves QA Performance due."""

import pytest

from agentic.core.process.application.review import review
from agentic.core.process.domain import roles, rules
from agentic.core.process.domain.task import INTEGRATION, apply
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryWorkspace, ScriptedRunner, memory_ports, reviewed

pytestmark = pytest.mark.anyio

SCENARIO = {
    "name": "sales-list",
    "volume": 10,
    "volume_unit": "rows",
    "basis": "b",
    "budgets": [{"metric": "time", "limit": 1, "unit": "ms", "source": "issue", "basis": "b"}],
}


def reopened(**more) -> dict:
    """The integration, delivered again after QA Performance returned it."""
    subtask = {
        "id": INTEGRATION,
        "title": "integration",
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "delivered",
        "branch": "agentic/GH-318",
        "base": "b",
        "returned_by": "qa_performance",
        **more,
    }
    return {
        "id": "GH-318",
        "title": "Perf",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "performance": [SCENARIO],
        "subtasks": {INTEGRATION: subtask},
        "subtask_id": INTEGRATION,
    }


CFG = Configuration(product_dir=MemoryWorkspace.ROOT)


async def test_a_review_return_after_a_qa_return_reads_its_delta_from_the_review_section():
    state = reopened()
    assert rules.return_section(state, INTEGRATION) == roles.QA_PERFORMANCE.name
    runner = ScriptedRunner(tech_lead_review=[reviewed("returned", "still slow")])
    patch = await review(state, CFG, memory_ports(runner))
    after = apply(state, patch)
    assert after["subtasks"][INTEGRATION]["status"] == "returned"
    assert after["subtasks"][INTEGRATION]["returned_by"] == "review"
    assert rules.return_section(after, INTEGRATION) == rules.review_section(INTEGRATION)


async def test_a_review_return_after_pm_final_validation_still_resets_it():
    state = reopened(returned_by="final_validation")
    runner = ScriptedRunner(tech_lead_review=[reviewed("returned", "wide")])
    after = apply(state, await review(state, CFG, memory_ports(runner)))
    assert after["subtasks"][INTEGRATION]["returned_by"] == "review"


async def test_a_subtask_that_never_had_the_key_still_gets_none_written():
    state = reopened()
    del state["subtasks"][INTEGRATION]["returned_by"]
    runner = ScriptedRunner(tech_lead_review=[reviewed("returned", "slow")])
    after = apply(state, await review(state, CFG, memory_ports(runner)))
    assert "returned_by" not in after["subtasks"][INTEGRATION]


async def test_an_approval_after_a_qa_return_makes_qa_performance_due_again():
    state = reopened()
    state["runs"] = [
        {"role": "QA Performance", "section": "QA Performance", "cost_usd": 0.1}
        | {"perf_verdict": "returned", "perf_head": "a" * 40}
    ]
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    after = apply(state, await review(state, CFG, memory_ports(runner)))
    assert after["subtasks"][INTEGRATION]["status"] == "approved"
    assert rules.qa_performance_due(after)
    assert rules.after_integrate(after) == "qa_performance"
