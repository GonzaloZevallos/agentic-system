"""The early_size node (GH-999, ADR-0110): one patch per run, fail open, never an estimate."""

from __future__ import annotations

import pytest

from agentic.core.process.application.early_size import early_size
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import PM
from agentic.core.process.domain.task import TaskState, apply
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, early_sized, memory_ports, stepping_now

pytestmark = pytest.mark.anyio

QUOTE = "> As an owner I can export the monthly report as a CSV file."
PRD = f"## Jobs\n\n{QUOTE}\n"
OVER = {"hand_files": 100, "hand_lines": 5000, "test_lines": 100}
PIECES = [
    {"title": "First", "body": f"Export, #10.\n\n{QUOTE}", "depends_on": []},
    {"title": "Second", "body": f"Pick, #10.\n\n{QUOTE}", "depends_on": [1]},
]
FAILURE = AttemptFailed("run failed", 0.05)


def state_of(**more) -> TaskState:
    return {
        "id": "GH-10",
        "title": "Reports",
        "description": "Export reports.",
        "type": "feature",
        "status": "definition_ready",
        "sections": {PM.name: PRD},
        "cost_usd": 0.0,
        **more,
    }


async def run(state, *answers, cap=None, start="2026-11-06T12:00:00+00:00"):
    runner = ScriptedRunner(definition_sizer=list(answers))
    cfg = Configuration(product_dir="/p", **({} if cap is None else {"task_max_budget_usd": cap}))
    return await early_size(state, cfg, memory_ports(runner, now=stepping_now(start))), runner


async def test_over_budget_with_quoted_parts_writes_parts_and_the_split_row_together():
    patch, runner = await run(state_of(), early_sized(parts=PIECES, **OVER))
    assert [r["verdict"] for r in patch["early_split"]] == ["split"]
    assert patch["early_split"][0]["cause"] == "over"
    assert [p["title"] for p in patch["parts"]] == ["First", "Second"]
    assert not {"estimate", "planned_size", "subtasks", "sections"} & patch.keys()
    assert runner.slugs == ["definition_sizer"]


async def test_the_task_cap_is_the_escalation_with_no_row_and_no_parts():
    patch, _ = await run(state_of(cost_usd=10.0), early_sized(parts=PIECES, **OVER), cap=10.0)
    assert patch["escalation_class"] == "cap"
    assert "early_split" not in patch and "parts" not in patch


async def test_a_role_failure_gives_go_a_sizer_failed_row_and_keeps_what_it_cost():
    patch, _ = await run(state_of(), FAILURE, FAILURE)
    assert [(r["verdict"], r["cause"]) for r in patch["early_split"]] == [("go", "sizer_failed")]
    assert patch["failures"] and patch["cost_usd"] > 0
    assert not patch.get("escalated") and "parts" not in patch


async def test_a_second_failure_closes_the_measurement_after_one_retry():
    first, _ = await run(state_of(), FAILURE, FAILURE)
    once = apply(state_of(), first)
    assert not rules.early_measured(once)
    second, _ = await run(once, FAILURE, FAILURE, start="2026-11-07T12:00:00+00:00")
    twice = apply(once, second)
    assert len(twice["early_split"]) == 2  # a different `at`, so the row is not dropped
    assert rules.early_measured(twice)


@pytest.mark.parametrize(
    "body", ["Export, #10.\n\n> Invented prose.", f"Export, no number.\n\n{QUOTE}"]
)
async def test_refused_parts_give_go_with_the_cause_and_no_parts(body):
    parts = [{**PIECES[0], "body": body}, PIECES[1]]
    patch, _ = await run(state_of(), early_sized(parts=parts, **OVER))
    assert [(r["verdict"], r["cause"]) for r in patch["early_split"]] == [("go", "parts_refused")]
    assert "parts" not in patch


async def test_a_task_near_its_cap_with_no_estimate_still_gets_the_sizers_answer():
    patch, _ = await run(state_of(cost_usd=8.0), early_sized(), cap=10.0)
    assert patch["early_split"][0]["cause"] == "fits"
    assert "escalation_class" not in patch and "parts" not in patch
