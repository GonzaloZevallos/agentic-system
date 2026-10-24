"""The plan's estimate at the `plan.plan` seam (ADR-0054): the history reaches the Tech
Lead as a placeholder value only, the check that refuses a bad estimate is injected on
both arms, and a plan that fits is delivered with its `estimate`."""

import pytest

from agentic.core.process.application.plan import plan
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    ScriptedRunner,
    memory_ports,
    planned,
    planned_parts,
    planned_tracker_only,
    questions,
    tech_answers,
)

pytestmark = pytest.mark.anyio

ASK, ANSWER = "tech_lead_ephemeral", "tech_lead_interviewee"
TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


def closed(task_id, cost, subtasks=1, **more):
    return {
        "id": task_id,
        "title": task_id,
        "description": "d",
        "closed_at": "2026-10-01",
        "cost_usd": cost,
        "subtasks": {f"s{i}": {"id": f"s{i}"} for i in range(subtasks)},
        **more,
    }


def cold(tmp_path):
    return Configuration(product_dir=tmp_path, skip_ephemeral=True)


def interviewed(tmp_path):
    return Configuration(product_dir=tmp_path)


def interview_runner(*plans):
    return ScriptedRunner(
        **{ASK: [questions(1, session="e1"), questions(done=True, session="e1")]},
        **{ANSWER: [tech_answers(1, session="t1")]},
        tech_lead=list(plans),
    )


async def test_a_plan_that_fits_is_delivered_with_its_estimate_on_both_arms(tmp_path):
    cold_patch = await plan(
        TASK,
        cold(tmp_path),
        memory_ports(ScriptedRunner(tech_lead=[planned()])),
    )
    interview_patch = await plan(
        TASK, interviewed(tmp_path), memory_ports(interview_runner(planned()))
    )
    for patch in (cold_patch, interview_patch):
        assert patch["status"] == "planned"
        assert patch["estimate"] == {"cost_usd": 20.0, "files": 5}


async def test_the_history_enters_the_prompt_but_never_the_recorded_hash(tmp_path):
    cheap, dear = (
        memory_ports(ScriptedRunner(tech_lead=[planned(estimated_cost_usd=100.0)])),
        memory_ports(ScriptedRunner(tech_lead=[planned(estimated_cost_usd=100.0)])),
    )
    dear.documents.saved.extend(closed(f"OLD-{i}", 90.0 + i) for i in range(6))
    cheap_patch = await plan(TASK, cold(tmp_path), cheap)
    dear_patch = await plan(TASK, cold(tmp_path), dear)

    assert cheap_patch["prompts"]["tech_lead"] == dear_patch["prompts"]["tech_lead"]
    cheap_prompt = cheap.runner.calls[0]["system_prompt"]
    dear_prompt = dear.runner.calls[0]["system_prompt"]
    assert "No closed task has a recorded cost yet" in cheap_prompt
    assert "1 subtasks: n=6" in dear_prompt and "No closed task" not in dear_prompt


async def test_the_history_is_also_given_on_the_interview_arm(tmp_path):
    ports = memory_ports(interview_runner(planned(estimated_cost_usd=100.0)))
    ports.documents.saved.extend(closed(f"OLD-{i}", 50.0) for i in range(5))
    await plan(TASK, interviewed(tmp_path), ports)
    assert "1 subtasks: n=5" in ports.runner.calls[-1]["system_prompt"]


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_an_estimate_below_what_was_spent_is_refused_and_retried_on_both_arms(arm, tmp_path):
    spent = {**TASK, "cost_usd": 50.0}
    scripted = [planned(estimated_cost_usd=20.0), planned(estimated_cost_usd=60.0)]
    if arm == "cold":
        runner, cfg = ScriptedRunner(tech_lead=scripted), cold(tmp_path)
    else:
        runner, cfg = interview_runner(*scripted), interviewed(tmp_path)
    patch = await plan(spent, cfg, memory_ports(runner))

    assert runner.slugs.count("tech_lead") == 2
    assert patch["status"] == "planned"
    assert patch["estimate"] == {"cost_usd": 60.0, "files": 5}
    assert [f["reason"] for f in patch["failures"]] == ["check"]
    assert "already spent" in patch["failures"][0]["error"]


async def test_an_estimate_below_half_the_bucket_median_is_refused(tmp_path):
    ports = memory_ports(
        ScriptedRunner(
            tech_lead=[planned(estimated_cost_usd=10.0), planned(estimated_cost_usd=45.0)]
        )
    )
    ports.documents.saved.extend(closed(f"OLD-{i}", 80.0) for i in range(5))
    patch = await plan(TASK, cold(tmp_path), ports)
    assert patch["estimate"]["cost_usd"] == 45.0
    assert "median" in patch["failures"][0]["error"]


# The two shapes of a plan at the `plan.plan` seam (ADR-0054): a plan that does not fit is
# delivered as parts and builds nothing; a plan that fits is delivered as subtasks.


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_plan_that_does_not_fit_is_delivered_as_parts_and_routes_to_split(arm, tmp_path):
    if arm == "cold":
        runner, cfg = ScriptedRunner(tech_lead=[planned_parts()]), cold(tmp_path)
    else:
        runner, cfg = interview_runner(planned_parts()), interviewed(tmp_path)
    patch = await plan(TASK, cfg, memory_ports(runner))

    assert patch["status"] == "planned"
    assert [p["title"] for p in patch["parts"]] == ["Catalogue", "Screens"]
    assert patch["estimate"] == {"cost_usd": 400.0, "files": 90}
    assert patch["invariants"] == ["Every part ships alone."]
    assert "subtasks" not in patch
    assert "### Parts" in patch["sections"]["Tech Lead"]
    assert "### Subtasks" not in patch["sections"]["Tech Lead"]
    assert rules.after_plan(apply(TASK, patch)) == "split"


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_plan_that_fits_carries_no_parts_and_routes_to_the_fan_in(arm, tmp_path):
    if arm == "cold":
        runner, cfg = ScriptedRunner(tech_lead=[planned()]), cold(tmp_path)
    else:
        runner, cfg = interview_runner(planned()), interviewed(tmp_path)
    patch = await plan(TASK, cfg, memory_ports(runner))

    assert "parts" not in patch and list(patch["subtasks"]) == ["api"]
    assert rules.after_plan(apply(TASK, patch)) == "integrate"


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_fit_that_does_not_hold_is_refused_as_subtasks_and_retried_as_parts(arm, tmp_path):
    scripted = [planned(estimated_cost_usd=400.0, estimated_files=90), planned_parts()]
    if arm == "cold":
        runner, cfg = ScriptedRunner(tech_lead=scripted), cold(tmp_path)
    else:
        runner, cfg = interview_runner(*scripted), interviewed(tmp_path)
    patch = await plan(TASK, cfg, memory_ports(runner))

    assert runner.slugs.count("tech_lead") == 2
    assert "parts" in patch
    assert "does not fit" in patch["failures"][0]["error"]


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_task_with_a_waived_fit_is_planned_whole_and_never_split(arm, tmp_path):
    waived = {**TASK, "fit_waived": True}
    scripted = [planned_parts(), planned(estimated_cost_usd=400.0, estimated_files=90)]
    if arm == "cold":
        runner, cfg = ScriptedRunner(tech_lead=scripted), cold(tmp_path)
    else:
        runner, cfg = interview_runner(*scripted), interviewed(tmp_path)
    patch = await plan(waived, cfg, memory_ports(runner))

    assert "parts" not in patch and list(patch["subtasks"]) == ["api"]
    assert "waived" in patch["failures"][0]["error"]


# The tracker-only rule reaches both rendered Tech Lead prompts (GH-336): the plan schema no
# longer says "every task changes a file", so the prompt is where the rule is taught.


@pytest.mark.parametrize("task_type", ["feature", "platform_enhancement"])
async def test_both_rendered_tech_lead_prompts_teach_the_tracker_only_rule(task_type, tmp_path):
    slug = "tech_lead_platform" if task_type == "platform_enhancement" else "tech_lead"
    runner = ScriptedRunner(**{slug: [planned()]})
    await plan({**TASK, "type": task_type}, cold(tmp_path), memory_ports(runner))

    prompt = " ".join(runner.calls[0]["system_prompt"].split())
    assert "task changes at least one file unless its only work is on the tracker" in prompt
    assert "`tracker_checklist`, no subtasks, no parts and `estimated_files: 0`" in prompt
    assert "Tracker work never becomes a subtask" in prompt
    assert "every task changes one" not in prompt


async def test_a_tracker_only_plan_is_delivered_with_its_checklist_and_no_subtasks(tmp_path):
    items = ["Post the verdict table on #307", "Close the follow-ups that are done"]
    runner = ScriptedRunner(tech_lead=[planned_tracker_only(items)])
    patch = await plan(TASK, cold(tmp_path), memory_ports(runner))

    assert patch["status"] == "planned"
    assert patch["tracker_checklist"] == items
    assert patch["estimate"] == {"cost_usd": 2.0, "files": 0}
    assert "subtasks" not in patch and "parts" not in patch
    assert "### Tracker work for the human" in patch["sections"]["Tech Lead"]
    assert "### Subtasks" not in patch["sections"]["Tech Lead"]


async def test_a_checklist_next_to_subtasks_is_stored_and_the_subtasks_still_are(tmp_path):
    runner = ScriptedRunner(tech_lead=[planned(tracker_checklist=["Close #9"])])
    patch = await plan(TASK, cold(tmp_path), memory_ports(runner))

    assert patch["tracker_checklist"] == ["Close #9"]
    assert list(patch["subtasks"]) == ["api"]
    assert "### Tracker work for the human" in patch["sections"]["Tech Lead"]
    assert "### Subtasks" in patch["sections"]["Tech Lead"]


async def test_a_plan_without_a_checklist_stores_none_and_adds_no_block(tmp_path):
    patch = await plan(TASK, cold(tmp_path), memory_ports(ScriptedRunner(tech_lead=[planned()])))
    assert "tracker_checklist" not in patch
    assert "Tracker work" not in patch["sections"]["Tech Lead"]
