"""The Developer's section carries the system's line for each verification the Developer ran
(ADR-0065 addendum, GH-451), written where the checks' results are, whatever the summary says."""

from __future__ import annotations

from typing import Any

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.domain.run_check import Verification
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import CHECKS, building
from tests.fakes import (
    MemoryCheckResults,
    MemoryStack,
    RawResult,
    ScriptedRunner,
    delivered,
    memory_ports,
)

pytestmark = pytest.mark.anyio

COMPARE = Verification("compare.sh --strict", 900.0)


class CallingRunner(ScriptedRunner):
    """A runner whose Developer calls `run_check` on the names it was given before it delivers."""

    def __init__(self, names: tuple[str, ...], **queues: list[RawResult | Exception]) -> None:
        super().__init__(**queues)
        self.names = names

    async def run_once(self, role: Any, prompt: str, **kwargs: Any) -> RawResult:
        if (tool := kwargs.get("check_tool")) is not None:
            for name in self.names:
                tool(name)
        return await super().run_once(role, prompt, **kwargs)


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def section_of(runner: ScriptedRunner, cfg, *, verifications=None) -> Any:
    stack = MemoryStack(checks=CHECKS, verifications=verifications or {})
    return memory_ports(runner, stack=stack, check_results=MemoryCheckResults())


async def run(runner: ScriptedRunner, cfg, verifications) -> str:
    ports = section_of(runner, cfg, verifications=verifications)
    state, _ = building(ports)
    patch = await develop(state, cfg, ports)
    return patch["sections"]["Developer: api"]


async def test_the_section_has_the_line_though_the_summary_never_mentions_it(cfg):
    runner = CallingRunner(("compare",), developer=[delivered(summary="all good")])
    text = await run(runner, cfg, {"compare": COMPARE})
    assert "### Verifications" in text and "`compare`: outcome `passed`" in text
    assert "compare" not in "all good"


async def test_a_name_that_is_not_declared_leaves_a_not_declared_line(cfg):
    runner = CallingRunner(("mobile-matrix",), developer=[delivered()])
    text = await run(runner, cfg, {"compare": COMPARE})
    assert "`mobile-matrix`: outcome `not_declared`" in text


async def test_with_no_call_on_a_verification_the_section_is_todays(cfg):
    declared = await run(CallingRunner((), developer=[delivered()]), cfg, {"compare": COMPARE})
    none_declared = await run(CallingRunner((), developer=[delivered()]), cfg, {})
    called_a_check = await run(
        CallingRunner(("tests",), developer=[delivered()]), cfg, {"compare": COMPARE}
    )
    assert all("Verifications" not in text for text in (declared, none_declared, called_a_check))
    assert declared.count("### ") == none_declared.count("### ") == called_a_check.count("### ")
