"""`deliver`'s fail-closed guard under tiers (GH-358): a product that declares
`delivery_checks` pushes only with a passed `full_checks` record whose head differs from HEAD
in nothing but the task's own document; a product that declares none is untouched."""

from __future__ import annotations

import pytest

from agentic.core.process.application.deliver import deliver
from agentic.core.process.domain import document
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack, memory_ports

pytestmark = pytest.mark.anyio

DOCUMENT = document.relative_path("GH-12").as_posix()
TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def tiered():
    return memory_ports(
        stack=MemoryStack(checks={"tests": "pytest", "lint": "ruff"}, delivery_checks=("tests",))
    )


def record(head="base", passed=True):
    return {
        "version": 1,
        "head": head,
        "passed": passed,
        "duration_s": 1.0,
        "declared": {"tests": "pytest", "lint": "ruff"},
        "results": [{"name": "tests", "passed": passed}],
        "witness": None,
    }


async def test_a_tiered_delivery_with_no_record_pushes_nothing_and_heals(cfg):
    ports = tiered()
    patch = await deliver(TASK, cfg, ports)
    assert "push" not in ports.workspace.events
    assert ports.tracker.pull_requests == []
    assert patch == {"task_head": "base", "full_checks_healed": "base"}
    assert ports.documents.saved


async def test_a_red_record_is_no_cover(cfg):
    ports = tiered()
    patch = await deliver({**TASK, "full_checks": record(passed=False)}, cfg, ports)
    assert "push" not in ports.workspace.events
    assert patch["full_checks_healed"] == "base"


async def test_a_stale_record_whose_code_differs_pushes_nothing(cfg):
    ports = tiered()
    ports.workspace.outside_differs = True
    patch = await deliver({**TASK, "full_checks": record(head="old")}, cfg, ports)
    assert "push" not in ports.workspace.events
    assert ports.tracker.pull_requests == []
    assert patch == {"task_head": "base", "full_checks_healed": "base"}
    [(_, verified, head, excluded)] = ports.workspace.outside_reads
    assert (verified, head, excluded) == ("old", "base", DOCUMENT)


async def test_a_record_whose_head_differs_only_in_the_task_document_pushes(cfg):
    ports = tiered()
    patch = await deliver({**TASK, "full_checks": record(head="old")}, cfg, ports)
    assert ports.workspace.events == ["push"]
    assert patch["status"] == "ready_for_review"
    assert ports.workspace.outside_reads[0][3] == DOCUMENT


async def test_a_record_of_this_very_head_pushes(cfg):
    ports = tiered()
    patch = await deliver({**TASK, "full_checks": record(head="base")}, cfg, ports)
    assert ports.workspace.events == ["push"]
    assert patch["status"] == "ready_for_review"


async def test_a_second_refusal_on_the_same_head_escalates_with_the_system_delivery_class(cfg):
    ports = tiered()
    patch = await deliver({**TASK, "full_checks_healed": "base"}, cfg, ports)
    assert "push" not in ports.workspace.events
    assert patch["escalation_class"] == "system_delivery"
    assert "base" in patch["escalated"]


async def test_a_product_with_no_tiers_reads_no_record_and_pushes_as_ever(cfg):
    ports = memory_ports()
    patch = await deliver(TASK, cfg, ports)
    assert ports.workspace.events == ["push"]
    assert ports.workspace.outside_reads == []
    assert "task_head" not in patch and "full_checks_healed" not in patch
