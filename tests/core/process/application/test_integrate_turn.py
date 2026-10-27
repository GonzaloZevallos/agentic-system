"""GH-364, subtask `sync-turn-git`: the system's turn between two moments of the technical
stage is a plain synchronous function, so the scheduling loop can run it in a worker thread
one turn at a time. `integrate` is its async wrapper and behaves exactly as before."""

from __future__ import annotations

import inspect

import pytest

from agentic.core.process.application.integrate import integrate, integrate_turn
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, memory_ports

pytestmark = pytest.mark.anyio

TASK = {"id": "GH-12", "title": "Dashboard", "description": "d", "type": "feature"}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def approved(sid: str) -> dict:
    return {
        "id": sid,
        "title": sid,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "approved",
        "branch": f"agentic/GH-12.{sid}",
        "base": "base1",
    }


def test_the_turn_is_synchronous_and_merges_what_was_approved(cfg):
    assert not inspect.iscoroutinefunction(integrate_turn)
    state = {**TASK, "subtasks": {"api": approved("api"), "domain": approved("domain")}}
    ports = memory_ports(ScriptedRunner())

    patch = integrate_turn(state, cfg, ports)

    assert [branch for _, branch in ports.workspace.merges] == [
        "agentic/GH-12.api",
        "agentic/GH-12.domain",
    ]
    assert patch["subtasks"][INTEGRATION]["status"] == "delivered"


async def test_integrate_answers_what_the_turn_answers(cfg):
    state = {**TASK, "subtasks": {"api": approved("api"), "domain": approved("domain")}}

    from_turn = integrate_turn(state, cfg, memory_ports(ScriptedRunner()))
    from_node = await integrate(state, cfg, memory_ports(ScriptedRunner()))

    assert from_node == from_turn


async def test_a_turn_with_the_task_escalated_touches_no_workspace(cfg):
    state = {**TASK, "escalated": "stop", "subtasks": {"api": approved("api")}}
    ports = memory_ports(ScriptedRunner())

    patch = integrate_turn(state, cfg, ports)

    assert patch["status"] == "waiting_on_human"
    assert ports.workspace.merges == []
    assert await integrate(state, cfg, ports) == patch
