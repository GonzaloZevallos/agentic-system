"""The plan's estimate at the `plan.plan` seam (ADR-0054): the history reaches the Tech
Lead as a placeholder value only, the check that refuses a bad estimate is injected on
both arms, and a plan that fits is delivered with its `estimate`."""

import pytest

from agentic.core.process.application.plan import plan
from agentic.core.process.domain import rules
from agentic.core.process.domain.size_budget import PROVEN_PAIRS, ActualSize, PlanSize
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_preflight import ORIGINAL_PARTS_WORDING
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
async def test_an_estimate_below_what_was_spent_is_raised_in_one_attempt_on_both_arms(
    arm, tmp_path
):
    spent = {**TASK, "cost_usd": 50.0}
    scripted = [planned(estimated_cost_usd=20.0)]
    if arm == "cold":
        runner, cfg = ScriptedRunner(tech_lead=scripted), cold(tmp_path)
    else:
        runner, cfg = interview_runner(*scripted), interviewed(tmp_path)
    patch = await plan(spent, cfg, memory_ports(runner))

    assert runner.slugs.count("tech_lead") == 1
    assert patch["status"] == "planned"
    assert patch["estimate"]["declared_usd"] == 20.0
    assert patch["estimate"]["cost_usd"] >= 70.0
    assert not patch.get("failures")


async def test_an_estimate_below_half_the_bucket_median_is_raised_to_the_median(tmp_path):
    ports = memory_ports(ScriptedRunner(tech_lead=[planned(estimated_cost_usd=10.0)]))
    ports.documents.saved.extend(closed(f"OLD-{i}", 80.0) for i in range(5))
    patch = await plan(TASK, cold(tmp_path), ports)
    assert patch["estimate"] == {"cost_usd": 80.0, "files": 5, "declared_usd": 10.0}
    assert not patch.get("failures")


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


# One estimate, one verdict whatever the shape (GH-462): the history's reference bucket is
# pinned for the run, so the two attempts of one run never refuse each other's answer.


def seven_subtasks(**more):
    extra = [
        {"id": f"s{i}", "title": f"S{i}", "brief": "Build it.", "seams": ["GET /x"]}
        for i in range(6)
    ]
    return planned(*extra, estimated_cost_usd=38.0, estimated_files=21, **more)


def history_ports(runner, *, seven: float, two: float | None = None):
    ports = memory_ports(runner)
    ports.documents.saved.extend(closed(f"S7-{i}", seven, subtasks=7) for i in range(5))
    if two is not None:
        ports.documents.saved.extend(closed(f"S2-{i}", two, subtasks=2) for i in range(5))
    return ports


async def test_parts_that_fit_then_subtasks_produce_a_plan_with_subtasks(tmp_path):
    # The issue's literal case: the estimate fits the bucket of 7, the model still writes
    # parts, the refusal says to drop them, and the retry is accepted.
    runner = ScriptedRunner(
        tech_lead=[planned_parts(estimated_cost_usd=38.0, estimated_files=21), seven_subtasks()]
    )
    patch = await plan(TASK, cold(tmp_path), history_ports(runner, seven=10.0))

    assert runner.slugs.count("tech_lead") == 2
    assert "parts" not in patch and len(patch["subtasks"]) == 7
    assert "write subtasks and no parts" in patch["failures"][0]["error"]
    assert rules.after_plan(apply(TASK, patch)) == "integrate"


async def test_subtasks_over_the_bucket_then_parts_produce_parts_not_a_loop(tmp_path):
    # GH-462: the same estimate was refused as 7 subtasks (bucket of 7) and then as 2 parts
    # (bucket of 2, "fits"). With the bucket pinned, the parts are judged against the bucket
    # that refused the subtasks, and the run delivers them.
    runner = ScriptedRunner(
        tech_lead=[
            seven_subtasks(),
            planned_parts(estimated_cost_usd=38.0, estimated_files=21),
        ]
    )
    patch = await plan(TASK, cold(tmp_path), history_ports(runner, seven=200.0, two=10.0))

    assert runner.slugs.count("tech_lead") == 2
    assert [p["title"] for p in patch["parts"]] == ["Catalogue", "Screens"]
    assert "does not fit" in patch["failures"][0]["error"]
    assert "ADR-0054" in patch["failures"][0]["error"]


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


# A task that is a part of a split (ADR-0054, GH-338 and GH-346): the Tech Lead is told so,
# a part never gets `parts` as a remedy, and a part over the line asks the human once.

PART = {
    **TASK,
    "id": "GH-346",
    "description": f"Second half.\n\n{rules.split_marker('GH-316', 2, 2)}",
}
PART_DEPTH_ONE = {
    **PART,
    "description": f"Second half.\n\n{rules.split_marker('GH-316', 2, 1)}",
}
PART_OLD_MARKER = {**PART, "description": "Second half.\n\n<!-- agentic-split: GH-316 part 2 -->"}


def arm_of(arm, tmp_path, *scripted):
    if arm == "cold":
        return ScriptedRunner(tech_lead=list(scripted)), cold(tmp_path)
    return interview_runner(*scripted), interviewed(tmp_path)


def over_the_line_by_reference(ports):
    """The GH-338 shape: the plan declares a small estimate, but the history of tasks with
    this many subtasks says one task of that size costs more than the limit."""
    ports.documents.saved.extend(closed(f"OLD-{i}", 400.0) for i in range(6))
    return ports


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_part_at_the_depth_cap_over_the_line_asks_once_and_keeps_its_plan(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, planned(estimated_cost_usd=200.0))
    patch = await plan(PART, cfg, over_the_line_by_reference(memory_ports(runner)))

    assert runner.slugs.count("tech_lead") == 1
    assert patch["escalation_class"] == "estimate"
    assert patch["fit_question"] is True
    assert "proposed_estimate" not in patch
    # The plan stays in the Tech Lead's section; nothing is queued to build.
    assert "subtasks" not in patch and "api" in patch["sections"]["Tech Lead"]
    assert "part 2 of GH-316" in patch["escalated"] and "not planned" in patch["escalated"]
    assert "Build it whole" not in patch["escalated"]
    assert [e["kind"] for e in patch["pause_log"]].count("escalation") == 1
    assert patch["failures"] == []


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_the_resume_of_a_part_over_the_line_does_not_waive_the_fit_and_plans_again(
    arm, tmp_path
):
    runner, cfg = arm_of(arm, tmp_path, planned(estimated_cost_usd=200.0))
    patch = await plan(PART, cfg, over_the_line_by_reference(memory_ports(runner)))
    paused = apply({**PART, "type": "platform_enhancement"}, patch)

    assert rules.auto_resumable(paused) is None
    resumed = apply(paused, rules.resumed(paused, "narrow it", at="2026-11-06T12:00:00+00:00"))

    assert "fit_waived" not in resumed and resumed["fit_question"] is False
    assert rules.resume_target(resumed) == "plan"


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_part_at_depth_one_over_the_line_is_told_to_write_parts_and_never_asked(
    arm, tmp_path
):
    runner, cfg = arm_of(arm, tmp_path, planned(estimated_cost_usd=200.0), planned_parts())
    patch = await plan(PART_DEPTH_ONE, cfg, over_the_line_by_reference(memory_ports(runner)))

    assert "does not fit" in patch["failures"][0]["error"]
    assert "write parts" in patch["failures"][0]["error"]
    assert "fit_question" not in patch and "escalated" not in patch
    assert patch["parts"] and "subtasks" not in patch


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_part_with_an_old_marker_is_read_at_depth_one_and_splits(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, planned_parts())
    patch = await plan(PART_OLD_MARKER, cfg, memory_ports(runner))

    assert patch["parts"] and "escalated" not in patch


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_part_whose_plan_fits_goes_on_with_no_escalation(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, planned())
    patch = await plan(PART, cfg, memory_ports(runner))

    assert "escalated" not in patch and "fit_question" not in patch
    assert rules.after_plan(apply(PART, patch)) == "integrate"


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_part_with_the_fit_waived_is_not_asked_again(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, planned(estimated_cost_usd=200.0))
    waived = {**PART, "fit_waived": True}
    patch = await plan(waived, cfg, over_the_line_by_reference(memory_ports(runner)))

    assert "escalated" not in patch and list(patch["subtasks"]) == ["api"]


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_task_that_is_not_a_part_over_the_line_by_reference_is_still_refused(arm, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, planned(estimated_cost_usd=200.0), planned_parts())
    patch = await plan(TASK, cfg, over_the_line_by_reference(memory_ports(runner)))

    assert "fit_question" not in patch
    assert "does not fit" in patch["failures"][0]["error"]


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_the_refusal_of_parts_on_a_part_says_subtasks_only(arm, tmp_path):
    """GH-346: parts plus a checklist on a part. The retry reads the rule, not 'write parts'."""
    with_checklist = planned_parts()
    with_checklist.structured_output["tracker_checklist"] = ["Close the old board."]
    runner, cfg = arm_of(arm, tmp_path, with_checklist, planned())
    patch = await plan(PART, cfg, memory_ports(runner))

    error = patch["failures"][0]["error"]
    assert "part 2 of GH-316" in error and "write subtasks only" in error
    assert "tracker_checklist" not in error
    assert runner.slugs.count("tech_lead") == 2
    assert list(patch["subtasks"]) == ["api"]


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_the_tech_lead_of_a_part_is_told_it_is_one(arm, tmp_path):
    part_runner, cfg = arm_of(arm, tmp_path, planned())
    await plan(PART, cfg, memory_ports(part_runner))
    whole_runner, cfg = arm_of(arm, tmp_path, planned())
    await plan(TASK, cfg, memory_ports(whole_runner))

    told = part_runner.calls[-1]["system_prompt"]
    assert "part 2 of task GH-316" in told and "deepest a split goes" in told
    assert "part 2" not in whole_runner.calls[-1]["system_prompt"]


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_the_tech_lead_of_a_task_that_is_not_a_part_reads_the_original_parts_wording(
    arm, tmp_path
):
    """The three passages the plan stage fills in for a task that is not a part are the
    ones the prompt carried before (typed in `ORIGINAL_PARTS_WORDING`, git 63d29be), and a
    part reads none of them."""
    whole_runner, cfg = arm_of(arm, tmp_path, planned())
    await plan(TASK, cfg, memory_ports(whole_runner))
    part_runner, cfg = arm_of(arm, tmp_path, planned())
    await plan(PART, cfg, memory_ports(part_runner))

    whole, part = whole_runner.calls[-1]["system_prompt"], part_runner.calls[-1]["system_prompt"]
    for wording in ORIGINAL_PARTS_WORDING.values():
        assert wording in whole
        assert wording not in part
    assert "{{" not in whole and "{{" not in part


@pytest.mark.parametrize("arm", ["cold", "interview"])
@pytest.mark.parametrize("cost", [160.0, 300.0, 400.0])
async def test_a_part_at_the_depth_cap_is_never_built_whole_over_the_line(arm, cost, tmp_path):
    runner, cfg = arm_of(arm, tmp_path, planned(estimated_cost_usd=cost))
    ports = memory_ports(runner)
    patch = await plan(PART, cfg, ports)

    assert patch["escalation_class"] == "estimate" and patch["fit_question"] is True
    assert "fit_waived" not in patch
    assert ports.tracker.comments == []


# -- the plan_shape: single arm (GH-471), on both paths of the plan node --------------------


def single_cfg(tmp_path, *, skip):
    return Configuration(product_dir=tmp_path, plan_shape="single", skip_ephemeral=skip)


EXTRA = {"id": "ui", "title": "UI", "brief": "Build the screen.", "seams": ["GET /y"]}


@pytest.mark.parametrize("skip", [True, False])
async def test_single_refuses_two_subtasks_and_accepts_the_retry_with_one(skip, tmp_path):
    scripted = [planned(EXTRA), planned()]
    runner = ScriptedRunner(tech_lead=scripted) if skip else interview_runner(*scripted)
    patch = await plan(TASK, single_cfg(tmp_path, skip=skip), memory_ports(runner))

    assert runner.slugs.count("tech_lead") == 2
    assert patch["status"] == "planned"
    assert [f["reason"] for f in patch["failures"]] == ["check"]
    assert "plan_shape single: you wrote 2 subtasks" in patch["failures"][0]["error"]
    first = runner.calls[runner.slugs.index("tech_lead")]
    assert "exactly one subtask" in first["system_prompt"]
    assert patch["experiment"]["plan_shape"] == "single"


@pytest.mark.parametrize("skip", [True, False])
async def test_single_refuses_an_integration_text(skip, tmp_path):
    scripted = [planned(integration="wire the CLI"), planned()]
    runner = ScriptedRunner(tech_lead=scripted) if skip else interview_runner(*scripted)
    patch = await plan(TASK, single_cfg(tmp_path, skip=skip), memory_ports(runner))
    assert "plan_shape single: write no `integration`" in patch["failures"][0]["error"]
    assert patch["status"] == "planned"


async def test_the_free_default_accepts_two_subtasks_and_says_nothing_of_the_shape(tmp_path):
    runner = ScriptedRunner(tech_lead=[planned(EXTRA)])
    patch = await plan(TASK, cold(tmp_path), memory_ports(runner))
    assert patch["failures"] == []
    assert len(patch["subtasks"]) == 2
    assert "exactly one subtask" not in runner.calls[0]["system_prompt"]
    assert "plan_shape" not in patch.get("experiment", {})


# The floor on the declared hand-written size (GH-592): 1.5 until ten closed tasks recorded both
# sizes, then the p90 of what they really were, in the prompt and in the fit.


def sized_closed(count, *, planned_files=2, actual_files=6):
    planned_size = PlanSize(planned_files, 20, 0).to_frontmatter()
    actual_size = ActualSize(actual_files, 100, 0, 0).to_frontmatter()
    return [closed(f"SZ-{i}", 10.0, **planned_size, **actual_size) for i in range(count)]


@pytest.mark.parametrize("arm", ["cold", "interview"])
async def test_a_proven_ratio_judges_the_declared_files_and_tells_the_planner(arm, tmp_path):
    scripted = [planned(hand_files=6), planned_parts()]
    if arm == "cold":
        runner, cfg = ScriptedRunner(tech_lead=scripted), cold(tmp_path)
    else:
        runner, cfg = interview_runner(*scripted), interviewed(tmp_path)
    ports = memory_ports(runner)
    ports.documents.saved.extend(sized_closed(PROVEN_PAIRS))
    patch = await plan(TASK, cfg, ports)

    # 6 declared files x 3.0 is 18, over the 15 cap: refused as subtasks, retried as parts.
    assert runner.slugs.count("tech_lead") == 2 and "parts" in patch
    assert "does not fit" in patch["failures"][0]["error"]
    told = " ".join(runner.calls[-2]["system_prompt"].split())
    assert "judged x 3 files and x 5 lines" in told


async def test_an_unproven_ratio_is_the_fixed_factor(tmp_path):
    runner = ScriptedRunner(tech_lead=[planned(hand_files=6)])
    ports = memory_ports(runner)
    ports.documents.saved.extend(sized_closed(PROVEN_PAIRS - 1))
    patch = await plan(TASK, cold(tmp_path), ports)

    # 6 x 1.5 is 9, within the cap: one attempt, delivered as subtasks.
    assert runner.slugs == ["tech_lead"] and not patch.get("failures") and "parts" not in patch
    assert "judged x 1.5, rounded up" in " ".join(runner.calls[0]["system_prompt"].split())


async def test_a_part_at_the_depth_cap_is_judged_with_the_proven_ratio_too(tmp_path):
    # PART is at the depth cap: a plan it cannot fit asks the human, one that fits does not.
    unproven, proven = (
        memory_ports(ScriptedRunner(tech_lead=[planned(hand_files=6)])) for _ in "ab"
    )
    proven.documents.saved.extend(sized_closed(PROVEN_PAIRS))
    fits = await plan(PART, cold(tmp_path), unproven)
    refused = await plan(PART, cold(tmp_path), proven)

    assert "fit_question" not in fits and "subtasks" in fits
    assert refused["fit_question"] is True and "subtasks" not in refused
