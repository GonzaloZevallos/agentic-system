"""The plan escalates a departure from an approved drawing before it dispatches subtasks
(GH-766 part 4b): with a declared `design`, a plan that stands with subtasks and declares a
departure the owner has not answered is paused as a `questions` escalation at stage `plan`,
with no subtasks, no promotion and no human tracker question. Without a `design` nothing about
the plan changes. Seam: `plan.plan` with a scripted runner and in-memory ports."""

from dataclasses import replace

import pytest

from agentic.core.process.application.plan import plan
from agentic.core.process.domain import document, drawing, drawing_answer, rules
from agentic.core.process.domain.design import Design
from agentic.core.process.domain.task import apply, departure_records
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_plan_critic import CRITIC_ARM, critique, sized
from tests.core.process.application.test_plan_estimate import (
    ITEM1,
    PART,
    TASK,
    arm_of,
    carve_ports,
    carve_result,
    cold,
    two_steps,
)
from tests.core.process.application.test_plan_promote import _promoted_state
from tests.core.process.application.test_spec_assumptions_nodes import SPEC
from tests.fakes import MemoryStack, ScriptedRunner, memory_ports, planned

pytestmark = pytest.mark.anyio

DESIGN = Design(prototype="pantallas", entry="Home", sample_app=None, compare=None)
SECOND = {"id": "ui", "title": "UI", "brief": "Show it.", "seams": ["page"]}
THIRD = {"id": "web", "title": "Web", "brief": "Serve it.", "seams": ["GET /y"]}
LIGHT = {**TASK, "path": "light", "initial_path": "light"}
RAW = {
    "drawing": "pantallas/home.html",
    "element": "header",
    "shows": "a search bar",
    "would_ship": "a plain title",
    "recommendation": "keep the plain title",
    "reason": "the search is out of scope",
    "raised_by": "tech_lead",
}
OTHER = {**RAW, "element": "footer"}
DEPARTURE = drawing.Departure(**RAW)
DRAWING_KEYS = {"drawing_departures", "drawing_escalation"}


def departing(*departures, **kwargs):
    """`planned(...)` whose plan declares `departures`."""
    result = planned(*kwargs.pop("subtasks", ()), **kwargs)
    result.structured_output.update(drawing_departures=list(departures))
    return result


def designed(runner):
    return memory_ports(runner, stack=MemoryStack(design=DESIGN))


def answered(verdict, raw=RAW):
    """A state's departure row for `raw` the owner has answered with `verdict`."""
    (row,) = departure_records(None, [drawing.Departure(**raw)], "t0")
    return {**row, "answer": {"verdict": verdict, "by": "comment", "at": "t1"}}


def paused_on_the_departure(patch, ports):
    assert patch["escalation_class"] == "questions"
    assert patch["status"] == "waiting_on_human"
    assert patch["drawing_escalation"]["stage"] == "plan"
    assert patch["drawing_escalation"]["numbers"] == [1]
    assert patch["escalated"] == drawing.departure_question(DEPARTURE, 1)
    assert not {"subtasks", "plan_promote_request", "plan_due", "parts"} & set(patch)
    assert ports.tracker.pull_requests == []
    assert rules.after_plan(apply(TASK, patch)) is None


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_an_open_departure_escalates_the_plan_and_dispatches_nothing(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, departing(RAW, invariants=["Keep it pure."]))
    ports = designed(runner)
    patch = await plan(TASK, cfg, ports)

    paused_on_the_departure(patch, ports)
    assert patch["estimate"]["files"] == 5
    assert patch["invariants"] == ["Keep it pure."]
    assert "### Subtasks" in patch["sections"]["Tech Lead"]
    assert [r["key"] for r in patch["drawing_departures"]] == [
        drawing.departure_key(DEPARTURE.drawing, DEPARTURE.element)
    ]
    assert ports.documents.saved[-1]["id"] == "GH-12"


async def test_an_accepted_departure_lets_the_plan_dispatch(tmp_path):
    held = {**TASK, "drawing_departures": [answered("accept")]}
    runner, cfg = arm_of("cold", tmp_path, departing(RAW))
    patch = await plan(held, cfg, designed(runner))

    assert "escalation_class" not in patch
    assert list(patch["subtasks"]) == ["api"]
    assert DRAWING_KEYS.isdisjoint(patch)
    assert rules.after_plan(apply(held, patch)) == "integrate"


async def test_a_rejected_departure_that_reappears_is_refused_inside_the_turn(tmp_path):
    held = {**TASK, "drawing_departures": [answered("reject")]}
    runner, cfg = arm_of("cold", tmp_path, departing(RAW), departing())
    patch = await plan(held, cfg, designed(runner))

    assert runner.slugs.count("tech_lead") == 2
    assert "declares again departure #1" in runner.calls[1]["prompt"]
    assert "escalation_class" not in patch
    assert list(patch["subtasks"]) == ["api"]


async def test_a_rejected_departure_that_keeps_reappearing_fails_the_plan(tmp_path):
    held = {**TASK, "drawing_departures": [answered("reject")]}
    runner, cfg = arm_of("cold", tmp_path, departing(RAW), departing(RAW), departing(RAW))
    patch = await plan(held, cfg, designed(runner))

    assert patch["escalation_class"] == "failure"
    assert "subtasks" not in patch and "drawing_escalation" not in patch


async def test_a_departure_with_a_promotion_request_escalates_and_does_not_promote(tmp_path):
    runner, cfg = arm_of("cold", tmp_path, departing(RAW, subtasks=(SECOND, THIRD)))
    patch = await plan(LIGHT, cfg, designed(runner))

    assert patch["drawing_escalation"]["stage"] == "plan"
    assert not {"promotion", "path", "plan_promote_request", "subtasks"} & set(patch)


async def test_a_departure_with_human_items_asks_only_about_the_departure(tmp_path):
    runner, cfg = arm_of("cold", tmp_path, departing(RAW, tracker_items=[ITEM1]))
    ports = designed(runner)
    patch = await plan(TASK, cfg, ports)

    paused_on_the_departure(patch, ports)
    assert not {"tracker_human", "questions_for_human"} & set(patch)
    assert ITEM1["question"] not in patch["escalated"]


async def test_a_spec_task_escalates_with_the_drawing_cause(tmp_path):
    runner, cfg = arm_of("cold", tmp_path, departing(RAW))
    patch = await plan({**TASK, "spec": SPEC}, cfg, designed(runner))

    assert patch["escalation_class"] == "questions"
    assert patch["spec_cause"] == "drawing"
    assert patch["drawing_escalation"]["stage"] == "plan"
    assert "spec_refusal" not in patch
    assert "subtasks" not in patch


async def test_the_critic_route_judges_the_revision_not_the_draft(tmp_path):
    runner = ScriptedRunner(
        plan_sizer=[sized()],
        tech_lead=[departing(RAW), departing()],
        tech_lead_critic=[critique("important")],
    )
    cfg = Configuration(product_dir=tmp_path, plan_review="critic")
    patch = await plan({**TASK, "assignment": CRITIC_ARM}, cfg, designed(runner))

    assert runner.slugs == ["plan_sizer", "tech_lead", "tech_lead_critic", "tech_lead"]
    assert "escalation_class" not in patch
    assert list(patch["subtasks"]) == ["api"]
    assert DRAWING_KEYS.isdisjoint(patch)


async def test_the_critic_route_escalates_a_departure_the_revision_keeps(tmp_path):
    runner = ScriptedRunner(
        plan_sizer=[sized()],
        tech_lead=[departing(), departing(RAW)],
        tech_lead_critic=[critique("important")],
    )
    cfg = Configuration(product_dir=tmp_path, plan_review="critic")
    patch = await plan({**TASK, "assignment": CRITIC_ARM}, cfg, designed(runner))

    assert patch["drawing_escalation"]["stage"] == "plan"
    assert "subtasks" not in patch


async def test_a_promoted_replan_escalates_keeps_its_due_and_returns_to_plan(tmp_path):
    held = _promoted_state(plan_due=True, status="planned")
    runner = ScriptedRunner(
        tech_lead=[departing(OTHER, subtasks=(SECOND,))] * 2,
        tech_lead_critic=[critique()],
    )
    cfg = Configuration(product_dir=tmp_path)
    patch = await plan(held, cfg, designed(runner))

    assert patch["drawing_escalation"]["stage"] == "plan"
    assert not {"subtasks", "plan_due"} & set(patch)
    paused = apply(held, patch)
    assert paused["plan_due"] is True
    assert list(paused["subtasks"]) == ["api"]
    assert rules.after_plan(paused) is None

    (row,) = patch["drawing_departures"]
    resumed = apply(
        paused,
        drawing_answer.resume_patch(
            paused,
            None,
            None,
            [
                answered("accept", OTHER)
                | row
                | {"answer": {"verdict": "accept", "by": "comment", "at": "t2"}}
            ],
            at="t2",
        ),
    )
    assert resumed["plan_due"] is True
    assert "escalated" not in resumed or not resumed["escalated"]

    runner2 = ScriptedRunner(
        tech_lead=[departing(OTHER, subtasks=(SECOND,))] * 2, tech_lead_critic=[critique()]
    )
    again = await plan(resumed, cfg, designed(runner2))
    assert "escalation_class" not in again
    assert again["plan_due"] is False
    assert set(again["subtasks"]) == {"ui"}


async def test_without_a_design_the_departures_are_ignored_and_nothing_is_written(tmp_path):
    held = {**TASK, "drawing_departures": [answered("reject")]}
    baseline_runner, cfg = arm_of("cold", tmp_path, planned())
    baseline = await plan(TASK, cfg, memory_ports(baseline_runner))
    runner, cfg = arm_of("cold", tmp_path, departing(RAW))
    patch = await plan(held, cfg, memory_ports(runner))
    rejected_runner, cfg = arm_of("cold", tmp_path, departing(RAW))
    again = await plan(held, cfg, memory_ports(rejected_runner))

    assert set(patch) == set(baseline)
    assert list(patch["subtasks"]) == ["api"]
    assert DRAWING_KEYS.isdisjoint(patch)
    assert rejected_runner.slugs.count("tech_lead") == 1  # the rejected key is not refused
    assert set(again) == set(baseline)


class UnreadableStack(MemoryStack):
    def design(self, revision):
        raise ValueError("stack.yaml: design is not readable")


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_an_unreadable_stack_fails_the_node_and_writes_nothing(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, departing(RAW))
    ports = memory_ports(runner, stack=UnreadableStack())
    with pytest.raises(ValueError, match="design is not readable"):
        await plan(TASK, cfg, ports)

    assert ports.documents.saved == []


# -- the plan the node keeps when it escalates on its own departures (GH-1004) ------------------


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_an_open_departure_keeps_the_plan_with_its_round_estimate_and_departures(
    arm, tmp_path
):
    runner, cfg = arm_of(arm, tmp_path, departing(RAW, invariants=["Keep it pure."]))
    patch = await plan(TASK, cfg, designed(runner))

    held = patch["plan_held"]
    assert held["round"] == patch["drawing_escalation"]["round"]
    assert held["estimate"] == patch["estimate"]
    assert held["departures"] == held["plan"]["drawing_departures"] == [RAW]
    assert [drawing.Departure(**row) for row in held["departures"]] == [DEPARTURE]
    assert held["plan"]["invariants"] == ["Keep it pure."]
    assert held["plan"]["subtasks"][0]["id"] == "api"
    stored = document.render({**TASK, **patch})
    assert "&id" not in stored and "*id" not in stored
    assert document.parse(stored)["plan_held"] == held


async def test_the_critic_route_keeps_the_revision_it_escalated_on(tmp_path):
    runner = ScriptedRunner(
        plan_sizer=[sized()],
        tech_lead=[departing(), departing(RAW)],
        tech_lead_critic=[critique("important")],
    )
    cfg = Configuration(product_dir=tmp_path, plan_review="critic")
    patch = await plan({**TASK, "assignment": CRITIC_ARM}, cfg, designed(runner))

    assert patch["plan_held"]["departures"] == [RAW]
    assert patch["plan_held"]["estimate"] == patch["estimate"]


async def test_a_floored_estimate_is_held_as_the_stored_one_with_the_declared_figure(tmp_path):
    spent = {**TASK, "cost_usd": 50.0}
    runner, cfg = arm_of("cold", tmp_path, departing(RAW, estimated_cost_usd=20.0, cost=30.0))
    patch = await plan(spent, cfg, designed(runner))

    held = patch["plan_held"]
    assert held["estimate"] == patch["estimate"]
    assert held["estimate"]["declared_usd"] == 20.0
    # The lift to what the task has spent after the node is above the floored plan's figure.
    assert held["plan"]["estimated_cost_usd"] >= 70.0
    assert held["estimate"]["cost_usd"] > held["plan"]["estimated_cost_usd"]


async def test_a_carve_out_that_lands_a_remainder_escalates_without_holding_the_plan(tmp_path):
    two = two_steps()
    two.structured_output.update(drawing_departures=[RAW])
    runner = ScriptedRunner(tech_lead=[two], tech_lead_carve_out=[carve_result()])
    ports = replace(carve_ports(runner), stack=MemoryStack(design=DESIGN))
    patch = await plan(PART, cold(tmp_path), ports)

    assert patch["carve_out"]["outcome"] == "applied"
    assert patch["drawing_escalation"]["stage"] == "plan"
    assert "subtasks" not in patch
    assert "plan_held" not in patch


@pytest.mark.parametrize(
    "script",
    [
        pytest.param(lambda: planned(), id="no departure"),
        pytest.param(lambda: departing(), id="no departure declared"),
    ],
)
async def test_a_plan_that_does_not_escalate_holds_nothing(script, tmp_path):
    runner, cfg = arm_of("cold", tmp_path, script())
    patch = await plan(TASK, cfg, designed(runner))

    assert "escalation_class" not in patch
    assert "plan_held" not in patch


async def test_an_accepted_departure_dispatches_and_holds_nothing(tmp_path):
    held = {**TASK, "drawing_departures": [answered("accept")]}
    runner, cfg = arm_of("cold", tmp_path, departing(RAW))
    patch = await plan(held, cfg, designed(runner))

    assert "plan_held" not in patch
