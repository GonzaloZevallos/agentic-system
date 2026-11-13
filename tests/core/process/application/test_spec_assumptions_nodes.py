"""The spec's assumptions at the nodes (GH-594 part 2b): with `state["spec"]` set, the Tech Lead's
plan (both its paths), the Developer's delivery and PM's final validation are asked for their
`*WithAssumptions` schema, and what they assumed lands in `state["assumptions"]` under the role's
name. Without a spec, the patch has no such key and the role gets the base schema class."""

from __future__ import annotations

import pytest

from agentic.core.process.application import final_validation as final_node
from agentic.core.process.application.develop import develop
from agentic.core.process.application.plan import plan
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import DEVELOPER, PM_FINAL, TECH_LEAD
from agentic.core.process.domain.spec_delivery import SpecContract
from agentic.core.process.domain.task import TaskState, apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.core.process.application.test_final_validation_close_docs import runner_with, state
from tests.fakes import (
    MemoryStack,
    RawResult,
    ScriptedRunner,
    delivered,
    final_validation,
    memory_ports,
    planned,
    questions,
)

pytestmark = pytest.mark.anyio

SPEC: SpecContract = {
    "path": "docs/specs/a.md",
    "slice": "s1",
    "commit": "a" * 40,
    "sha256": "b" * 64,
    "criteria": [],
    "screens": [],
    "risks": [],
    "touches_ui": False,
    "touches_copy": False,
}
PLAN_TASK: TaskState = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}
ASSUMED = "Totals are shown in USD"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def _assuming(result: RawResult, *texts: str) -> RawResult:
    result.structured_output["assumptions"] = list(texts)
    return result


def _rows(patch: TaskState, role: str, *texts: str) -> None:
    state_: TaskState = apply({}, patch)
    assert state_["assumptions"] == [{"role": role, "text": text} for text in texts]


def _asked(runner: ScriptedRunner, slug: str):
    return next(c["role"] for c in runner.calls if c["slug"] == slug)


# develop


async def _develop(cfg, result: RawResult, **extra):
    runner = ScriptedRunner(developer=[result])
    ports = memory_ports(runner, stack=MemoryStack())
    base, _ = building(ports)
    return runner, await develop({**base, **extra}, cfg, ports)


async def test_develop_with_a_spec_records_the_developers_assumptions(cfg):
    runner, patch = await _develop(
        cfg, _assuming(delivered(), ASSUMED, "  Dates   are UTC "), spec=SPEC
    )
    _rows(patch, DEVELOPER.name, ASSUMED, "Dates are UTC")
    # `Delivery` has no spec subclass: it already carries `assumptions`, and stays the same.
    assert _asked(runner, "developer").schema is schemas.Delivery


async def test_develop_without_a_spec_leaves_the_assumptions_out_of_the_patch(cfg):
    runner, patch = await _develop(cfg, _assuming(delivered(), ASSUMED))
    assert "assumptions" not in patch
    assert _asked(runner, "developer").schema is schemas.Delivery


async def test_develop_with_a_spec_and_nothing_assumed_adds_no_key(cfg):
    _, patch = await _develop(cfg, delivered(), spec=SPEC)
    assert "assumptions" not in patch


# plan: the interview path (plan.py `_interview_and_plan`) and the cold path (`_cold_draft`)


async def _plan(cfg, *, cold: bool, spec: SpecContract | None):
    interview = {} if cold else {"tech_lead_ephemeral": [questions(done=True)]}
    runner = ScriptedRunner(tech_lead=[_assuming(planned(), ASSUMED)], **interview)
    cfg = Configuration(product_dir=cfg.product_dir, skip_ephemeral=cold)
    task = {**PLAN_TASK, "spec": spec} if spec else dict(PLAN_TASK)
    return runner, await plan(task, cfg, memory_ports(runner))  # type: ignore[arg-type]


@pytest.mark.parametrize("cold", [False, True], ids=["interview path", "cold path"])
async def test_plan_with_a_spec_records_the_tech_leads_assumptions(cfg, cold):
    runner, patch = await _plan(cfg, cold=cold, spec=SPEC)
    _rows(patch, TECH_LEAD.name, ASSUMED)
    assert _asked(runner, "tech_lead").schema is schemas.TechPlanWithAssumptions


@pytest.mark.parametrize("cold", [False, True], ids=["interview path", "cold path"])
async def test_plan_without_a_spec_adds_no_key_and_asks_for_the_base_schema(cfg, cold):
    runner, patch = await _plan(cfg, cold=cold, spec=None)
    assert "assumptions" not in patch
    assert _asked(runner, "tech_lead").schema is schemas.TechPlan


# final validation


async def _final(cfg, result: RawResult, **extra):
    runner = runner_with(result)
    ports = memory_ports(runner)
    start = {**state(), **extra}
    checkout = ports.workspace.task_checkout(start["id"])
    return runner, await final_node.final_validation(start, cfg, ports, checkout=checkout)


async def test_final_validation_with_a_spec_records_pms_assumptions(cfg):
    runner, patch = await _final(cfg, _assuming(final_validation(), ASSUMED), spec=SPEC)
    _rows(patch, PM_FINAL.name, ASSUMED)
    assert _asked(runner, "pm_final").schema is schemas.FinalValidationWithAssumptions


async def test_final_validation_without_a_spec_adds_no_key_and_asks_for_the_base_schema(cfg):
    runner, patch = await _final(cfg, _assuming(final_validation(), ASSUMED))
    assert "assumptions" not in patch
    assert _asked(runner, "pm_final").schema is schemas.FinalValidation
