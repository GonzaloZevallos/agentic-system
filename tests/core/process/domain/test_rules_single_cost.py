"""What a shadow review spent is out of every cap decision (GH-473, `review_mode` `single`):
`rules.cost_without_shadow` in the estimate cap, in `run_role`'s task cap and estimate stop and
in `landing_plan`'s release, while `task_spent` keeps the gross cost."""

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.application.ports import IssueComment, IssueState
from agentic.core.process.application.run_role import run_role
from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import PJM
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryTracker, ScriptedRunner, classified, memory_ports


def shadow(cost, subtask="s1"):
    return {
        "subtask": subtask,
        "head": "abc",
        "status": "ran",
        "cause": None,
        "findings": [],
        "cost_usd": cost,
        "changed_lines": 10,
    }


def with_estimate(**more):
    return {"id": "T-1", "estimate": {"cost_usd": 40.0, "files": 10}, **more}


def test_cost_without_shadow_subtracts_every_record():
    state = {"shadow_reviews": [shadow(3.0, "s1"), shadow(2.5, "s2")]}
    assert rules.cost_without_shadow(20.0, state) == pytest.approx(14.5)


def test_a_state_without_records_is_charged_its_whole_cost():
    assert rules.cost_without_shadow(20.0, {"id": "T-1"}) == 20.0
    assert rules.cost_without_shadow(20.0, {"shadow_reviews": []}) == 20.0


def test_the_cost_never_goes_below_zero():
    assert rules.cost_without_shadow(1.0, {"shadow_reviews": [shadow(5.0)]}) == 0.0


def test_the_estimate_cap_is_not_reached_when_the_excess_is_shadow_cost():
    plain = with_estimate()
    shadowed = with_estimate(shadow_reviews=[shadow(10.0)])
    assert rules.estimate_cap_reached(85.0, plain)
    assert not rules.estimate_cap_reached(85.0, shadowed)
    assert rules.estimate_cap_reached(90.0, shadowed)


def test_task_spent_keeps_the_gross_cost():
    state = {"cost_usd": 12.0, "landing_cost_carried": 3.0, "shadow_reviews": [shadow(5.0)]}
    assert rules.task_spent(state) == 15.0


PM_STATE = {"id": "T-1", "title": "t", "description": "d"}


async def run_pjm(tmp_path, state, **cfg_more):
    cfg = Configuration(product_dir=tmp_path, **cfg_more)
    runner = ScriptedRunner(pjm=[classified(cost=0.03)])
    ports = memory_ports(runner)
    output, patch = await run_role({**PM_STATE, **state}, PJM, cfg, ports, product="x")
    return output, patch, runner


@pytest.mark.anyio
async def test_run_role_task_cap_ignores_what_a_shadow_spent(tmp_path):
    state = {"cost_usd": 55.0, "shadow_reviews": [shadow(10.0)]}
    output, _, runner = await run_pjm(tmp_path, state, task_max_budget_usd=50.0)
    assert output is not None and len(runner.calls) == 1


@pytest.mark.anyio
async def test_run_role_task_cap_still_stops_on_the_cost_that_is_not_shadow(tmp_path):
    state = {"cost_usd": 55.0, "shadow_reviews": [shadow(4.0)]}
    output, patch, runner = await run_pjm(tmp_path, state, task_max_budget_usd=50.0)
    assert output is None and runner.calls == []
    assert patch["escalation_class"] == "cap"
    assert patch["escalated"] == "PjM stopped by the task cap: 51.00 of 50.00 USD spent"


@pytest.mark.anyio
async def test_run_role_estimate_stop_ignores_what_a_shadow_spent(tmp_path):
    state = with_estimate(cost_usd=85.0, shadow_reviews=[shadow(10.0)])
    output, _, runner = await run_pjm(tmp_path, state, task_budget_usd=100_000.0)
    assert output is not None and len(runner.calls) == 1


@pytest.mark.anyio
async def test_run_role_estimate_stop_reports_the_counted_cost(tmp_path):
    state = with_estimate(cost_usd=95.0, shadow_reviews=[shadow(10.0)])
    output, patch, runner = await run_pjm(tmp_path, state, task_budget_usd=100_000.0)
    assert output is None and runner.calls == []
    assert patch["escalation_class"] == "estimate"
    assert patch["escalated"].startswith("PjM stopped by the estimate cap: 85.00 of 80.00 USD")


ESCALATED_AT = "2026-10-23T06:05:00+00:00"
ANSWERED_AT = "2026-10-23T06:47:00+00:00"


def release(raw, cfg):
    ports = memory_ports(tracker=MemoryTracker())
    ports.tracker.timestamped["GH-5"] = [
        IssueComment("Escalated: x", ESCALATED_AT),
        IssueComment("merge master in", ANSWERED_AT),
    ]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None, ())
    return landing_plan.release(ports, cfg, "GH-5", raw, 5)


def escalated(**over):
    raw = {
        "id": "GH-5",
        "status": "waiting_on_human",
        "escalated": "Landing Diagnoser did not deliver after 1 attempt: max_turns",
        "escalation_class": "failure",
        "pull_request": "https://github.com/o/r/pull/5",
        "landing": [
            {
                "head": "abc",
                "step": "diagnose",
                "refusal": "merge_failed",
                "at": "2026-10-23T06:00:00+00:00",
                "failure": "max_turns",
            }
        ],
    }
    return {**raw, **over}


def test_landing_release_ignores_what_a_shadow_spent():
    cfg = Configuration(product_dir="/p", task_max_budget_usd=10.0)
    over = escalated(cost_usd=12.0, shadow_reviews=[shadow(5.0)])
    assert release(over, cfg).why == "released"
    still_over = escalated(cost_usd=12.0, shadow_reviews=[shadow(1.0)])
    assert release(still_over, cfg).why == "task_cap"
