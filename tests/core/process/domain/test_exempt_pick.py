"""GH-500 (ADR-0045 addendum): `rules.poll_plan` picks the one exempt child by how long each
candidate has waited since its last attempt, not by issue number, and defers a backed-off
merge as `backoff`."""

from datetime import UTC, datetime, timedelta

from agentic.core.process.domain import exempt_queue as eq
from agentic.core.process.domain import rules

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


def _plan(**kwargs) -> rules.PollPlan:
    base: dict = {"closes": [], "resumes": [], "launches": [], "available_slots": 0, "now": NOW}
    return rules.poll_plan(**{**base, **kwargs})


def _attempts(**by_key: datetime) -> dict:
    """`m478=...` is the merge of #478, `l4=...` the land of #4."""
    return {
        (int(name[1:]), "merge" if name[0] == "m" else "land"): eq.ExemptAttempt(last_attempt=at)
        for name, at in by_key.items()
    }


def test_a_merge_that_just_refused_does_not_win_the_slot_again():
    # 2026-11-04: #478 refused `merge_conflict` every round and took the slot from #487 and #492.
    plan = _plan(
        merges=[478, 487, 492], exempt_attempts=_attempts(m478=NOW - timedelta(seconds=30))
    )
    assert plan.actions == [rules.PollAction("merge", 487)]
    assert plan.deferred == [
        rules.Deferred("merge", 478, "exempt_busy"),
        rules.Deferred("merge", 492, "exempt_busy"),
    ]


def test_the_refused_merge_comes_back_once_the_others_have_been_tried_after_it():
    attempts = _attempts(
        m478=NOW - timedelta(minutes=3),
        m487=NOW - timedelta(minutes=2),
        m492=NOW - timedelta(minutes=1),
    )
    assert _plan(merges=[478, 487, 492], exempt_attempts=attempts).actions == [
        rules.PollAction("merge", 478)
    ]


def test_without_attempt_records_the_pick_is_todays_all_merges_then_lands_lowest_first():
    lands = [rules.LandCandidate(2, "land_checks")]
    assert _plan(merges=[9, 7], lands=lands).actions == [rules.PollAction("merge", 7)]
    assert _plan(lands=[rules.LandCandidate(9, "land_checks"), *lands]).actions == [
        rules.PollAction("land", 2)
    ]


def test_a_land_that_waited_longer_than_a_merges_last_attempt_starts_first():
    attempts = _attempts(m7=NOW - timedelta(minutes=1), l4=NOW - timedelta(minutes=10))
    plan = _plan(
        merges=[7], lands=[rules.LandCandidate(4, "land_checks")], exempt_attempts=attempts
    )
    assert plan.actions == [rules.PollAction("land", 4)]
    assert plan.deferred == [rules.Deferred("merge", 7, "exempt_busy")]


def test_a_land_attempted_more_recently_than_a_merge_goes_after_it():
    attempts = _attempts(l4=NOW - timedelta(minutes=1))
    plan = _plan(
        merges=[7], lands=[rules.LandCandidate(4, "land_checks")], exempt_attempts=attempts
    )
    assert plan.actions == [rules.PollAction("merge", 7)]


def test_records_of_the_other_kind_of_the_same_issue_do_not_mix():
    attempts = _attempts(l7=NOW)
    plan = _plan(merges=[7, 8], exempt_attempts=attempts)
    assert plan.actions == [rules.PollAction("merge", 7)]


def test_a_backed_off_merge_is_deferred_backoff_and_never_starts():
    plan = _plan(merges=[478, 487], backed_off_merges=[478])
    assert plan.actions == [rules.PollAction("merge", 487)]
    assert plan.deferred == [rules.Deferred("merge", 478, "backoff")]


def test_with_only_backed_off_merges_nothing_starts():
    plan = _plan(merges=[478], backed_off_merges=[478])
    assert plan.actions == []
    assert plan.deferred == [rules.Deferred("merge", 478, "backoff")]


def test_a_backed_off_merge_does_not_hold_a_live_exempt_slot_reason():
    plan = _plan(merges=[478, 487], backed_off_merges=[478], live_exempt=1)
    assert plan.actions == []
    assert plan.deferred == [
        rules.Deferred("merge", 478, "backoff"),
        rules.Deferred("merge", 487, "exempt_busy"),
    ]


def test_a_yielding_product_starts_no_exempt_child_and_everything_is_busy():
    plan = _plan(merges=[7], lands=[rules.LandCandidate(4, "land_checks")], exempt_yield=True)
    assert plan.actions == []
    assert plan.deferred == [
        rules.Deferred("merge", 7, "exempt_busy"),
        rules.Deferred("land", 4, "exempt_busy"),
    ]


def test_a_yield_leaves_the_children_that_take_a_slot_alone():
    plan = _plan(
        available_slots=2,
        retakes=[2],
        merges=[7],
        lands=[rules.LandCandidate(4, "land_model")],
        exempt_yield=True,
    )
    assert plan.actions == [rules.PollAction("land", 4), rules.PollAction("retake", 2)]
    assert plan.deferred == [rules.Deferred("merge", 7, "exempt_busy")]
