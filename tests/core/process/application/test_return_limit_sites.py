"""GH-593: the sites that keep escalating at their limit ask `rules.on_return_limit`, and none
compares the pair's returns with the limit on its own. Each node is called with the state it
would receive; the decision is forced to `escalate` with no return used, so only a node that asks
the decision escalates (a node that still compared the counter would count the return)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application.design import consolidate, design_review, ux_walk
from agentic.core.process.application.final_validation import final_validation
from agentic.core.process.application.ports import Ports
from agentic.core.process.application.validate_prd import validate_prd
from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import (
    PAIR_PM_PA,
    PAIR_PM_TECH_LEAD,
    PAIR_UI_LEAD,
    PAIR_UX_LEAD,
    Pair,
)
from agentic.core.process.domain.task import WAITING_ON_HUMAN, TaskState
from agentic.shared.configuration import Configuration
from tests.fakes import (
    ScriptedRunner,
    consolidation,
    design_findings,
    memory_ports,
    verdict,
)
from tests.fakes import final_validation as final_verdict

pytestmark = pytest.mark.anyio

TASK: TaskState = {
    "id": "GH-9",
    "title": "Export",
    "description": "CSV.",
    "type": "feature",
    "sections": {
        "PM": "### Consolidated definition\n\nx",
        "Product Analyst": "x",
        "UX Designer": "flow",
        "UI Designer": "screens",
    },
}

ALL_PAIRS = (PAIR_PM_PA, PAIR_PM_TECH_LEAD, PAIR_UX_LEAD, PAIR_UI_LEAD)
Node = Callable[[TaskState, Configuration, Ports], Coroutine[Any, Any, TaskState]]


def force(monkeypatch: pytest.MonkeyPatch, answer: str) -> list[Pair]:
    """Make the decision answer `answer` whatever the counter says; returns the pairs asked."""
    asked: list[Pair] = []

    def decide(state: TaskState, pair: Pair, limit: int, subtask_id: str | None = None) -> str:
        asked.append(pair)
        return answer

    monkeypatch.setattr(rules, "on_return_limit", decide)
    return asked


@pytest.fixture
def cfg(tmp_path: Path) -> Configuration:
    return Configuration(product_dir=tmp_path)


CASES = [
    pytest.param(validate_prd, "pm", verdict("returned", "gap"), PAIR_PM_PA, id="validate_prd"),
    pytest.param(ux_walk, "ux_lead", design_findings("returned", "gap"), None, id="ux_walk"),
    pytest.param(
        design_review, "ui_lead", design_findings("returned", "gap"), None, id="design_review"
    ),
    pytest.param(
        consolidate,
        "pm_consolidator",
        consolidation("returned", "gap"),
        PAIR_PM_PA,
        id="consolidate",
    ),
]


@pytest.mark.parametrize(("node", "slug", "reply", "pair"), CASES)
async def test_the_decision_escalates_a_return_the_counter_would_still_count(
    node: Node, slug: str, reply: Any, pair: Pair | None, cfg: Configuration, monkeypatch
):
    asked = force(monkeypatch, "escalate")
    patch = await node(TASK, cfg, memory_ports(ScriptedRunner(**{slug: [reply]})))
    assert patch["status"] == WAITING_ON_HUMAN
    assert patch["escalation_class"] == "returns"
    assert "changes_requested" not in patch
    assert asked and (pair is None or asked == [pair])


@pytest.mark.parametrize(("node", "slug", "reply", "pair"), CASES)
async def test_the_decision_counts_a_return_the_counter_would_already_escalate(
    node: Node, slug: str, reply: Any, pair: Pair | None, cfg: Configuration, monkeypatch
):
    force(monkeypatch, "count")
    spent = {**TASK, "changes_requested": {k.key: 2 for k in ALL_PAIRS}}
    patch = await node(spent, cfg, memory_ports(ScriptedRunner(**{slug: [reply]})))
    assert patch.get("escalation_class") is None
    assert "changes_requested" in patch


def single_subtask(**more: Any) -> TaskState:
    return {
        **TASK,
        "subtasks": {
            "s0": {
                "id": "s0",
                "title": "s0",
                "brief": "b",
                "seams": ["s"],
                "depends_on": [],
                "status": "approved",
                "reviewed_head": "a" * 40,
                **more,
            }
        },
    }


async def final(state: TaskState, cfg: Configuration) -> TaskState:
    runner = ScriptedRunner(
        ux_lead_final=[design_findings("approved")],
        ui_lead_final=[design_findings("approved")],
        pm_final=[final_verdict("returned", "too narrow", blocking=["still 1037px"])],
    )
    ports = memory_ports(runner)
    checkout = ports.workspace.task_checkout(state["id"])
    return await final_validation(state, cfg, ports, checkout=checkout)


async def test_final_validation_asks_the_decision_for_its_own_pair(cfg, monkeypatch):
    asked = force(monkeypatch, "escalate")
    patch = await final(single_subtask(), cfg)
    assert patch["status"] == WAITING_ON_HUMAN and patch["escalation_class"] == "returns"
    assert asked == [PAIR_PM_TECH_LEAD]


async def test_a_return_within_the_limit_is_counted_by_final_validation(cfg):
    patch = await final(single_subtask(), cfg)
    assert patch["changes_requested"] == {PAIR_PM_TECH_LEAD.key: 1}
    assert "escalated" not in patch


async def test_an_approved_subtask_returned_by_final_validation_escalates_and_never_restarts(cfg):
    spent = {**single_subtask(approved_once=True), "changes_requested": {PAIR_PM_TECH_LEAD.key: 2}}
    patch = await final(spent, cfg)
    assert patch["status"] == WAITING_ON_HUMAN and patch["escalation_class"] == "returns"
    assert "restarts" not in patch and "restart_pending" not in patch
    assert "changes_requested" not in patch
    assert patch["escalated"] == rules.limit_reached(PAIR_PM_TECH_LEAD, 2, "too narrow")
