"""A convened task end to end (GH-318, ADR-0060), driven the way the graph drives it: the
fan-in's own routing (`rules.after_integrate`) picks the next node, each node is the real use
case, and the only fakes are the role runner and the `PerfRunner` (plus its lock, the
in-memory workspace and the stack that declares the scenario).

The fixture: the integrated piece is approved, QA Performance measures it over budget and
returns it, the Developer fixes it, the review approves again, QA Performance re-measures
within budget and approves, `deliver` is next, and the merge gate then lets the PR through on
the measured head -- and only on that head. A plan that convened nothing takes the old path."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.application.plan import plan
from agentic.core.process.application.qa_performance import qa_performance
from agentic.core.process.application.review import review
from agentic.core.process.domain import document, rules
from agentic.core.process.domain.performance import Limit, Measurement, Run, Scenario
from agentic.core.process.domain.rules import MergeFacts
from agentic.core.process.domain.task import INTEGRATION, TaskState, apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    FakePerfLock,
    FakePerfRunner,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    planned,
    memory_ports,
    reviewed,
)

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT)
HEAD = "a" * 40
MASTER = "b" * 40
PLAN = {
    "name": "list",
    "volume": 1000,
    "volume_unit": "rows",
    "basis": "the largest customer",
    "budgets": [
        {"metric": "list_ms", "limit": 100, "unit": "ms", "source": "issue", "basis": "issue"}
    ],
}


class Pinned(MemoryWorkspace):
    """A workspace whose full head is a real 40-hex sha, the form the merge gate requires."""

    def full_head(self, path: Path) -> str:
        return HEAD


def declared() -> dict[str, Scenario]:
    return {"list": Scenario("list", "seed", "measure", 1, "rows", {"list_ms": Limit(100, "ms")})}


def measured(value: float) -> list[Run]:
    """The warm-up and five measuring runs one scenario costs, all printing `value`."""
    run = Run(Measurement(1000, "rows", {"list_ms": value}), 0, False, 0.5, 0.6)
    return [Run(None, 0, False, 0.5, 0.5), *[run] * 5]


def integrated(performance: list[dict]) -> TaskState:
    """The integration approved by its review: what the fan-in hands to `after_integrate`."""
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
        "performance": performance,
        "subtasks": {INTEGRATION: subtask},
        "subtask_id": INTEGRATION,
        "runs": [{"role": "Tech Lead", "section": rules.review_section(INTEGRATION), "version": 1}],
    }


async def drive(
    state: TaskState, role: ScriptedRunner, perf: FakePerfRunner
) -> tuple[list[str], TaskState]:
    """Follow `after_integrate` until it names `deliver`, running each node it names."""
    route: list[str] = []
    workspace = Pinned()
    workspace.moved.add(MemoryWorkspace.ROOT / "GH-1")  # the Developer commits
    for _ in range(12):
        step = rules.after_integrate(state)
        assert step is not None
        route.append(step)
        if step == "deliver":
            return route, state
        ports = memory_ports(
            role,
            stack=MemoryStack(performance=declared()),
            workspace=workspace,
            perf_runner=perf,
            perf_lock=FakePerfLock(),
        )
        if step == "qa_performance":
            state = apply(state, await qa_performance(state, CFG, ports))
            assert rules.after_qa_performance(state) == "integrate"
        elif step == "pipeline":
            state = apply(state, await develop(state, CFG, ports))
            state = apply(state, await review(state, CFG, ports))
        else:
            raise AssertionError(step)
    raise AssertionError(route)


def merge_facts(state: TaskState, pr_head: str) -> MergeFacts:
    """What the poller and the landing build for a PR whose reviewed head is its own head."""
    convened, verdict, head = rules.perf_merge_facts(state)
    return MergeFacts(
        task_id="GH-1",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=pr_head,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=pr_head,
        in_eval_set=False,
        touched_paths=("agentic/x.py",),
        attempts=1,
        checks_head=pr_head,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
        perf_convened=convened,
        perf_verdict=verdict,
        perf_head=head,
    )


async def test_a_convened_task_goes_over_budget_is_fixed_and_approved_then_delivers():
    role = ScriptedRunner(
        qa_performance=[reviewed("approved"), reviewed("approved")],
        developer_integrator=[delivered("fixed the index")],
        tech_lead_review=[reviewed("approved")],
    )
    perf = FakePerfRunner([*measured(150), *measured(40)])

    route, final = await drive(integrated([PLAN]), role, perf)

    assert route == ["qa_performance", "pipeline", "qa_performance", "deliver"]
    assert role.slugs == [
        "qa_performance",
        "developer_integrator",
        "tech_lead_review",
        "qa_performance",
    ]
    verdicts = [r["perf_verdict"] for r in final["runs"] if "perf_verdict" in r]
    assert verdicts == ["returned", "approved"]
    assert rules.open_findings(final) == []  # the approval settled the row the return opened
    assert "| list | list_ms | 40 | 100 | ms | within |" in final["sections"]["QA Performance"]

    assert final["runs"][-1]["perf_head"] == HEAD
    assert rules.auto_merge_decision(merge_facts(final, HEAD)).kind == "merge"


async def test_the_gate_refuses_a_head_other_than_the_one_qa_performance_measured():
    role = ScriptedRunner(qa_performance=[reviewed("approved")])
    route, final = await drive(integrated([PLAN]), role, FakePerfRunner(measured(40)))
    assert route == ["qa_performance", "deliver"]
    moved = rules.auto_merge_decision(merge_facts(final, "c" * 40))  # reviewed, but not measured
    assert (moved.kind, moved.reason) == ("refuse", "perf_unverified")


async def test_an_unfixed_return_leaves_the_pull_request_to_the_human():
    role = ScriptedRunner(qa_performance=[reviewed("approved")])
    state = integrated([PLAN])
    ports = memory_ports(
        role,
        stack=MemoryStack(performance=declared()),
        workspace=Pinned(),
        perf_runner=FakePerfRunner(measured(150)),
        perf_lock=FakePerfLock(),
    )
    returned = apply(state, await qa_performance(state, CFG, ports))
    decision = rules.auto_merge_decision(merge_facts(returned, HEAD))
    assert (decision.kind, decision.reason) == ("refuse", "perf_unverified")


async def test_a_task_that_convened_nothing_takes_the_old_path_and_measures_nothing():
    role = ScriptedRunner()
    perf = FakePerfRunner()
    route, final = await drive(integrated([]), role, perf)
    assert route == ["deliver"]
    assert role.slugs == [] and perf.calls == []
    assert rules.perf_merge_facts(final) == (False, None, None)


async def test_a_convened_plan_reaches_the_state_survives_the_document_and_drives_the_node():
    """The step before the integration: Tech Lead's plan carries `performance`, `plan`
    lands it in the state, the task document round-trips it, and the fan-in's routing
    reads 'convened' from it once the subtask is approved."""
    result = planned()
    assert isinstance(result.structured_output, dict)
    result.structured_output["performance"] = [PLAN]
    task = {
        "id": "GH-1",
        "title": "Perf",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
    }
    cfg = Configuration(product_dir=MemoryWorkspace.ROOT, skip_ephemeral=True)
    state = apply(task, await plan(task, cfg, memory_ports(ScriptedRunner(tech_lead=[result]))))
    assert state["performance"] == [PLAN]
    assert document.parse(document.render(state))["performance"] == [PLAN]
    assert rules.after_integrate(state) == "pipeline"  # the subtask is still to build

    unit = state["subtasks"]["api"]
    state["subtasks"]["api"] = {**unit, "status": "approved", "branch": "agentic/GH-1"}
    state["runs"] = [{"role": "Tech Lead", "section": rules.review_section("api"), "version": 1}]
    assert rules.after_integrate(state) == "qa_performance"
    route, final = await drive(
        state, ScriptedRunner(qa_performance=[reviewed("approved")]), FakePerfRunner(measured(40))
    )
    assert route == ["qa_performance", "deliver"]
    assert rules.perf_merge_facts(final)[:2] == (True, "approved")
