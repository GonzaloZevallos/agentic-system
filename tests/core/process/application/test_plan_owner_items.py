"""The plan asks its owner decisions at its own end (GH-784, ADR-0062): a plan that passed every
gate and stands raises its `human` tracker items as a `questions` escalation before anything is
built. Seam: `plan.plan` with a scripted runner and in-memory ports, on both arms and on the
critic and re-plan routes. A plan that escalated, split, was promoted, has only tracker work or
belongs to a spec task asks nothing here."""

import pytest

from agentic.core.process.application.plan import plan
from agentic.core.process.domain import rules, tracker_items
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_plan_critic import (
    CRITIC_ARM,
    critique,
    sized,
)
from tests.core.process.application.test_plan_estimate import (
    ITEM1,
    PART,
    TASK,
    arm_of,
    over_the_line_by_reference,
)
from tests.core.process.application.test_plan_promote import _promoted_state
from tests.core.process.application.test_spec_assumptions_nodes import SPEC
from tests.fakes import (
    ScriptedRunner,
    memory_ports,
    planned,
    planned_parts,
    planned_tracker_only,
)

pytestmark = pytest.mark.anyio

SECOND = {"id": "ui", "title": "UI", "brief": "Show it.", "seams": ["page"]}
THIRD = {"id": "web", "title": "Web", "brief": "Serve it.", "seams": ["GET /y"]}
LIGHT = {**TASK, "path": "light", "initial_path": "light"}


def hashed(*items):
    return [tracker_items.item_hash(i) for i in items]


def asked_and_nothing_opened(patch, ports):
    assert patch["escalation_class"] == "questions"
    assert patch["status"] == "waiting_on_human"
    assert patch["tracker_human"] == hashed(ITEM1)
    assert ports.tracker.pull_requests == []
    assert ports.tracker.comments == []


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_fitting_plan_with_a_human_item_asks_before_anything_is_built(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, planned(tracker_items=[ITEM1]))
    ports = memory_ports(runner)
    patch = await plan(TASK, cfg, ports)

    asked_and_nothing_opened(patch, ports)
    assert list(patch["subtasks"]) == ["api"]
    assert patch["questions_for_human"] == [tracker_items.question_line(ITEM1)]
    assert [e["kind"] for e in patch["pause_log"]].count("escalation") == 1
    assert rules.after_plan(apply(TASK, patch)) is None


async def test_a_human_item_over_budget_escalates_the_estimate_not_the_questions(tmp_path):
    runner, cfg = arm_of("cold", tmp_path, planned(estimated_cost_usd=200.0, tracker_items=[ITEM1]))
    patch = await plan(PART, cfg, over_the_line_by_reference(memory_ports(runner)))

    assert patch["escalation_class"] == "estimate"
    assert "tracker_human" not in patch and "questions_for_human" not in patch


async def test_a_human_item_left_next_to_parts_routes_to_split_without_asking(tmp_path):
    held = {**TASK, "tracker_items": [ITEM1]}
    runner, cfg = arm_of("cold", tmp_path, planned_parts())
    patch = await plan(held, cfg, memory_ports(runner))

    assert "escalation_class" not in patch and "tracker_human" not in patch
    assert rules.after_plan(apply(held, patch)) == "split"


async def test_a_human_item_with_a_plan_over_the_light_cap_promotes_without_asking(tmp_path):
    runner, cfg = arm_of("cold", tmp_path, planned(SECOND, THIRD, tracker_items=[ITEM1]))
    patch = await plan(LIGHT, cfg, memory_ports(runner))

    assert patch["promotion"]["kind"] == "plan_over_cap"
    assert patch["path"] == "full"
    assert "escalation_class" not in patch and "tracker_human" not in patch


async def test_a_refused_promotion_does_not_hide_the_question(tmp_path):
    result = planned(tracker_items=[ITEM1])
    result.structured_output.update(promote="bigger")
    runner, cfg = arm_of("cold", tmp_path, result)
    ports = memory_ports(runner)
    patch = await plan({**LIGHT, "path_label": "light"}, cfg, ports)

    assert "path:light" in patch["sections"]["Tech Lead"]
    assert "promotion" not in patch and "path" not in patch
    asked_and_nothing_opened(patch, ports)


async def test_a_request_that_changes_nothing_does_not_hide_the_question(tmp_path):
    result = planned(tracker_items=[ITEM1])
    result.structured_output.update(promote="bigger")
    runner, cfg = arm_of("cold", tmp_path, result)
    ports = memory_ports(runner)
    patch = await plan({**TASK, "path": "full"}, cfg, ports)

    assert "promotion" not in patch
    asked_and_nothing_opened(patch, ports)


async def test_a_plan_that_stands_after_a_due_replan_with_only_tracker_work_does_not_ask(tmp_path):
    due = {**TASK, "status": "planned", "plan_due": True}
    runner, cfg = arm_of("cold", tmp_path, planned_tracker_only([ITEM1]))
    patch = await plan(due, cfg, memory_ports(runner))
    state = apply(due, patch)

    assert "escalation_class" not in patch
    assert state["plan_due"] is False
    assert rules.after_plan(state) == "tracker_only"


async def test_a_spec_task_with_a_human_item_never_raises(tmp_path):
    # The plan check refuses a human item under a spec on all its attempts: a failure, not a question.
    runner, cfg = arm_of("cold", tmp_path, *[planned(tracker_items=[ITEM1])] * 3)
    patch = await plan({**TASK, "spec": SPEC}, cfg, memory_ports(runner))

    assert patch["escalation_class"] == "failure"
    assert "tracker_human" not in patch


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_plan_with_no_tracker_items_adds_no_key(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, planned())
    patch = await plan(TASK, cfg, memory_ports(runner))

    assert patch["status"] == "planned"
    assert not {"tracker_human", "questions_for_human", "escalated", "pause_log"} & set(patch)


# The routes that reach the one exit: the critic after its single revision, and a re-plan.


async def test_the_critic_route_after_its_revision_asks_the_item(tmp_path):
    runner = ScriptedRunner(
        plan_sizer=[sized(cost=12.0)],
        tech_lead=[planned(tracker_items=[ITEM1]), planned(tracker_items=[ITEM1])],
        tech_lead_critic=[critique("important")],
    )
    ports = memory_ports(runner)
    cfg = Configuration(product_dir=tmp_path, plan_review="critic")
    patch = await plan({**TASK, "assignment": CRITIC_ARM}, cfg, ports)

    assert runner.slugs == ["plan_sizer", "tech_lead", "tech_lead_critic", "tech_lead"]
    asked_and_nothing_opened(patch, ports)


async def test_a_replan_that_stands_asks_the_item(tmp_path):
    runner = ScriptedRunner(
        tech_lead=[planned(SECOND, tracker_items=[ITEM1])] * 2,
        tech_lead_critic=[critique()],
    )
    ports = memory_ports(runner)
    patch = await plan(_promoted_state(), Configuration(product_dir=tmp_path), ports)

    assert "api" not in patch["subtasks"]
    asked_and_nothing_opened(patch, ports)
