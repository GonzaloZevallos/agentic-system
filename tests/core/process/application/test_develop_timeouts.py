"""The Developer's patch for a delivery whose gate saw a check time out with no failing test
(GH-1075): `RoleRun.timeouts` is set (capped) and the unverified line is in the section; a later
green call of the same run leaves the key absent."""

from __future__ import annotations

from typing import Any

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.application.ports import CommandResult
from agentic.core.process.domain.task import ROLE_TIMEOUTS_MAX
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.fakes import (
    MemoryCommands,
    MemoryStack,
    RawResult,
    ScriptedRunner,
    delivered,
    memory_ports,
)

pytestmark = pytest.mark.anyio

TIMED_OUT = CommandResult(-9, "partial", timed_out=True)


class AskingTwice(ScriptedRunner):
    """A runner that asks the gate again after its first look, as a retry does."""

    async def run_once(self, role: Any, prompt: str, **kwargs: Any) -> RawResult:
        if (presubmit := kwargs.get("presubmit")) is not None:
            presubmit()
        return await super().run_once(role, prompt, **kwargs)


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def patch_of(runner, cfg, checks, commands):
    ports = memory_ports(runner, stack=MemoryStack(checks=checks), commands=commands)
    state, _ = building(ports)
    return await develop(state, cfg, ports)


async def test_a_timeout_sets_the_runs_timeouts_and_the_line_in_the_section(cfg):
    commands = MemoryCommands(pytest=[TIMED_OUT])
    patch = await patch_of(
        ScriptedRunner(developer=[delivered()]), cfg, {"tests": "pytest"}, commands
    )
    (fact,) = patch["runs"][-1]["timeouts"]
    assert fact["check"] == "tests"
    assert "tests timed out after" in patch["sections"]["Developer: api"]


async def test_the_timeouts_are_capped(cfg):
    checks = {f"c{i}": f"cmd{i}" for i in range(ROLE_TIMEOUTS_MAX + 5)}
    commands = MemoryCommands(**{command: [TIMED_OUT] for command in checks.values()})
    patch = await patch_of(ScriptedRunner(developer=[delivered()]), cfg, checks, commands)
    assert len(patch["runs"][-1]["timeouts"]) == ROLE_TIMEOUTS_MAX


async def test_a_timeout_then_a_green_call_leaves_the_key_absent(cfg):
    commands = MemoryCommands(pytest=[TIMED_OUT, CommandResult(0, "ok")])
    runner = AskingTwice(developer=[delivered()])
    patch = await patch_of(runner, cfg, {"tests": "pytest"}, commands)
    assert "timeouts" not in patch["runs"][-1]
    assert "timed out after" not in patch["sections"]["Developer: api"]
