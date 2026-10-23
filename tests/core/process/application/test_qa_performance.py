"""QA Performance's node (GH-318, ADR-0060) over the fake role runner, a fake `PerfRunner`, a
fake `PerfLock` and the in-memory workspace and stack: the verdict, the reopen, the rows the
system builds, the settling writer and the guarantee that nothing runs in a task checkout."""

from __future__ import annotations

import pytest

from agentic.core.process.application.perf import PerfInterrupted
from agentic.core.process.application.qa_performance import qa_performance
from agentic.core.process.application.review import review
from agentic.core.process.domain import roles, rules
from agentic.core.process.domain.performance import Limit, Measurement, Run, Scenario
from agentic.core.process.domain.task import INTEGRATION, apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    FakePerfLock,
    FakePerfRunner,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    finding,
    memory_ports,
    reviewed,
)

pytestmark = pytest.mark.anyio

PLAN = {
    "name": "list",
    "volume": 1000,
    "volume_unit": "rows",
    "basis": "b",
    "budgets": [{"metric": "list_ms", "limit": 100, "unit": "ms", "source": "issue", "basis": "b"}],
}
CFG = Configuration(product_dir=MemoryWorkspace.ROOT)


def declared(limit: float = 100) -> dict[str, Scenario]:
    limits = {"list_ms": Limit(limit, "ms")}
    return {"list": Scenario("list", "seed", "measure", 1, "rows", limits)}


def state_of(**more) -> dict:
    subtask = {
        "id": INTEGRATION,
        "title": "integration",
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "approved",
        "branch": "agentic/GH-1",
        "base": "b",
    }
    return {
        "id": "GH-1",
        "title": "Perf",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "performance": [PLAN],
        "subtasks": {INTEGRATION: subtask},
        "subtask_id": INTEGRATION,
        "runs": [{"role": "Tech Lead", "section": rules.review_section(INTEGRATION), "version": 1}],
        **more,
    }


def measured(value: float) -> list[Run]:
    run = Run(Measurement(1000, "rows", {"list_ms": value}), 0, False, 0.5, 0.6)
    return [Run(None, 0, False, 0.5, 0.5), *[run] * 5]


def approving() -> object:
    return ScriptedRunner(qa_performance=[reviewed("approved")])


async def go(state, runner_script, role=None, stack=None, lock=None, workspace=None):
    perf = FakePerfRunner(runner_script)
    lock = lock or FakePerfLock()
    role = role or ScriptedRunner(qa_performance=[reviewed("approved")])
    ports = memory_ports(
        role,
        stack=stack or MemoryStack(performance=declared()),
        workspace=workspace,
        perf_runner=perf,
        perf_lock=lock,
    )
    patch = await qa_performance(state, CFG, ports)
    return apply(state, patch), perf, lock, role, ports


async def test_every_budget_within_is_approved_on_the_measured_head_and_runs_before_the_role():
    after, perf, lock, role, ports = await go(state_of(), measured(50))
    run = after["runs"][-1]
    assert run["perf_verdict"] == "approved" and run["perf_head"]
    assert "| list | list_ms | 50 | 100 | ms | within |" in after["sections"]["QA Performance"]
    assert len(perf.calls) == 6 and lock.released == 1
    assert role.slugs == ["qa_performance"]
    assert not rules.qa_performance_due(after)


async def test_every_measurement_call_comes_before_the_role_runner_is_called():
    class Watching(ScriptedRunner):
        seen: list[int] = []

        async def run_once(self, role, prompt, **kwargs):
            self.seen.append(len(perf.calls))
            return await super().run_once(role, prompt, **kwargs)

    perf = FakePerfRunner(measured(50))
    role = Watching(qa_performance=[reviewed("approved")])
    role.seen = []
    ports = memory_ports(
        role, stack=MemoryStack(performance=declared()), perf_runner=perf, perf_lock=FakePerfLock()
    )
    await qa_performance(state_of(), CFG, ports)
    assert role.seen == [6]


async def test_no_command_runs_in_the_task_checkout():
    after, perf, _, _, ports = await go(state_of(), measured(50))
    task = ports.workspace.task_checkout("GH-1").path
    assert perf.calls and all(call[0] != task for call in perf.calls)
    assert all(call[0].name.startswith("perf-") for call in perf.calls)


async def test_an_over_budget_returns_with_a_stable_claim_and_reopens_the_integration():
    after, *_ = await go(state_of(), measured(150))
    assert after["runs"][-1]["perf_verdict"] == "returned"
    unit = after["subtasks"][INTEGRATION]
    assert (unit["status"], unit["returned_by"]) == ("returned", "qa_performance")
    (row,) = after["findings"]
    assert row["claim"] == "list/list_ms over 100ms"
    assert (row["location"], row["scope"], row["status"]) == ("QA Performance", "plan", "open")
    assert (row["category"], row["severity"]) == ("standard-other", "blocking")
    assert after["changes_requested"][roles.PAIR_QA_PERFORMANCE_TECH_LEAD.key] == 1


async def test_the_same_row_keeps_its_key_across_a_second_return_and_an_approval_settles_it():
    first, *_ = await go(state_of(), measured(150))
    second, *_ = await go(first, measured(160))
    assert len(rules.open_findings(second)) == 1
    third, *_ = await go(second, measured(40))
    assert third["runs"][-1]["perf_verdict"] == "approved"
    assert rules.open_findings(third) == []
    again, *_ = await go(third, measured(150))
    assert [r["claim"] for r in rules.open_findings(again)] == ["list/list_ms over 100ms"]


async def test_a_plan_scenario_missing_at_the_head_returns_as_spec_scope_and_measures_nothing():
    after, perf, lock, *_ = await go(state_of(), [], stack=MemoryStack(performance={}))
    (row,) = after["findings"]
    assert (row["claim"], row["category"]) == ("list missing at head", "spec-scope")
    assert after["runs"][-1]["perf_verdict"] == "returned"
    assert perf.calls == [] and lock.acquired == []


async def test_declared_limits_that_differ_from_the_plan_are_a_mismatch_fact():
    after, perf, *_ = await go(state_of(), [], stack=MemoryStack(performance=declared(500)))
    (row,) = after["findings"]
    assert (row["claim"], row["category"]) == ("list limits mismatch", "spec-scope")
    assert perf.calls == []


async def test_limits_loosened_from_the_base_are_a_standard_other_fact():
    class Moving(MemoryStack):
        def performance(self, revision):
            return declared(100) if revision.startswith("head") else declared(50)

    class Head(MemoryWorkspace):
        def full_head(self, path):
            return "head" + "0" * 36

    after, *_ = await go(
        state_of(), measured(40), stack=Moving(), workspace=Head(), role=approving()
    )
    (row,) = after["findings"]
    assert (row["claim"], row["category"]) == ("list limits loosened", "standard-other")


async def test_only_inconclusive_is_unverified_with_no_blocking_row_and_counts_as_done():
    wide = [Run(None, 0, False, 0, 0)] + [
        Run(Measurement(1000, "rows", {"list_ms": v}), 0, False, 0, 0) for v in (1, 90, 40, 70, 20)
    ] * 2
    after, *_ = await go(state_of(), wide)
    assert after["runs"][-1]["perf_verdict"] == "unverified"
    assert after.get("findings", []) == [] and not after["subtasks"][INTEGRATION].get("returned_by")


async def test_busy_on_both_tries_is_unverified_with_reason_busy():
    lock = FakePerfLock(available=False)
    after, perf, lock, *_ = await go(state_of(), [], lock=lock)
    assert lock.acquired == [CFG.perf_lock_wait_s] * 2 and perf.calls == []
    assert after["runs"][-1]["perf_verdict"] == "unverified"
    assert "| inconclusive | busy |" in after["sections"]["QA Performance"]


async def test_one_failed_try_then_success_measures():
    class Flaky(FakePerfLock):
        def acquire(self, wait_s):
            super().acquire(wait_s)
            return len(self.acquired) > 1

    after, perf, lock, *_ = await go(state_of(), measured(50), lock=Flaky())
    assert len(lock.acquired) == 2 and after["runs"][-1]["perf_verdict"] == "approved"


async def test_an_interrupt_leaves_no_table_no_run_no_lock_and_the_node_still_due():
    class Interrupted(FakePerfRunner):
        def run(self, checkout, command, extra_env, timeout_s):
            raise PerfInterrupted(15)

    state = state_of()
    role, lock = approving(), FakePerfLock()
    ports = memory_ports(
        role,
        stack=MemoryStack(performance=declared()),
        perf_runner=Interrupted(),
        perf_lock=lock,
    )
    assert rules.qa_performance_due(state)
    with pytest.raises(PerfInterrupted):
        await qa_performance(state, CFG, ports)
    assert role.calls == [] and ports.documents.saved == []
    assert lock.released == 1
    (made,) = ports.workspace.detached
    assert ports.workspace.removed_checkouts[-1] == made
    assert rules.qa_performance_due(state)


async def test_a_blocking_finding_of_the_role_returns_even_when_every_budget_is_within():
    role = ScriptedRunner(
        qa_performance=[
            reviewed(
                "returned", findings=[finding(claim="N+1 in list.py:10", category="standard-other")]
            )
        ]
    )
    after, *_ = await go(state_of(), measured(50), role=role)
    assert after["runs"][-1]["perf_verdict"] == "returned"
    assert [r["claim"] for r in after["findings"]] == ["N+1 in list.py:10"]


async def test_the_pair_limit_escalates_with_the_table_and_the_close_the_pr_sentence():
    state = state_of(changes_requested={roles.PAIR_QA_PERFORMANCE_TECH_LEAD.key: 2})
    after, *_ = await go(state, measured(150))
    assert "re-filing the issue" in after["escalated"] and "| list_ms |" in after["escalated"]


def reviewable(convened: bool, section: bool = True) -> dict:
    state = state_of(performance=[PLAN] if convened else [])
    state["subtasks"][INTEGRATION]["status"] = "delivered"
    state["sections"]["QA Performance"] = "Measured head: x\n\n| UNIQUE-TABLE |" if section else ""
    if not section:
        del state["sections"]["QA Performance"]
    return state


async def reviewer_input(state):
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    await review(state, CFG, memory_ports(runner))
    (call,) = runner.calls
    return call["prompt"], call["role"].allowed


async def test_a_convened_integration_review_reads_the_latest_table_and_gains_no_tool():
    with_table, tools = await reviewer_input(reviewable(True))
    without, base_tools = await reviewer_input(reviewable(True, section=False))
    assert "UNIQUE-TABLE" in with_table and "UNIQUE-TABLE" not in without
    assert tools == base_tools and not any("perf" in tool for tool in tools)


async def test_a_task_that_is_not_convened_reads_the_same_review_input_whatever_the_section():
    stale, tools = await reviewer_input(reviewable(False))
    clean, base_tools = await reviewer_input(reviewable(False, section=False))
    assert "UNIQUE-TABLE" not in stale
    assert stale == clean and tools == base_tools
