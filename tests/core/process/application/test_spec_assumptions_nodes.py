"""The spec's assumptions at the nodes (GH-594 part 2b): with `state["spec"]` set, the Tech Lead's
plan (both its paths), the Developer's delivery and PM's final validation are asked for their
`*WithAssumptions` schema, and what they assumed lands in `state["assumptions"]` under the role's
name. Without a spec, the patch has no such key and the role gets the base schema class."""

from __future__ import annotations

import pytest

from agentic.core.process.application import final_validation as final_node
from agentic.core.process.application import plan as plan_module
from agentic.core.process.application.develop import develop
from agentic.core.process.application.plan import plan
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import DEVELOPER, PM_FINAL, TECH_LEAD
from agentic.core.process.domain.spec_delivery import SpecContract
from agentic.core.process.domain.task import TaskState, apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.core.process.application.test_final_validation_close_docs import runner_with, state
from tests.core.process.application.test_plan_critic import critique, sized
from tests.fakes import (
    MemoryStack,
    RawResult,
    ScriptedRunner,
    delivered,
    final_validation,
    memory_ports,
    planned,
    planned_tracker_only,
    questions,
)

pytestmark = pytest.mark.anyio

SPEC: SpecContract = {
    "path": "docs/specs/a.md",
    "slice": "s1",
    "commit": "a" * 40,
    "sha256": "b" * 64,
    "criteria": [],
    "screens": [],
    "risks": [],
    "touches_ui": False,
    "touches_copy": False,
}
PLAN_TASK: TaskState = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}
ASSUMED = "Totals are shown in USD"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def _assuming(result: RawResult, *texts: str) -> RawResult:
    result.structured_output["assumptions"] = list(texts)
    return result


def _rows(patch: TaskState, role: str, *texts: str) -> None:
    state_: TaskState = apply({}, patch)
    assert state_["assumptions"] == [{"role": role, "text": text} for text in texts]


def _asked(runner: ScriptedRunner, slug: str):
    return next(c["role"] for c in runner.calls if c["slug"] == slug)


# develop


async def _develop(cfg, result: RawResult, **extra):
    runner = ScriptedRunner(developer=[result])
    ports = memory_ports(runner, stack=MemoryStack())
    base, _ = building(ports)
    return runner, await develop({**base, **extra}, cfg, ports)


async def test_develop_with_a_spec_records_the_developers_assumptions(cfg):
    runner, patch = await _develop(
        cfg, _assuming(delivered(), ASSUMED, "  Dates   are UTC "), spec=SPEC
    )
    _rows(patch, DEVELOPER.name, ASSUMED, "Dates are UTC")
    # `Delivery` has no spec subclass: it already carries `assumptions`, and stays the same.
    assert _asked(runner, "developer").schema is schemas.Delivery


async def test_develop_without_a_spec_leaves_the_assumptions_out_of_the_patch(cfg):
    runner, patch = await _develop(cfg, _assuming(delivered(), ASSUMED))
    assert "assumptions" not in patch
    assert _asked(runner, "developer").schema is schemas.Delivery


async def test_develop_with_a_spec_and_nothing_assumed_adds_no_key(cfg):
    _, patch = await _develop(cfg, delivered(), spec=SPEC)
    assert "assumptions" not in patch


# plan: the interview path (plan.py `_interview_and_plan`) and the cold path (`_cold_draft`)


async def _plan(cfg, *, cold: bool, spec: SpecContract | None):
    interview = {} if cold else {"tech_lead_ephemeral": [questions(done=True)]}
    runner = ScriptedRunner(tech_lead=[_assuming(planned(), ASSUMED)], **interview)
    cfg = Configuration(product_dir=cfg.product_dir, skip_ephemeral=cold)
    task = {**PLAN_TASK, "spec": spec} if spec else dict(PLAN_TASK)
    return runner, await plan(task, cfg, memory_ports(runner))  # type: ignore[arg-type]


@pytest.mark.parametrize("cold", [False, True], ids=["interview path", "cold path"])
async def test_plan_with_a_spec_records_the_tech_leads_assumptions(cfg, cold):
    runner, patch = await _plan(cfg, cold=cold, spec=SPEC)
    _rows(patch, TECH_LEAD.name, ASSUMED)
    assert _asked(runner, "tech_lead").schema is schemas.TechPlanWithAssumptions


@pytest.mark.parametrize("cold", [False, True], ids=["interview path", "cold path"])
async def test_plan_without_a_spec_adds_no_key_and_asks_for_the_base_schema(cfg, cold):
    runner, patch = await _plan(cfg, cold=cold, spec=None)
    assert "assumptions" not in patch
    assert _asked(runner, "tech_lead").schema is schemas.TechPlan


# final validation


async def _final(cfg, result: RawResult, **extra):
    runner = runner_with(result)
    ports = memory_ports(runner)
    start = {**state(), **extra}
    checkout = ports.workspace.task_checkout(start["id"])
    return runner, await final_node.final_validation(start, cfg, ports, checkout=checkout)


async def test_final_validation_with_a_spec_records_pms_assumptions(cfg):
    runner, patch = await _final(cfg, _assuming(final_validation(), ASSUMED), spec=SPEC)
    _rows(patch, PM_FINAL.name, ASSUMED)
    assert _asked(runner, "pm_final").schema is schemas.FinalValidationWithAssumptions


async def test_final_validation_without_a_spec_adds_no_key_and_asks_for_the_base_schema(cfg):
    runner, patch = await _final(cfg, _assuming(final_validation(), ASSUMED))
    assert "assumptions" not in patch
    assert _asked(runner, "pm_final").schema is schemas.FinalValidation


# plan: a spec conflict raised in the plan (GH-594 part 3b), on the three routes

CONFLICT_ARM = {"experiment": "e1", "arm": "critic", "switches": {"plan_review": "critic"}}
ASKED_BY = {"kind": "escalation", "value": "questions", "at": "t1", "spec_cause": "contradiction"}
RESUMED_ENTRY = {"kind": "resumed", "at": "t2"}
SETTLED = "spec conflict (contradiction) settled by the owner's decision"
NOT_BUILT = (
    "estimate",
    "planned_size",
    "invariants",
    "integration_brief",
    "required_paths",
    "ci_required",
    "performance",
    "api_break",
    "tracker_items",
    "parts",
    "subtasks",
    "experiment",
    "plan_promote_request",
)
ROUTES = ["interview", "skip", "critic"]


def _conflicting(cause: str | None, **kwargs) -> RawResult:
    result = planned(**kwargs)
    if cause:
        result.structured_output["spec_conflict"] = cause
    return result


async def _route(tmp_path, route: str, *drafts: RawResult, **extra):
    """The plan node on `route`; `drafts` are the Tech Lead's calls in order."""
    task: TaskState = {**PLAN_TASK, **extra}
    if route == "critic":
        runner = ScriptedRunner(
            plan_sizer=[sized()], tech_lead=list(drafts), tech_lead_critic=[critique("important")]
        )
        cfg = Configuration(product_dir=tmp_path, plan_review="critic")
        task = {**task, "assignment": CONFLICT_ARM}
    else:
        interview = {} if route == "skip" else {"tech_lead_ephemeral": [questions(done=True)]}
        runner = ScriptedRunner(tech_lead=list(drafts), **interview)
        cfg = Configuration(product_dir=tmp_path, skip_ephemeral=route == "skip")
    return runner, await plan(task, cfg, memory_ports(runner))


@pytest.mark.parametrize("route", ROUTES)
async def test_an_undecided_spec_conflict_in_the_plan_escalates_and_builds_nothing(tmp_path, route):
    runner, patch = await _route(tmp_path, route, _conflicting("contradiction"), spec=SPEC)

    assert patch["escalation_class"] == "questions" and patch["spec_cause"] == "contradiction"
    assert patch["status"] == "waiting_on_human"
    assert patch["questions_for_human"] == [
        "Spec contradiction: the spec contradicts itself; decide which clause holds. "
        "(see the Tech Lead section of the task document)"
    ]
    assert [e["kind"] for e in patch["pause_log"]] == ["escalation"]
    assert patch["pause_log"][0]["spec_cause"] == "contradiction"
    assert patch.get("status") != "planned"
    assert not [key for key in NOT_BUILT if key in patch]
    assert TECH_LEAD.name in patch["sections"]
    assert patch["cost_usd"] > 0 and "tech_lead" in [r["slug"] for r in patch["runs"]]


async def test_a_conflict_in_the_critic_draft_stops_before_the_critic_and_the_revision(tmp_path):
    runner, patch = await _route(
        tmp_path, "critic", _conflicting("data_loss"), planned(), spec=SPEC
    )

    assert runner.slugs == ["plan_sizer", "tech_lead"]
    assert patch["spec_cause"] == "data_loss"


@pytest.mark.parametrize("route", ROUTES)
async def test_a_conflict_the_owner_decided_becomes_one_assumption_row(tmp_path, route):
    log = [ASKED_BY, RESUMED_ENTRY]
    drafts = [_conflicting("contradiction")] * (2 if route == "critic" else 1)
    _, patch = await _route(tmp_path, route, *drafts, spec=SPEC, pause_log=log)

    assert patch["status"] == "planned" and "escalation_class" not in patch
    assert "spec_cause" not in patch
    _rows(patch, TECH_LEAD.name, SETTLED)
    assert patch["subtasks"]


async def test_a_decided_contradiction_does_not_settle_an_undecided_data_loss(tmp_path):
    _, patch = await _route(
        tmp_path,
        "skip",
        _conflicting("data_loss"),
        spec=SPEC,
        pause_log=[ASKED_BY, RESUMED_ENTRY],
    )

    assert patch["spec_cause"] == "data_loss" and patch["escalation_class"] == "questions"


async def test_the_critic_route_escalates_resumes_and_escalates_on_another_cause(tmp_path):
    _, first = await _route(tmp_path, "critic", _conflicting("contradiction"), spec=SPEC)
    assert first["spec_cause"] == "contradiction"

    resumed = {**apply({**PLAN_TASK, "spec": SPEC}, first), "pause_log": []}
    resumed["pause_log"] = [*first["pause_log"], RESUMED_ENTRY]
    resumed.pop("escalation_class", None)
    resumed.pop("spec_cause", None)
    resumed.pop("questions_for_human", None)
    drafts = [_conflicting("contradiction"), _conflicting("contradiction")]
    _, again = await _route(tmp_path, "critic", *drafts, **_extra(resumed))
    assert again["status"] == "planned"
    _rows(again, TECH_LEAD.name, SETTLED)

    _, other = await _route(tmp_path, "critic", _conflicting("data_loss"), **_extra(resumed))
    assert other["spec_cause"] == "data_loss" and other["escalation_class"] == "questions"


def _extra(state: TaskState) -> dict:
    return {k: v for k, v in state.items() if k in ("spec", "pause_log", "sections", "runs")}


HUMAN_ITEM = {"kind": "human", "question": "Post it?", "recommendation": "Yes."}


@pytest.mark.parametrize("has_spec", [True, False], ids=["spec", "no spec"])
async def test_a_spec_plan_with_a_human_item_is_refused_and_the_same_plan_without_is_not(
    tmp_path, has_spec
):
    plan_result = planned_tracker_only([HUMAN_ITEM])
    runner, patch = await _route(
        tmp_path, "skip", plan_result, plan_result, **({"spec": SPEC} if has_spec else {})
    )

    if has_spec:
        assert patch["escalation_class"] == "failure"
        assert "spec_human_item" in str(patch["failures"]) + patch["escalated"]
    else:
        assert patch["status"] == "planned" and patch["tracker_items"]


def _stable(patch: TaskState) -> TaskState:
    """The patch without the run timings that differ between two identical calls."""
    runs = [{k: v for k, v in run.items() if k != "queue_wait_s"} for run in patch["runs"]]
    return {**patch, "runs": runs}


@pytest.mark.parametrize("stray", [None, "contradiction"], ids=["plain", "stray spec_conflict"])
@pytest.mark.parametrize("route", ROUTES)
async def test_without_a_spec_the_gate_adds_nothing(tmp_path, monkeypatch, route, stray):
    def drafts():
        return [_conflicting(stray)] * (2 if route == "critic" else 1)

    _, real = await _route(tmp_path, route, *drafts())
    monkeypatch.setattr(plan_module, "_spec_gate", lambda *args, **kwargs: None)
    _, stubbed = await _route(tmp_path, route, *drafts())

    assert _stable(real) == _stable(stubbed)
    assert real["status"] == "planned"
    assert not {"spec_cause", "questions_for_human", "assumptions", "pause_log"} & set(real)
