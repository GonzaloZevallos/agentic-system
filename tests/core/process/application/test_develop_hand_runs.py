"""The wiring of the hand-run refusal in `develop` (ADR-0065 addendum 2026-11-25, GH-1070): the
red set the system holds, and the Bash closure built beside the `run_check` tool. These are not
at the three declared seams because the red set is built in `develop`, which is where only its
inputs (the gate's last rows, the open findings) can be given."""

from __future__ import annotations

from typing import Any

import pytest

from agentic.core.process.application.develop import develop
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.fakes import (
    CommandResult,
    MemoryCheckResults,
    MemoryCommands,
    MemoryStack,
    RawResult,
    ScriptedRunner,
    delivered,
    memory_ports,
)

pytestmark = pytest.mark.anyio

CHECKS = {"tests": "pytest", "lint": "ruff", "e2e": "playwright"}
AFFECTED = {"tests": "pytest --affected {base}"}


class Probe(ScriptedRunner):
    """A Developer that, before delivering, may hand the gate in once, then calls the tool and
    asks the Bash closure about the full command."""

    def __init__(self, gate_first: bool = False, **queues: list[RawResult | Exception]) -> None:
        super().__init__(**queues)
        self.gate_first = gate_first
        self.replies: dict[str, str] = {}
        self.bash: str | None = None

    async def run_once(self, role: Any, prompt: str, **kwargs: Any) -> RawResult:
        if self.gate_first and (presubmit := kwargs.get("presubmit")) is not None:
            presubmit()
        tool = kwargs["check_tool"]
        for name in ("tests", "lint", "e2e"):
            self.replies[name] = tool(name)
        self.bash = kwargs["refuse"]("pytest")
        return await super().run_once(role, prompt, **kwargs)


def finding(location: str, subtask: str = "other", status: str = "open") -> dict[str, Any]:
    return {
        "subtask": subtask,
        "version": 1,
        "location": location,
        "claim": "the full tier is red",
        "severity": "blocking",
        "scope": "subtask",
        "category": "correctness",
        "status": status,
    }


async def hand_runs(runner: Probe, *, findings=(), commands=None) -> tuple[Probe, MemoryCommands]:
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests", "lint"), affected_checks=AFFECTED)
    commands = commands or MemoryCommands()
    ports = memory_ports(runner, stack=stack, check_results=MemoryCheckResults(), commands=commands)
    state, _ = building(ports)
    await develop(
        {**state, "findings": list(findings)}, Configuration(product_dir=ports_dir()), ports
    )
    return runner, commands


def ports_dir():
    from pathlib import Path

    return Path("/memory/product")


def refused(reply: str) -> bool:
    return "run by the delivery gate" in reply


async def test_with_nothing_red_the_gate_suites_are_refused_in_the_tool_and_in_bash():
    runner, _ = await hand_runs(Probe(developer=[delivered()]))
    assert refused(runner.replies["tests"]) and "pytest --affected" in runner.replies["tests"]
    assert not refused(runner.replies["e2e"])
    assert runner.bash is not None and refused(runner.bash)


async def test_an_open_full_checks_row_of_another_subtask_or_of_none_opens_the_full_run():
    for owner in ("other", ""):
        row = finding("full_checks:tests", subtask=owner)
        runner, _ = await hand_runs(Probe(developer=[delivered()]), findings=[row])
        assert not refused(runner.replies["tests"]), owner
        assert runner.bash is None


async def test_a_dismissed_row_opens_nothing():
    row = finding("full_checks:tests", status="dismissed")
    runner, _ = await hand_runs(Probe(developer=[delivered()]), findings=[row])
    assert refused(runner.replies["tests"])


async def test_a_red_row_of_the_gates_last_look_opens_the_full_run_of_that_check_only():
    red = MemoryCommands(pytest=[CommandResult(1, "boom"), CommandResult(1, "boom")])
    runner, _ = await hand_runs(Probe(gate_first=True, developer=[delivered()]), commands=red)
    assert not refused(runner.replies["tests"])
    assert refused(runner.replies["lint"]) is False  # lint had no affected form and ran
