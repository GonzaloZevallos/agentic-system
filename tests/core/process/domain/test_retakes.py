"""The single retake budget of a task (GH-466): count, reset, calendar, stale, notice."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.domain import retakes

T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
HUMAN_RESUMED = "Resumed: "
SYSTEM = (
    "Escalated: ",
    HUMAN_RESUMED,
    *retakes.RETAKE_PREFIXES,
    retakes.RETAKES_SPENT,
    retakes.RESUMED_BY_POLLER,
)

REBOOT = f"{retakes.RETAKEN}previous boot"
CRASH = f"{retakes.RETAKEN_AFTER_CRASH}run raised under a; resuming under b (retake 1 of 3)"
ORPHAN = f"{retakes.RETAKEN_ORPHANED}retake 1 of 3"


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _budget(*bodies: str) -> retakes.Budget:
    comments = [(body, _at(i * 10)) for i, body in enumerate(bodies)]
    return retakes.budget(comments, SYSTEM, HUMAN_RESUMED)


def test_the_cap_is_three_and_the_prefixes_collide_with_nothing():
    assert retakes.RETAKE_MAX == 3
    everything = (*retakes.RETAKE_PREFIXES, retakes.RETAKES_SPENT, retakes.RESUMED_BY_POLLER)
    for one in everything:
        for other in (*everything, HUMAN_RESUMED):
            assert one == other or not one.startswith(other), (one, other)
        assert one in SYSTEM


def test_a_reboot_a_crash_and_an_orphan_retake_share_one_budget_and_spend_it():
    used = _budget(REBOOT, CRASH, ORPHAN)
    assert used == retakes.Budget(3, _at(0), _at(20))
    assert retakes.spent(used)
    assert not retakes.spent(_budget(REBOOT, CRASH))


def test_legacy_retake_comments_count_toward_the_budget():
    legacy = "Retaken after reboot: previous boot"
    assert _budget(legacy, legacy, legacy).used == 3


def test_only_a_comment_that_starts_with_a_retake_prefix_counts():
    quoted = f"The owner wrote: {retakes.RETAKEN}previous boot"
    assert _budget(quoted).used == 0
    assert _budget(f" {REBOOT}", REBOOT).used == 1


def test_nothing_used_is_an_empty_budget():
    assert _budget() == retakes.Budget(0, None, None)
    assert _budget("a human decision") == retakes.Budget(0, None, None)


@pytest.mark.parametrize("reset", ["a human comment", "Resumed: by a human"])
def test_a_human_comment_and_a_human_resume_reset_the_budget(reset):
    assert _budget(REBOOT, CRASH, reset).used == 0
    assert _budget(REBOOT, CRASH, reset, ORPHAN).used == 1


@pytest.mark.parametrize(
    "not_a_reset",
    [retakes.RESUMED_BY_POLLER + "retake", retakes.RETAKES_SPENT + "x", REBOOT, CRASH, ORPHAN],
)
def test_the_systems_own_comments_never_reset_the_budget(not_a_reset):
    assert _budget(REBOOT, not_a_reset).used >= 1
    assert _budget(REBOOT, REBOOT, not_a_reset).used >= 2


def test_reset_index_is_the_last_reset_or_minus_one():
    bodies = ["human", REBOOT, "Resumed: x", REBOOT, retakes.RESUMED_BY_POLLER + "y"]
    assert retakes.reset_index(bodies, SYSTEM, HUMAN_RESUMED) == 2
    assert retakes.reset_index([REBOOT], SYSTEM, HUMAN_RESUMED) == -1
    assert retakes.reset_index([], SYSTEM, HUMAN_RESUMED) == -1


def test_the_first_retake_waits_for_its_delay_and_the_others_follow_the_calendar():
    zero = timedelta(0)
    assert retakes.due(_budget(), T0, zero) == T0
    assert retakes.due(_budget(), T0, timedelta(hours=1)) == T0 + timedelta(hours=1)
    one = retakes.Budget(1, T0, T0)
    assert retakes.due(one, _at(500), timedelta(hours=1)) == T0 + timedelta(minutes=30)
    two = retakes.Budget(2, T0, _at(40))
    assert retakes.due(two, T0, zero) == _at(40 + 60)
    assert retakes.RETAKE_SCHEDULE[1] == 30


def test_a_document_is_stale_after_a_day_and_not_before_and_never_when_unknown():
    assert retakes.stale(T0 - timedelta(hours=25), T0)
    assert not retakes.stale(T0 - timedelta(hours=23), T0)
    assert not retakes.stale(None, T0)


def test_the_spent_notice_counts_only_after_the_reset_boundary():
    notice = retakes.RETAKES_SPENT + "crashed, 3 retakes"
    assert retakes.notice_posted([REBOOT, notice], SYSTEM, HUMAN_RESUMED)
    assert not retakes.notice_posted([REBOOT, CRASH], SYSTEM, HUMAN_RESUMED)
    assert not retakes.notice_posted([notice, "human answer"], SYSTEM, HUMAN_RESUMED)
    assert not retakes.notice_posted([notice, "Resumed: by a human"], SYSTEM, HUMAN_RESUMED)
    assert retakes.notice_posted([notice, retakes.RESUMED_BY_POLLER + "r"], SYSTEM, HUMAN_RESUMED)
    assert not retakes.notice_posted([], SYSTEM, HUMAN_RESUMED)
