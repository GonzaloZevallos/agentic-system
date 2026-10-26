"""The wiring of the fast tier (GH-358): `delivery_checks` narrows the declared checks, the
fan-in stamps the task head for a tiered product only, and `develop` gates and briefs the
Developer on the fast tier alone, with today's prompt for a product that declares none."""

from __future__ import annotations

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.application.integrate import integrate
from agentic.core.process.application.presubmit import delivery_checks
from agentic.core.process.domain import presubmit
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack, memory_ports

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
