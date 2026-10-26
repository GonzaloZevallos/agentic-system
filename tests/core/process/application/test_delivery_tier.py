"""The wiring of the fast tier (GH-358): `delivery_checks` narrows the declared checks, the
fan-in stamps the task head for a tiered product only, and `develop` gates and briefs the
Developer on the fast tier alone, with today's prompt for a product that declares none."""

from __future__ import annotations

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.application.integrate import integrate
from agentic.core.process.application.presubmit import delivery_checks
from agentic.core.process.domain import presubmit
from agentic.core.process.domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR, prompt_hash
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from tests.fakes import (
    CommandResult,
    MemoryCommands,
    MemoryStack,
    ScriptedRunner,
    delivered,
    memory_ports,
)

pytestmark = pytest.mark.anyio

CHECKS = {"tests": "pytest", "lint": "ruff", "e2e": "playwright"}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def test_a_product_with_no_tiers_gets_every_declared_check():
    ports = memory_ports(stack=MemoryStack(checks=CHECKS))
    assert delivery_checks({"id": "GH-1"}, ports) == CHECKS


def test_a_tiered_product_gets_only_its_fast_checks_in_declared_order():
    ports = memory_ports(stack=MemoryStack(checks=CHECKS, delivery_checks=("lint", "tests")))
    assert delivery_checks({"id": "GH-1"}, ports) == {"tests": "pytest", "lint": "ruff"}


def sub(sid, status="approved"):
    return {
        "id": sid,
        "title": sid,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": status,
        "branch": f"agentic/GH-12.{sid}",
        "base": "base1",
    }


TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
    "subtasks": {"api": sub("api"), "domain": sub("domain")},
}


async def test_the_fan_in_stamps_the_task_head_for_a_tiered_product(cfg):
    ports = memory_ports(stack=MemoryStack(checks=CHECKS, delivery_checks=("tests",)))
    patch = await integrate(TASK, cfg, ports)
    assert patch["task_head"] == "base"
    assert ports.documents.last["task_head"] == "base"


async def test_a_resumed_pass_stamps_the_head_again_on_every_pass(cfg):
    ports = memory_ports(stack=MemoryStack(checks=CHECKS, delivery_checks=("tests",)))
    ports.workspace.moved.add(ports.workspace.task_checkout("GH-12").path)
    first = await integrate({**TASK, "task_head": "stale"}, cfg, ports)
    second = await integrate({**TASK, **first}, cfg, ports)
    assert first["task_head"] != "stale"
    assert second["task_head"] != first["task_head"]


async def test_a_product_with_no_tiers_writes_no_head(cfg):
    ports = memory_ports(stack=MemoryStack(checks=CHECKS))
    patch = await integrate(TASK, cfg, ports)
    assert "task_head" not in patch
    assert "task_head" not in ports.documents.last


async def test_an_escalated_fan_in_stamps_nothing(cfg):
    ports = memory_ports(stack=MemoryStack(checks=CHECKS, delivery_checks=("tests",)))
    patch = await integrate({**TASK, "escalated": "stuck"}, cfg, ports)
    assert "task_head" not in patch


# `develop`: the gate and the brief of a Developer under tiers.


AFFECTED = {"tests": "pytest --affected {base}", "e2e": "playwright --affected {base}"}


def building(ports, subtask_id="api"):
    state = {
        **TASK,
        "subtasks": {
            "api": {**sub("api", "pending"), "branch": "", "base": ""},
            "domain": {**sub("domain", "pending"), "branch": "", "base": ""},
        },
        "subtask_id": subtask_id,
    }
    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    ports.workspace.existing_branches["agentic/GH-12"] = ("head", "base0")
    return state, checkout


async def test_a_tiered_delivery_runs_only_the_fast_checks(cfg):
    runner = ScriptedRunner(developer=[delivered()])
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests", "lint"))
    ports = memory_ports(runner, stack=stack)
    state, checkout = building(ports)
    await develop(state, cfg, ports)
    assert ports.commands.ran == [(checkout.path, "pytest"), (checkout.path, "ruff")]


async def test_the_affected_forms_of_checks_outside_the_fast_tier_are_never_run(cfg):
    runner = ScriptedRunner(developer=[delivered()])
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests", "lint"), affected_checks=AFFECTED)
    ports = memory_ports(runner, stack=stack)
    state, _ = building(ports)
    ports.workspace.diff_paths = ("src/a.py",)
    await develop(state, cfg, ports)
    ran = [command for _, command in ports.commands.ran]
    assert not any("playwright" in command for command in ran)
    assert any("--affected" in command for command in ran)


async def test_the_developer_is_told_the_tiers_in_a_section_the_hash_covers(cfg):
    runner = ScriptedRunner(developer=[delivered()])
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests", "lint"))
    ports = memory_ports(runner, stack=stack)
    state, _ = building(ports)
    patch = await develop(state, cfg, ports)
    system = runner.calls[0]["system_prompt"]
    section = presubmit.tier_section(["tests", "lint"], ["e2e"])
    assert system.endswith("\n\n" + section)
    raw, _ = ports.prompts.raw(DEVELOPER)
    assert patch["prompts"][DEVELOPER.slug] == prompt_hash(raw + "\n" + section)
    assert "`e2e`" in section


async def test_the_integrator_is_told_the_tiers_the_same_way(cfg):
    runner = ScriptedRunner(developer_integrator=[delivered()])
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests",))
    ports = memory_ports(runner, stack=stack)
    state, _ = building(ports)
    state["subtasks"][INTEGRATION] = {**sub(INTEGRATION, "pending"), "branch": "", "base": ""}
    ports.workspace.moved.add(ports.workspace.task_checkout("GH-12").path)
    patch = await develop({**state, "subtask_id": INTEGRATION}, cfg, ports)
    section = presubmit.tier_section(["tests"], ["lint", "e2e"])
    assert runner.calls[0]["system_prompt"].endswith("\n\n" + section)
    raw, _ = ports.prompts.raw(DEVELOPER_INTEGRATOR)
    assert patch["prompts"][DEVELOPER_INTEGRATOR.slug] == prompt_hash(raw + "\n" + section)


async def test_the_delivery_record_names_the_tier_that_ran(cfg):
    runner = ScriptedRunner(developer=[delivered()])
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests",))
    ports = memory_ports(runner, stack=stack)
    state, _ = building(ports)
    patch = await develop(state, cfg, ports)
    section = patch["sections"]["Developer: api"]
    assert "Delivery tier: fast (`tests`)" in section


async def test_a_red_fast_check_still_refuses_the_delivery(cfg):
    red = [CommandResult(1, "1 failed")] * 6
    runner = ScriptedRunner(developer=[delivered()])
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests",))
    ports = memory_ports(runner, stack=stack, commands=MemoryCommands(pytest=red))
    state, _ = building(ports)
    await develop(state, cfg, ports)
    assert len(runner.refused) == 2 and "`tests` failed" in runner.refused[0]


async def test_a_product_with_no_tiers_has_todays_prompt_hash_record_and_checks(cfg):
    runner = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(runner, stack=MemoryStack(checks=CHECKS))
    state, checkout = building(ports)
    patch = await develop(state, cfg, ports)
    raw, _ = ports.prompts.raw(DEVELOPER)
    assert patch["prompts"][DEVELOPER.slug] == prompt_hash(raw)
    assert "Delivery checks" not in runner.calls[0]["system_prompt"]
    assert "Delivery tier" not in patch["sections"]["Developer: api"]
    assert ports.commands.ran == [(checkout.path, c) for c in CHECKS.values()]


# `run_check` (ADR-0065): offered to a Developer only where a pass can be stored and reused.


async def test_the_developer_gets_the_run_check_tool_when_the_system_has_a_fingerprint(cfg):
    from tests.fakes import MemoryCheckResults

    runner = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(
        runner, stack=MemoryStack(checks=CHECKS), check_results=MemoryCheckResults()
    )
    state, _ = building(ports)
    await develop(state, cfg, ports)
    assert runner.calls[0]["check_tool"] is not None


async def test_a_dirty_stage0_offers_no_tool_and_runs_exactly_todays_commands(cfg):
    from agentic.shared.stage0 import CHECKOUT
    from tests.fakes import MemoryCheckResults

    runner = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(
        runner, stack=MemoryStack(checks=CHECKS), check_results=MemoryCheckResults()
    )
    ports.workspace.uncommitted.add(CHECKOUT)  # stage0 is +dirty
    state, checkout = building(ports)
    await develop(state, cfg, ports)
    assert runner.calls[0]["check_tool"] is None
    assert ports.commands.ran == [
        (checkout.path, "pytest"),
        (checkout.path, "ruff"),
        (checkout.path, "playwright"),
    ]
