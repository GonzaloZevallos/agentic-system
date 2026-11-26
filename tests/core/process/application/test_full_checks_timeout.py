"""The `full_checks` node on a suite that timed out with no failing test (GH-1075), over port
fakes: it escalates (class `ci`, cause `check_timeout`) and writes no record and no return; a row
a `ci_covers` deferral covers keeps its path of before."""

from __future__ import annotations

import pytest

from agentic.core.process.application.checkouts import task_base, task_checkout
from agentic.core.process.application.full_checks import _local_full_checks
from agentic.core.process.application.ports import CommandResult
from agentic.core.process.domain.presubmit import CiCover
from agentic.core.process.domain.task import INTEGRATION, apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryCommands,
    MemoryStack,
    MemoryWorkspace,
    full_checks_in_task_checkout,
    memory_ports,
)

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT, check_timeout_s=100.0, ci_start_grace_s=30.0)
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
TIMED_OUT = CommandResult(-9, "partial output", timed_out=True)
COVER = CiCover("e2e", r"✘")
WEB_OUT = "  ✓ 216 [mobile] works\n  ✓ 217 [mobile] last\n"
CHECKS_WEB = {"unit": "pytest", "web-e2e": "playwright test"}


def state_of(**more) -> dict:
    subtask = {
        "id": INTEGRATION,
        "title": "integration",
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "approved",
        "branch": "agentic/GH-1",
        "base": "b",
    }
    return {
        "id": "GH-1",
        "title": "Tiers",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtasks": {INTEGRATION: subtask},
        "task_head": "base",
        **more,
    }


def local_ports(commands: MemoryCommands):
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests",))
    return memory_ports(stack=stack, commands=commands)


async def test_a_timed_out_check_escalates_with_class_ci_and_cause_check_timeout():
    ports = local_ports(MemoryCommands(pytest=[TIMED_OUT]))
    patch = await full_checks_in_task_checkout(state_of(), CFG, ports)
    assert "full_checks" not in patch
    assert patch["escalation_class"] == "ci"
    entry = patch["pause_log"][-1]
    assert entry["kind"] == "escalation" and entry["cause"] == "check_timeout"
    assert [fact["check"] for fact in entry["timeouts"]] == ["tests"]
    assert entry["previous_timeouts"] == []


async def test_a_timeout_writes_no_record_no_return_and_one_observation_row():
    ports = local_ports(MemoryCommands(pytest=[TIMED_OUT]))
    state = state_of()
    patch = await full_checks_in_task_checkout(state, CFG, ports)
    assert "full_checks" not in patch and "change_history" not in patch
    assert [(r["category"], r["severity"], r["location"]) for r in patch["findings"]] == [
        ("process-check-timeout", "observation", "checks:tests")
    ]
    after = apply(state, patch)
    assert after["subtasks"][INTEGRATION]["status"] == "approved"  # no return reopened it


async def test_the_section_says_the_timeout_line_never_a_pass():
    ports = local_ports(MemoryCommands(pytest=[TIMED_OUT]))
    patch = await full_checks_in_task_checkout(state_of(), CFG, ports)
    text = patch["sections"]["Full checks"]
    assert "tests timed out after" in text and "unverified" in text
    assert "Passed" not in text


async def test_a_real_red_beside_a_timeout_is_still_a_return():
    commands = MemoryCommands(
        pytest=[TIMED_OUT], **{"ruff check .": [CommandResult(1, "E501")] * 2}
    )
    patch = await full_checks_in_task_checkout(state_of(), CFG, local_ports(commands))
    assert patch["full_checks"]["passed"] is False
    assert not any(e.get("cause") == "check_timeout" for e in patch.get("pause_log", []))


async def test_a_second_timeout_names_the_first_as_previous():
    ports = local_ports(MemoryCommands(pytest=[TIMED_OUT]))
    first = await full_checks_in_task_checkout(state_of(), CFG, ports)
    ports = local_ports(MemoryCommands(pytest=[TIMED_OUT]))
    second = await full_checks_in_task_checkout(apply(state_of(), first), CFG, ports)
    entry = second["pause_log"][-1]
    assert [f["check"] for f in entry["previous_timeouts"]] == ["tests"]


def covered_ports():
    stack = MemoryStack(checks=CHECKS_WEB, delivery_checks=("unit",), ci=("unit", "web"))
    stack.declared_ci_covers = {"web-e2e": COVER}
    commands = MemoryCommands(**{"playwright test": [CommandResult(-9, WEB_OUT, timed_out=True)]})
    ports = memory_ports(stack=stack, commands=commands)
    return ports


async def test_a_timeout_covered_by_ci_is_deferred_as_before_and_never_escalates():
    # Through the private entry: the node never reaches a deferrable fallback with a CI
    # that gave no verdict (it escalates first, GH-462), so the node cannot show the deferral.
    ports, state = covered_ports(), state_of()
    patch = await _local_full_checks(
        state, CFG, ports, task_checkout(state, CFG, ports), task_base(state, ports), "no_run"
    )
    record = patch["full_checks"]
    assert record["ci_fallback"] == "no_run" and record["deferred"] == ["web-e2e"]
    assert not any(e.get("cause") == "check_timeout" for e in patch.get("pause_log", []))
    assert "deferred to CI job `e2e`" in patch["sections"]["Full checks"]
