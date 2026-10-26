"""GH-362, Rule 2 (ADR-0045 addendum): Cercanía. `rules.closeness` ranks a task by how close
it is to merge, and `poll_plan` starts what counts against the cap in that order."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.domain import rules

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

DEFINITION = rules.CLOSENESS.index("definition")
PLAN = rules.CLOSENESS.index("plan")
DEVELOPMENT = rules.CLOSENESS.index("development")
REVIEW = rules.CLOSENESS.index("review")
DELIVERED = rules.CLOSENESS.index("delivered")


def _run(section: str) -> dict:
    return {"role": section.split(":")[0], "section": section, "cost_usd": 1.0}


# `rules.closeness`


def test_closeness_is_built_from_the_phases_with_launch_first_and_land_last():
    assert rules.CLOSENESS == ("launch", *rules.PHASES, "delivered", "land")
    assert rules.CLOSENESS[0] == "launch"
    assert rules.CLOSENESS[-1] == "land"


def test_every_phase_is_ranked_between_launch_and_delivered_so_a_new_one_needs_no_edit():
    # The phases are not listed again: whatever `PHASES` holds sits between the two ends.
    assert rules.CLOSENESS[1 : 1 + len(rules.PHASES)] == rules.PHASES
    assert rules.CLOSENESS[1 + len(rules.PHASES) :] == ("delivered", "land")


def test_a_task_with_no_runs_is_in_definition():
    assert rules.closeness({}) == DEFINITION
    assert rules.closeness({"runs": []}) == DEFINITION


def test_a_run_of_a_developer_section_is_development():
    assert rules.closeness({"runs": [_run("Developer: api")]}) == DEVELOPMENT


def test_a_return_to_plan_after_development_stays_development():
    runs = [_run("Developer: api"), _run("Tech Lead")]
    assert rules.closeness({"runs": runs}) == DEVELOPMENT


def test_a_review_run_is_review_and_beats_the_earlier_phases():
    runs = [_run("PM"), _run("Tech Lead"), _run("Developer: api"), _run("Tech Lead: review api")]
    assert rules.closeness({"runs": runs}) == REVIEW


def test_a_run_without_a_section_is_read_by_its_role():
    assert rules.closeness({"runs": [{"role": "PM", "cost_usd": 1.0}]}) == DEFINITION


def test_a_pull_request_is_delivered_whatever_the_runs_say():
    state = {"pull_request": "https://example.test/pull/9", "runs": [_run("PM")]}
    assert rules.closeness(state) == DELIVERED


def test_the_ranks_run_from_launch_through_land():
    assert rules.CLOSENESS.index("launch") < DEFINITION < PLAN < DEVELOPMENT < REVIEW
    assert REVIEW < DELIVERED < rules.CLOSENESS.index("land")


# `rules.poll_plan` ordering


def _plan(**kwargs) -> rules.PollPlan:
    base: dict = {"closes": [], "resumes": [], "launches": [], "now": NOW}
    return rules.poll_plan(**{**base, **kwargs})


def _due(issue: int) -> rules.ResumeCandidate:
    return rules.ResumeCandidate(issue, "account_limit", NOW - timedelta(days=1), 1, 5)


def _pending(issue: int) -> rules.ResumeCandidate:
    return rules.ResumeCandidate(issue, "account_limit", NOW, 0, 5)


MODEL_LAND = [rules.LandCandidate(4, "land_model")]
LAUNCH = [rules.LaunchCandidate(80, False, None, 0, 3)]


def test_a_development_resume_beats_a_definition_resume_when_one_slot_is_free():
    plan = _plan(
        available_slots=1,
        resumes=[_due(3), _due(5)],
        closeness={3: DEFINITION, 5: DEVELOPMENT},
    )
    assert plan.actions == [rules.PollAction("resume", 5)]


def test_a_delivered_retake_beats_a_plan_resume_when_one_slot_is_free():
    plan = _plan(
        available_slots=1,
        retakes=[7],
        resumes=[_due(3)],
        closeness={7: DELIVERED, 3: PLAN},
    )
    assert plan.actions == [rules.PollAction("retake", 7)]


def test_a_review_resume_beats_a_plan_retake_when_one_slot_is_free():
    plan = _plan(
        available_slots=1,
        retakes=[2],
        resumes=[_due(9)],
        closeness={2: PLAN, 9: REVIEW},
    )
    assert plan.actions == [rules.PollAction("resume", 9)]


def test_a_crash_retake_is_ranked_with_the_other_retakes():
    plan = _plan(
        available_slots=1,
        retakes=[2],
        crash_retakes=[6],
        closeness={2: PLAN, 6: REVIEW},
    )
    assert plan.actions == [rules.PollAction("retake", 6, reason="crash")]


def test_equal_ranks_fall_back_to_the_lowest_issue():
    plan = _plan(
        available_slots=1,
        resumes=[_due(6), _due(4)],
        closeness={6: DEVELOPMENT, 4: DEVELOPMENT},
    )
    assert plan.actions == [rules.PollAction("resume", 4)]


def test_with_no_mapping_every_rank_ties_and_the_lowest_issue_goes_first():
    plan = _plan(available_slots=2, resumes=[_due(6), _due(4), _due(5)])
    assert plan.actions == [rules.PollAction("resume", 4), rules.PollAction("resume", 5)]


def test_a_retake_goes_before_a_resume_of_the_same_rank():
    plan = _plan(available_slots=1, retakes=[9], resumes=[_due(3)])
    assert plan.actions == [rules.PollAction("retake", 9)]


def test_a_missing_key_is_rank_zero_and_loses_to_any_ranked_task():
    plan = _plan(
        available_slots=1,
        resumes=[_due(3), _due(9)],
        closeness={9: DEFINITION},
    )
    assert plan.actions == [rules.PollAction("resume", 9)]


def test_actions_come_out_in_rank_order_not_issue_order():
    plan = _plan(
        available_slots=3,
        retakes=[8],
        resumes=[_due(2), _due(5)],
        closeness={8: PLAN, 2: DEFINITION, 5: REVIEW},
    )
    assert plan.actions == [
        rules.PollAction("resume", 5),
        rules.PollAction("retake", 8),
        rules.PollAction("resume", 2),
    ]


def test_a_land_outranks_every_retake_and_resume_even_a_delivered_one():
    plan = _plan(
        available_slots=1,
        land_slots=1,
        retakes=[2],
        resumes=[_due(3)],
        lands=MODEL_LAND,
        closeness={2: DELIVERED, 3: DELIVERED},
    )
    assert plan.actions == [rules.PollAction("land", 4)]
    assert plan.deferred == []


# Each row of the pool table (`A` = available_slots, `H` = land_slots, `n` = due retakes and
# resumes).


def test_row_account_paused_defers_the_land_and_starts_min_n_a_resumes():
    plan = _plan(
        available_slots=2,
        land_slots=3,
        resumes=[_due(3), _due(5), _due(6), _pending(9)],
        lands=MODEL_LAND,
        launches=LAUNCH,
        closeness={6: REVIEW},
    )
    assert plan.actions == [rules.PollAction("resume", 6), rules.PollAction("resume", 3)]
    assert plan.deferred == [rules.Deferred("land", 4, "pause_waiting")]


def test_row_account_paused_with_fewer_resumes_than_slots_starts_them_all():
    plan = _plan(
        available_slots=3,
        land_slots=4,
        resumes=[_due(3), _pending(9)],
        lands=MODEL_LAND,
    )
    assert plan.actions == [rules.PollAction("resume", 3)]
    assert plan.deferred == [rules.Deferred("land", 4, "pause_waiting")]


def test_row_no_headroom_and_no_slot_defers_the_land_and_starts_nothing():
    plan = _plan(
        available_slots=0,
        land_slots=0,
        retakes=[2],
        resumes=[_due(3)],
        lands=MODEL_LAND,
        launches=LAUNCH,
    )
    assert plan.actions == []
    assert plan.deferred == [rules.Deferred("land", 4, "no_slot")]


def test_row_more_slots_than_resumes_the_land_and_every_resume_take_regular_slots():
    plan = _plan(
        available_slots=3,
        land_slots=4,
        resumes=[_due(3), _due(5)],
        lands=MODEL_LAND,
        launches=LAUNCH,
    )
    assert plan.actions == [
        rules.PollAction("land", 4),
        rules.PollAction("resume", 3),
        rules.PollAction("resume", 5),
    ]
    assert plan.deferred == []


def test_row_more_slots_than_resumes_the_launch_sees_what_the_land_and_resumes_left():
    resumes = [_due(3), _due(5)]
    full = _plan(
        available_slots=3, land_slots=4, resumes=resumes, lands=MODEL_LAND, launches=LAUNCH
    )
    spare = _plan(
        available_slots=4, land_slots=5, resumes=resumes, lands=MODEL_LAND, launches=LAUNCH
    )
    assert rules.PollAction("launch", 80) not in full.actions
    assert spare.actions[-1] == rules.PollAction("launch", 80)


def test_row_headroom_above_the_slots_starts_the_land_on_it_and_every_slot_on_a_resume():
    plan = _plan(
        available_slots=1,
        land_slots=2,
        resumes=[_due(3), _due(5)],
        lands=MODEL_LAND,
        launches=LAUNCH,
        closeness={3: DEFINITION, 5: DEVELOPMENT},
    )
    assert plan.actions == [rules.PollAction("land", 4), rules.PollAction("resume", 5)]
    assert plan.deferred == []


def test_row_headroom_equal_to_the_slots_the_land_takes_one_regular_slot_from_the_resumes():
    plan = _plan(
        available_slots=2,
        land_slots=2,
        resumes=[_due(3), _due(5)],
        lands=MODEL_LAND,
        launches=LAUNCH,
        closeness={3: DEFINITION, 5: DEVELOPMENT},
    )
    assert plan.actions == [rules.PollAction("land", 4), rules.PollAction("resume", 5)]
    assert plan.deferred == []


def test_one_slot_with_a_headroom_starts_both_a_resume_and_the_land():
    plan = _plan(
        available_slots=1, land_slots=2, resumes=[_due(3)], lands=MODEL_LAND, launches=LAUNCH
    )
    assert plan.actions == [rules.PollAction("land", 4), rules.PollAction("resume", 3)]


def test_retakes_never_use_the_headroom():
    plan = _plan(
        available_slots=1,
        land_slots=2,
        retakes=[2, 7],
        lands=MODEL_LAND,
        closeness={7: REVIEW},
    )
    assert plan.actions == [rules.PollAction("land", 4), rules.PollAction("retake", 7)]


@pytest.mark.parametrize("slots", [0, 1])
def test_at_most_one_land_starts_per_round(slots):
    lands = [rules.LandCandidate(9, "land_model"), rules.LandCandidate(4, "land_model")]
    plan = _plan(available_slots=slots, land_slots=slots + 1, lands=lands)
    assert [a for a in plan.actions if a.kind == "land"] == [rules.PollAction("land", 4)]
    assert plan.deferred == [rules.Deferred("land", 9, "no_slot")]
