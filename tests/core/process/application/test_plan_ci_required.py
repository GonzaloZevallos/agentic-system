"""The checks a plan requires reach the state (GH-371): `plan.plan` writes `ci_required` and
`required_paths` as validated, and writes nothing for a plan that names none, so the
document of an old task, or of one that requires no external check, stays as it was."""

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


async def _plan(tmp_path, **plan_fields):
    result = planned()
    result.structured_output.update(plan_fields)
    cfg = Configuration(product_dir=tmp_path, skip_ephemeral=True)
    return await plan(TASK, cfg, memory_ports(ScriptedRunner(tech_lead=[result])))


async def test_the_required_checks_and_paths_land_in_the_state_and_survive_the_document(tmp_path):
    patch = await _plan(
        tmp_path,
        ci_required=["web", "e2e"],
        ci_none_reason="",
        required_paths=[".agentic/stack.yaml"],
    )
    assert patch["ci_required"] == ["web", "e2e"]
    assert patch["required_paths"] == [".agentic/stack.yaml"]
    parsed = document.parse(document.render({**TASK, **patch}))
    assert parsed["ci_required"] == ["web", "e2e"]
    assert parsed["required_paths"] == [".agentic/stack.yaml"]


async def test_a_plan_that_requires_no_check_writes_neither_key(tmp_path):
    patch = await _plan(tmp_path)
    assert "ci_required" not in patch and "required_paths" not in patch
    rendered = document.render({**TASK, **patch})
    assert "ci_required" not in rendered and "required_paths" not in rendered
    assert "ci_required" not in document.parse(rendered)
