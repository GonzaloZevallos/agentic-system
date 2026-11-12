"""`landing_order.head` (GH-588): which delivered pull request of a product leads its landing
order. Pure: members in, the head's issue out."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentic.core.process.domain.landing_order import Member, head, turn_of

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _member(
    issue: int,
    at: int | None,
    *,
    live_pass: bool = False,
    human: bool = False,
) -> Member:
    """`at` is minutes after `T0`; `None` is an unstamped member."""
    queued = None if at is None else T0 + timedelta(minutes=at)
    return Member(issue, queued, live_pass, human)


def test_the_oldest_stamp_leads():
    assert head([_member(5, 30), _member(9, 10), _member(2, 20)]) == 9


def test_equal_stamps_are_broken_by_issue():
    assert head([_member(7, 10), _member(3, 10), _member(5, 10)]) == 3


def test_unstamped_members_go_after_every_stamped_one_by_issue():
    members = [_member(1, None), _member(8, 500), _member(2, None)]
    assert head(members) == 8
    assert head([_member(4, None), _member(2, None)]) == 2


def test_a_live_pass_beats_an_older_stamp():
    assert head([_member(1, 0), _member(2, 60, live_pass=True)]) == 2


def test_the_earliest_of_several_live_passes_leads():
    members = [_member(1, 0), _member(3, 40, live_pass=True), _member(2, 20, live_pass=True)]
    assert head(members) == 2


def test_a_member_waiting_on_a_human_is_skipped_even_with_a_live_pass_and_the_oldest_stamp():
    members = [
        _member(1, 0, live_pass=True, human=True),
        _member(2, 10, human=True),
        _member(3, 20),
        _member(4, 30),
    ]
    assert head(members) == 3


def test_nobody_eligible_has_no_head():
    assert head([]) is None
    assert head([_member(1, 0, human=True), _member(2, None, live_pass=True, human=True)]) is None


def test_the_input_order_does_not_matter():
    members = [_member(3, 20), _member(1, None), _member(2, 5, live_pass=True), _member(4, 1)]
    assert head(members) == head(list(reversed(members))) == 2


def test_turn_of_names_the_head_and_everybody_else_waits():
    assert turn_of(4, 4) == "head"
    assert turn_of(5, 4) == "waiting"
    assert turn_of(5, None) == "waiting"
