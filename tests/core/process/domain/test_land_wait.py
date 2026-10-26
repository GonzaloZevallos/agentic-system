"""GH-362, Rule 1 (ADR-0045 addendum): Espera de aterrizaje in the domain. `holds_launches`
says which deferrals open a wait, `poll_plan(others_landing=True)` holds every counted start
below a land, and `land_wait_since` keeps the start of a wait across rounds."""

from datetime import UTC, datetime, timedelta
from typing import get_args, get_type_hints

import pytest

from agentic.core.process.domain import rules

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
LAUNCH = [rules.LaunchCandidate(8, False, None, 0, 3)]
MODEL_LAND = [rules.LandCandidate(4, "land_model")]
DUE = rules.ResumeCandidate(
    issue=3, reason="upstream", since=NOW - timedelta(days=1), prior=1, limit=5
)


def _plan(**kwargs) -> rules.PollPlan:
    base: dict = {"closes": [], "resumes": [], "launches": [], "now": NOW}
    return rules.poll_plan(**{**base, **kwargs})


# Every value `Deferred.reason` can take, classed: a new reason fails here until it is.
HOLDS = {"no_slot": True, "exempt_busy": False, "pause_waiting": False}


def test_every_deferred_reason_is_classed_explicitly():
    reasons = get_args(get_type_hints(rules.Deferred)["reason"])
    assert set(reasons) == set(HOLDS)


@pytest.mark.parametrize(("reason", "holds"), sorted(HOLDS.items()))
def test_a_deferred_land_holds_launches_only_for_the_reasons_that_open_a_wait(reason, holds):
    assert rules.holds_launches(rules.Deferred("land", 4, reason)) is holds


def test_a_deferred_merge_never_opens_a_wait():
    assert rules.holds_launches(rules.Deferred("merge", 4, "exempt_busy")) is False


def test_a_launch_is_held_while_others_land():
    free = _plan(available_slots=2, launches=LAUNCH)
    held = _plan(available_slots=2, launches=LAUNCH, others_landing=True)
    assert free.actions == [rules.PollAction("launch", 8)]
    assert held.actions == []


def test_a_due_resume_is_held_while_others_land():
    held = _plan(available_slots=2, resumes=[DUE], others_landing=True)
    assert _plan(available_slots=2, resumes=[DUE]).actions == [rules.PollAction("resume", 3)]
    assert held.actions == []


def test_a_retake_and_a_crash_retake_are_held_while_others_land():
    kwargs = {"available_slots": 2, "retakes": [2], "crash_retakes": [3]}
    assert len(_plan(**kwargs).actions) == 2
    assert _plan(**kwargs, others_landing=True).actions == []


def test_the_own_land_close_and_exempt_merge_are_not_held():
    plan = _plan(
        available_slots=2,
        land_slots=3,
        closes=[1],
        lands=MODEL_LAND,
        merges=[6],
        launches=LAUNCH,
        resumes=[DUE],
        others_landing=True,
    )
    assert plan.actions == [
        rules.PollAction("close", 1),
        rules.PollAction("land", 4),
        rules.PollAction("merge", 6),
    ]


def test_a_land_with_no_slot_is_still_deferred_while_others_land():
    plan = _plan(available_slots=0, land_slots=0, lands=MODEL_LAND, others_landing=True)
    assert plan.deferred == [rules.Deferred("land", 4, "no_slot")]


def test_the_start_of_a_wait_is_kept_while_it_lasts():
    began = NOW - timedelta(minutes=5)
    assert rules.land_wait_since(began, True, NOW) == began


def test_a_wait_with_no_previous_record_starts_now():
    assert rules.land_wait_since(None, True, NOW) == NOW


def test_no_waiting_land_means_no_record_whatever_the_previous_was():
    assert rules.land_wait_since(NOW - timedelta(minutes=5), False, NOW) is None
