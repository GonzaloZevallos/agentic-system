"""GH-839: `rules.poll_plan` and the per-issue hold -- a held issue is not launched, resumed or
retaken, it is deferred `held`, and what the hold leaves out never changes another issue's plan."""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import product
from typing import get_args

import pytest

from agentic.core.process.application import poll
from agentic.core.process.domain import rules

NOW = datetime(2026, 10, 3, 20, 0)


def _cand(issue: int, *, due: bool = True) -> rules.LaunchCandidate:
    since = None if due else NOW - timedelta(seconds=1)
    return rules.LaunchCandidate(issue, False, since, 3, 6)


def _waiting(issue: int, reason: rules.ResumeKey = "upstream") -> rules.ResumeCandidate:
    """A resume still waiting out its calendar."""
    return rules.ResumeCandidate(issue, reason, NOW - timedelta(minutes=1), 1, 24)


def _due(issue: int) -> rules.ResumeCandidate:
    return rules.ResumeCandidate(issue, "upstream", NOW - timedelta(hours=3), 1, 24)


def _plan(**kwargs) -> rules.PollPlan:
    base: dict = {"closes": [], "resumes": [], "launches": [], "now": NOW, "available_slots": 3}
    return rules.poll_plan(**{**base, **kwargs})


def _actions(plan: rules.PollPlan) -> list[tuple[str, int]]:
    return [(a.kind, a.issue) for a in plan.actions]


def _deferred(plan: rules.PollPlan) -> list[tuple[str, int, str]]:
    return [(d.kind, d.issue, d.reason) for d in plan.deferred]


def test_a_held_launch_is_deferred_held_and_the_same_issue_released_is_launched():
    held = _plan(launches=[_cand(4)], held_issues=frozenset({4}))
    assert _actions(held) == []
    assert _deferred(held) == [("launch", 4, "held")]
    released = _plan(launches=[_cand(4)], held_issues=frozenset())
    assert _actions(released) == [("launch", 4)]
    assert _deferred(released) == []


def test_a_held_launch_does_not_take_the_slot_of_a_released_one():
    plan = _plan(launches=[_cand(4), _cand(5)], available_slots=1, held_issues=frozenset({4}))
    assert _actions(plan) == [("launch", 5)]
    assert _deferred(plan) == [("launch", 4, "held")]


def test_a_held_resume_is_deferred_held_and_starts_nothing():
    plan = _plan(resumes=[_due(7), _due(8)], held_issues=frozenset({7}))
    assert _actions(plan) == [("resume", 8)]
    assert _deferred(plan) == [("resume", 7, "held")]


@pytest.mark.parametrize("why", ["retakes", "crash_retakes"])
def test_a_held_retake_reboot_or_crash_is_deferred_held_and_ordered_to_nobody(why):
    plan = _plan(**{why: [7, 8]}, held_issues=frozenset({7}))
    assert [(k, i) for k, i in _actions(plan)] == [("retake", 8)]
    assert _deferred(plan) == [("retake", 7, "held")]


def test_a_held_orphan_is_deferred_held_and_a_released_one_is_retaken():
    plan = _plan(orphan_retakes=[7, 8], held_issues=frozenset({7}))
    assert _actions(plan) == [("retake", 8)]
    assert _deferred(plan) == [("orphan", 7, "held")]


def test_a_held_issue_that_is_also_schedule_gated_is_deferred_only_as_held():
    plan = _plan(launches=[_cand(4, due=False)], held_issues=frozenset({4}))
    assert _deferred(plan) == [("launch", 4, "held")]


def test_a_held_calendar_resume_still_waiting_stops_no_other_launch():
    plan = _plan(resumes=[_waiting(7)], launches=[_cand(4)], held_issues=frozenset({7}))
    assert _actions(plan) == [("launch", 4)]
    assert _deferred(plan) == [("resume", 7, "held")]


def test_a_held_resume_waiting_does_not_defer_a_launch_it_would_without_the_hold():
    """Rejected input: the same resume not held does stop the launch."""
    plan = _plan(resumes=[_waiting(7)], launches=[_cand(4)])
    assert _actions(plan) == []
    assert _deferred(plan) == [("launch", 4, "resume_waiting")]


def test_a_held_account_limit_pause_still_stops_the_launches_and_the_model_lands():
    plan = _plan(
        resumes=[_waiting(7, "account_limit")],
        launches=[_cand(4)],
        lands=[rules.LandCandidate(9, "land_model")],
        held_issues=frozenset({7}),
    )
    assert _actions(plan) == []
    assert ("launch", 4, "resume_waiting") in _deferred(plan)
    assert ("land", 9, "pause_waiting") in _deferred(plan)
    assert ("resume", 7, "held") in _deferred(plan)


def test_a_round_held_by_volumes_says_space_held_never_held():
    plan = _plan(launches=[_cand(4)], held=("scratch",), held_issues=frozenset({4}))
    assert _deferred(plan) == [("launch", 4, "space_held")]


def test_an_unread_hold_leaves_the_closes_and_the_notices_and_defers_nothing():
    plan = _plan(
        closes=[3],
        spent_retakes=[6],
        launches=[_cand(4)],
        resumes=[_due(7)],
        retakes=[8],
        crash_retakes=[9],
        orphan_retakes=[10],
        merges=[11],
        lands=[rules.LandCandidate(12, "land_model")],
        holds_unread=True,
    )
    assert _actions(plan) == [("close", 3), ("retakes_spent", 6)]
    assert plan.deferred == []


def test_a_held_issue_does_not_hold_a_close_or_a_notice():
    plan = _plan(closes=[4], spent_retakes=[4], held_issues=frozenset({4}))
    assert _actions(plan) == [("close", 4), ("retakes_spent", 4)]


KINDS = get_args(rules.DeferredKind)
REASONS = get_args(rules.DeferredReason)
LEGAL = [(k, r) for k, r in product(KINDS, REASONS) if r in rules.DEFERRED_REASONS[k]]
ILLEGAL = [(k, r) for k, r in product(KINDS, REASONS) if r not in rules.DEFERRED_REASONS[k]]


def _line(deferred: rules.Deferred) -> str:
    """The line the poller prints for it, by the builder its path uses."""
    if deferred.reason == "held":
        return poll.held_line(deferred)
    if deferred.kind == "orphan":
        return rules.orphaned_line(deferred.issue, "no_slot")
    if deferred.kind == "launch":
        return f"agentic: poll: launch #{deferred.issue} waiting ({deferred.reason})"
    return poll.deferred_line(deferred)


@pytest.mark.parametrize(("kind", "reason"), LEGAL)
def test_every_legal_pair_builds_answers_holds_launches_and_names_its_reason(kind, reason):
    deferred = rules.Deferred(kind, 7, reason)
    expected = kind == "land" and reason in ("no_slot", "land_queued")
    assert rules.holds_launches(deferred) is expected
    line = _line(deferred)
    if reason == "held":
        assert line == f"agentic: poll: {kind} #7 waiting (held)"
    elif kind != "orphan":
        assert reason in line
    assert "#7" in line


@pytest.mark.parametrize(("kind", "reason"), ILLEGAL)
def test_every_illegal_pair_is_refused_at_construction(kind, reason):
    with pytest.raises(ValueError, match="no deferral"):
        rules.Deferred(kind, 7, reason)


def test_land_and_merge_never_carry_held():
    assert ("land", "held") in ILLEGAL and ("merge", "held") in ILLEGAL


def test_every_deferred_a_busy_round_returns_is_a_listed_pair():
    plan = _plan(
        closes=[1],
        resumes=[_waiting(7, "account_limit"), _due(8), _due(20)],
        launches=[_cand(4), _cand(5, due=False), _cand(6)],
        retakes=[9],
        crash_retakes=[10],
        orphan_retakes=[11, 21],
        merges=[12, 13],
        backed_off_merges=[13],
        lands=[rules.LandCandidate(14, "land_model"), rules.LandCandidate(15, "land_checks")],
        available_slots=1,
        held_issues=frozenset({4, 8, 9, 10, 11}),
    )
    assert plan.deferred
    for d in plan.deferred:
        assert d.reason in rules.DEFERRED_REASONS[d.kind]
