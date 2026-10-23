"""A convened plan reaches the state (GH-318, ADR-0060): `plan.plan` writes the Tech Lead's
`performance` scenarios as validated, and writes nothing for a plan that convenes none, so
the document of a task that is not convened stays as it was."""

import pytest

from agentic.core.process.application.plan import plan
from agentic.core.process.domain import document
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, memory_ports, planned

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}
SCENARIO = {
    "name": "list",
    "volume": 50000,
    "volume_unit": "rows",
    "basis": "the largest account",
    "budgets": [
        {
            "metric": "list_ms",
            "limit": 300,
            "unit": "ms",
            "source": "issue",
            "basis": "the issue says 300 ms",
        }
    ],
}


async def _plan(tmp_path, performance):
    result = planned()
    result.structured_output["performance"] = performance
    cfg = Configuration(product_dir=tmp_path, skip_ephemeral=True)
    return await plan(TASK, cfg, memory_ports(ScriptedRunner(tech_lead=[result])))


async def test_a_convened_plan_lands_in_the_state_and_survives_the_document(tmp_path):
    patch = await _plan(tmp_path, [SCENARIO])
    assert patch["performance"] == [SCENARIO]
    state = {**TASK, **patch}
    assert document.parse(document.render(state))["performance"] == patch["performance"]


async def test_a_plan_that_convenes_nothing_writes_no_performance(tmp_path):
    patch = await _plan(tmp_path, [])
    assert "performance" not in patch
    assert "performance" not in document.render({**TASK, **patch})
