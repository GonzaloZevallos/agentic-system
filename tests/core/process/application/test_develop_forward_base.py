"""`develop` builds the Developer's delivery gate with the forward affected base on (GH-1125) and
the `run_check` presubmit without it: the gate moves G, the tool never reads or writes it."""

import pytest

from agentic.core.process.application.develop import develop
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.fakes import MemoryCheckResults, MemoryStack, ScriptedRunner, delivered, memory_ports

pytestmark = pytest.mark.anyio


async def test_the_delivery_gate_advances_its_base_and_the_run_check_gate_does_not(tmp_path):
    runner = ScriptedRunner(developer=[delivered()])
    ports = memory_ports(
        runner,
        stack=MemoryStack(checks={"tests": "pytest"}, affected_checks={"tests": "pytest {base}"}),
        check_results=MemoryCheckResults(),
    )
    state, _ = building(ports)
    await develop(state, Configuration(product_dir=tmp_path), ports)
    call = runner.calls[0]
    gate, tool = call["presubmit"], call["check_tool"]
    assert gate.advance_base is True
    assert gate.green_heads == {"tests": gate.head}
    assert tool is not None
    assert tool.gate.advance_base is False and tool.gate.green_heads == {}
