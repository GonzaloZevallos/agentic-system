"""The review asks for a promotion, it never applies one (GH-591, part 2): the request is stored
on the reviewed unit's own subtask, under the review section's name, and a `risk_area` request
already there is never replaced. The node writes no `path` and no `promotion`."""

import pytest

from agentic.core.process.application.review import review
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import INTEGRATION, apply
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryWorkspace, ScriptedRunner, memory_ports, reviewed

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT)


def _state(unit: str = "api", **subtask) -> dict:
    subtasks = {
        unit: {
            "id": unit,
            "title": unit,
            "brief": "b",
            "seams": ["s"],
            "depends_on": [],
            "status": "delivered",
            "branch": "agentic/GH-9",
            "base": "b",
            **subtask,
        }
    }
    return {
        "id": "GH-9",
        "title": "T",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "path": "light",
        "initial_path": "light",
        "subtasks": subtasks,
        "subtask_id": unit,
    }


def _asking(kind: str = "approved", promote: str | None = "it converts stored data"):
    result = reviewed(kind)
    if promote is not None:
        result.structured_output["promote"] = promote
    return result


@pytest.mark.parametrize("verdict", ["approved", "returned"])
async def test_the_request_is_stored_on_the_reviewed_unit_whatever_the_verdict(verdict):
    state = _state()
    runner = ScriptedRunner(tech_lead_review=[_asking(verdict)])
    patch = await review(state, CFG, memory_ports(runner))
    after = apply(state, patch)
    assert after["subtasks"]["api"]["promote_request"] == {
        "kind": "role",
        "from_role": rules.review_section("api"),
        "text": "it converts stored data",
    }
    assert after["subtasks"]["api"]["status"] == (
        "approved" if verdict == "approved" else "returned"
    )
    assert "path" not in patch or patch["path"] == "light"
    assert "promotion" not in patch


async def test_the_integration_review_stores_it_on_the_integration_unit():
    state = _state(INTEGRATION)
    runner = ScriptedRunner(tech_lead_review=[_asking()])
    after = apply(state, await review(state, CFG, memory_ports(runner)))
    assert after["subtasks"][INTEGRATION]["promote_request"]["from_role"] == rules.review_section(
        INTEGRATION
    )


async def test_a_review_that_asks_nothing_leaves_the_subtask_without_the_key():
    state = _state()
    runner = ScriptedRunner(tech_lead_review=[_asking(promote=None)])
    after = apply(state, await review(state, CFG, memory_ports(runner)))
    assert "promote_request" not in after["subtasks"]["api"]


async def test_a_risk_area_request_already_on_the_unit_is_never_replaced():
    risk = {"kind": "risk_area", "from_role": "system", "text": "data-migration"}
    state = _state(promote_request=risk)
    runner = ScriptedRunner(tech_lead_review=[_asking()])
    after = apply(state, await review(state, CFG, memory_ports(runner)))
    assert after["subtasks"]["api"]["promote_request"] == risk


async def test_a_review_that_asks_nothing_keeps_a_request_already_stored():
    risk = {"kind": "risk_area", "from_role": "system", "text": "data-migration"}
    state = _state(promote_request=risk)
    runner = ScriptedRunner(tech_lead_review=[_asking(promote=None)])
    after = apply(state, await review(state, CFG, memory_ports(runner)))
    assert after["subtasks"]["api"]["promote_request"] == risk
