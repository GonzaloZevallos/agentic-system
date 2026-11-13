"""`landing_order.head` (GH-588): which delivered pull request of a product leads its landing
order. Pure: members in, the head's issue out."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.landing_order import (
    GAP_MAX,
    HEAD_BUDGET,
    HEAD_SEEN_EVERY,
    LANE_SYNCS_MIN,
    YIELD_MARK,
    LaneReason,
    Member,
    advance,
    head,
    head_time,
    holds_turn,
    instant,
    integrates,
    lane_ready,
    lane_reason,
    left_head,
    observed,
    order,
    over_budget,
    resumed,
    turn_of,
    turn_patch,
    unproductive_steps,
    yielded,
)

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


# --- GH-621: the head budget -------------------------------------------------------------------

NOW = T0 + timedelta(hours=5)
SEC = timedelta(seconds=1)


def _m(issue: int, **kw) -> Member:
    """A member queued `issue` minutes after `T0`, with the facts in `kw`."""
    base = {"queued_at": T0 + timedelta(minutes=issue), "live_pass": False, "waits_on_human": False}
    return Member(issue, **{**base, **kw})


def _head_before(members: list[Member]) -> int | None:
    """A literal copy of `head` as GH-588 left it, to prove the default path did not move."""

    def first(ms):
        ordered = sorted(
            ms, key=lambda m: (m.queued_at is None, m.queued_at or datetime.min, m.issue)
        )
        return ordered[0] if ordered else None

    eligible = [m for m in members if not m.waits_on_human]
    chosen = first(m for m in eligible if m.live_pass) or first(eligible)
    return chosen.issue if chosen is not None else None


@pytest.mark.parametrize(
    "members",
    [
        [_member(5, 30), _member(9, 10), _member(2, 20)],
        [_member(7, 10), _member(3, 10)],
        [_member(1, None), _member(8, 500), _member(2, None)],
        [_member(1, 0), _member(3, 40, live_pass=True), _member(2, 20, live_pass=True)],
        [_member(1, 0, live_pass=True, human=True), _member(4, 30)],
        [],
    ],
)
def test_default_facts_give_the_head_of_before(members):
    assert head(members) == _head_before(members) == head(members, NOW) == order(members, NOW).head


def test_a_stuck_head_yields_at_the_budget_and_not_before():
    a = _m(1, seen_at=NOW - GAP_MAX, charged_s=(HEAD_BUDGET - GAP_MAX).total_seconds())
    before = order([a, _m(2)], NOW - SEC)
    assert before.head == 1 and before.yielded == ()
    result = order([a, _m(2)], NOW)
    assert result.head == 2
    assert result.yielded[0].reason == "budget_time"
    assert result.yielded[0].member == yielded(a, "budget_time", NOW)
    assert result.yielded[0].member.charged_s == 0.0 and result.yielded[0].member.seen_at is None


def test_time_skipped_is_not_charged_and_a_gap_is_capped():
    m = _m(1)
    for minute in (0, 5, 10, 15, 20):
        m = observed(m, T0 + timedelta(minutes=minute))
    m = left_head(m, T0 + timedelta(minutes=20))
    assert m.charged_s == 1200 and m.seen_at is None
    back = T0 + timedelta(minutes=50)
    m = observed(m, back)
    assert head_time(m, back) == timedelta(minutes=20)
    for minute in (5, 10, 15, 20, 25, 30):
        m = observed(m, back + timedelta(minutes=minute))
    end = back + timedelta(minutes=30)
    assert head_time(m, end) == timedelta(minutes=50)
    assert order([m, _m(2)], end).yielded[0].reason == "budget_time"
    assert head_time(_m(3, seen_at=T0), T0 + timedelta(hours=3)) == GAP_MAX


def test_turn_at_is_anchored_once_kept_on_leaving_and_reset_by_a_yield():
    first = observed(_m(1), T0)
    assert first.turn_at == T0
    assert observed(first, T0 + timedelta(minutes=5)).turn_at == T0
    assert observed(left_head(first, T0 + timedelta(minutes=5)), NOW).turn_at == T0
    again = observed(yielded(first, "budget_time", NOW), NOW)
    assert again.turn_at == NOW
    entries = [
        {
            "head": "s",
            "at": (NOW - timedelta(minutes=9)).isoformat(),
            "step": "x",
            "closed_at": "c",
        },
        {
            "head": "s",
            "at": (NOW + timedelta(minutes=1)).isoformat(),
            "step": "a",
            "closed_at": "c",
        },
        {
            "head": "s",
            "at": (NOW + timedelta(minutes=2)).isoformat(),
            "step": "b",
            "closed_at": "c",
        },
    ]
    assert unproductive_steps(entries, "s", again.turn_at) == 2


def test_a_lone_member_never_yields():
    lone = _m(1, charged_s=HEAD_BUDGET.total_seconds() * 2, steps_on_head=5)
    result = order([lone], NOW)
    assert result.head == 1 and result.yielded == ()
    assert order([lone, _m(2, waits_on_human=True)], NOW).yielded == ()


def test_after_a_yield_the_member_is_not_over_budget_as_head_again_and_holds_until_the_budget():
    a = yielded(_m(1, busy=False, charged_s=HEAD_BUDGET.total_seconds()), "budget_time", NOW)
    assert over_budget(a, NOW) is None
    only = [a]
    assert order(only, NOW).head == 1
    steps = yielded(_m(1, steps_on_head=2), "budget_steps", NOW)
    assert over_budget(steps, NOW) is None
    # as head again, observed every five minutes until one second short of the budget
    seen = observed(a, NOW)
    for minute in range(5, 45, 5):
        seen = observed(seen, NOW + timedelta(minutes=minute))
    end = NOW + HEAD_BUDGET - SEC
    assert over_budget(seen, end) is None
    assert over_budget(seen, NOW + HEAD_BUDGET) == "budget_time"
    busy = replace(seen, busy=True)
    assert holds_turn(busy, NOW + timedelta(minutes=40)) and not holds_turn(
        busy, NOW + timedelta(hours=2)
    )


def test_a_second_time_yield_goes_behind_a_first_time_yield():
    first = yielded(_m(1), "budget_time", NOW)
    second = yielded(yielded(_m(2), "budget_time", NOW - timedelta(hours=1)), "budget_time", NOW)
    assert order([second, first], NOW).head == 1
    # same yields: the older yielded_at, then the issue
    old = replace(first, issue=5, yielded_at=NOW - timedelta(minutes=1))
    assert order([first, old], NOW).head == 5
    tie = replace(first, issue=0)
    assert order([first, tie], NOW).head == 0


def test_a_yielded_member_sits_behind_an_unyielded_one_even_with_a_fresher_stamp():
    gave_up = yielded(_m(1, live_pass=True), "budget_time", NOW)
    late = Member(9, None, False, False)
    assert order([gave_up, late], NOW).head == 9


def test_yielded_at_set_or_not_sorts_without_a_type_error():
    plain = _m(1, yields=1)
    gave_up = yielded(_m(2), "budget_time", NOW)
    assert order([gave_up, plain], NOW).head == 1
    assert order([plain, gave_up], NOW).head == 1


def test_two_fresh_busy_members_have_the_same_head_in_either_input_order():
    a, b = _m(1, busy=True, seen_at=NOW), _m(2, busy=True, seen_at=NOW)
    assert order([a, b], NOW).head == order([b, a], NOW).head == 1


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"busy": True, "seen_at": NOW, "waits_on_ci": True}, 1),
        ({"waits_on_ci": True}, 2),
        ({"account_paused": True}, 2),
        ({"busy": True, "seen_at": NOW, "account_paused": True}, 1),
    ],
)
def test_busy_beats_the_drop_for_ci_and_the_paused_account(kw, expected):
    assert order([_m(1, **kw), _m(2)], NOW).head == expected


def test_an_old_anchor_does_not_beat_the_busy_member_seen_a_moment_ago():
    a = _m(1, busy=True, turn_at=T0, seen_at=None)
    b = _m(2, busy=True, seen_at=NOW - timedelta(minutes=3))
    assert order([a, b], NOW).head == 2


def test_a_stale_busy_member_stays_head_and_does_not_yield():
    stale = _m(1, busy=True, live_pass=True, seen_at=T0, charged_s=HEAD_BUDGET.total_seconds())
    result = order([stale, _m(2)], NOW)
    assert result.head == 1 and result.yielded == ()
    # a seen_at that could not be read is None: the same member, not holding the turn
    unread = replace(stale, seen_at=None)
    assert order([unread, _m(2)], NOW).head == 1
    over = replace(unread, busy=False)
    assert order([over, _m(2)], NOW).head == 2


def test_a_yielded_live_pass_that_is_not_busy_waits_behind_the_unyielded():
    a = _m(1, live_pass=True, charged_s=HEAD_BUDGET.total_seconds())
    result = order([a, _m(2)], NOW)
    assert result.head == 2 and result.yielded[0].member.live_pass


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"charged_s": 2700.0}, "budget_time"),
        ({"charged_s": 2700.0, "merge_due": True}, None),
        ({"charged_s": 2700.0, "busy": True}, None),
        ({"steps_on_head": 2}, "budget_steps"),
        ({"steps_on_head": 1}, None),
        ({"charged_s": 2700.0, "steps_on_head": 2}, "budget_time"),
    ],
)
def test_over_budget_table(kw, reason):
    assert over_budget(_m(1, **kw), NOW) == reason


def test_a_future_seen_at_is_clamped_to_zero():
    assert head_time(_m(1, charged_s=30.0, seen_at=NOW + timedelta(hours=1)), NOW) == timedelta(
        seconds=30
    )


@pytest.mark.parametrize(
    "case",
    ["stays", "becomes", "loses", "skipped", "ineligible", "yields", "lone"],
)
def test_advance_folds_each_member_by_what_happened_to_it(case):
    seen = NOW - timedelta(minutes=5)
    a = _m(1, seen_at=seen, turn_at=seen, charged_s=60.0)
    b = _m(2)
    if case == "stays":
        result, folded = advance([a, b], NOW)
        assert result.head == 1 and folded == (observed(a, NOW), b)
        assert folded[0].charged_s == 360.0
    elif case == "becomes":
        result, folded = advance([b, _m(3)], NOW)
        assert result.head == 2 and folded == (observed(b, NOW), _m(3))
    elif case == "loses":
        older = _m(0, live_pass=False, queued_at=T0 - timedelta(days=1))
        result, folded = advance([a, older], NOW)
        assert result.head == 0 and folded == (left_head(a, NOW), observed(older, NOW))
        assert folded[0].seen_at is None and folded[0].turn_at == seen
    elif case == "skipped":
        result, folded = advance([replace(a, waits_on_ci=True), b], NOW)
        assert result.head == 2 and folded[0].seen_at is None and folded[0].charged_s == 360.0
    elif case == "ineligible":
        result, folded = advance([replace(a, waits_on_human=True), b], NOW)
        assert result.head == 2 and folded[0].seen_at is None
    elif case == "yields":
        over = replace(a, charged_s=HEAD_BUDGET.total_seconds())
        result, folded = advance([over, b], NOW)
        assert result.head == 2 and folded[0] == yielded(over, "budget_time", NOW)
        assert folded[1].turn_at == NOW and folded[1].seen_at == NOW
    else:
        result, folded = advance([a], NOW)
        assert result.head == 1 and folded == (observed(a, NOW),)


def test_a_chain_of_over_budget_heads_yields_in_one_call_and_is_idempotent():
    spent = HEAD_BUDGET.total_seconds()
    members = [_m(1, charged_s=spent), _m(2, charged_s=spent)]
    result, folded = advance(members, NOW)
    assert [y.member.issue for y in result.yielded] == [1, 2]
    assert result.head == 1
    assert folded[0].turn_at == NOW and folded[0].seen_at == NOW and folded[0].yields == 1
    assert folded[1].seen_at is None and folded[1].yields == 1
    again = order(folded, NOW)
    assert again.head == 1 and again.yielded == ()
    assert advance(folded, NOW)[1] == folded


def test_head_wrapper_refuses_facts_it_cannot_read():
    with pytest.raises(ValueError, match="busy"):
        head([_m(1, busy=True)])
    with pytest.raises(ValueError, match=r"charged_s"):
        head([_m(1, charged_s=1.0)])
    with pytest.raises(ValueError, match="issue 7"):
        head([_m(1), _m(7, yields=1)])
    assert head([_m(1, busy=True), _m(2)], NOW) == 1


def _entry(
    step: str | None, at: object, *, sha: str = "s", closed: bool = False, resolved: bool = False
):
    entry: dict[str, object] = {"head": sha, "at": at}
    if step:
        entry["step"] = step
    if closed:
        entry["closed_at"] = "c"
    if resolved:
        entry["resolved_at"] = "r"
    return entry


def _at(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


@pytest.mark.parametrize(
    ("entries", "since", "expected"),
    [
        ([_entry("sync_master", _at(1), closed=True)], None, 0),
        (
            [
                _entry("sync_master", _at(1), closed=True, resolved=True),
                _entry("re_review", _at(2)),
            ],
            T0,
            0,
        ),
        (
            [
                _entry("sync_master", _at(1), closed=True),
                _entry("sync_master", _at(2), closed=True),
            ],
            T0,
            2,
        ),
        ([_entry("re_review", _at(1)), _entry("re_review", _at(2))], T0, 1),
        ([_entry("sync_master", _at(1), closed=True, sha="other")], T0, 0),
        ([_entry("sync_master", _at(1), closed=True)], T0 + timedelta(minutes=2), 0),
        (
            [
                _entry("sync_master", "2026-11-10T12:01:00", closed=True),
                _entry("x", "x", closed=True),
            ],
            T0,
            0,
        ),
        ([_entry("sync_master", None, closed=True), _entry(None, _at(1), closed=True)], T0, 0),
    ],
)
def test_unproductive_steps(entries, since, expected):
    assert unproductive_steps(entries, "s", since) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-10T12:00:00+00:00", T0),
        ("2026-11-10T12:00:00Z", T0),
        ("2026-11-10T12:00:00", None),
        ("garbage", None),
        (None, None),
        (12, None),
    ],
)
def test_instant_is_aware_or_nothing(value, expected):
    assert instant(value) == expected


def _ago(minutes: float) -> datetime:
    return T0 - timedelta(minutes=minutes)


def test_resumed_refreshes_a_busy_head_left_unobserved_without_charging_the_gap():
    stale = Member(5, T0, False, False, busy=True, seen_at=_ago(40), charged_s=600.0)
    other = Member(6, T0, False, False)

    kept = resumed([stale, other], T0)

    assert kept == (replace(stale, seen_at=T0), other)
    assert head_time(kept[0], T0) == timedelta(seconds=600)


def test_resumed_refreshes_only_the_most_recent_of_two_busy_members():
    older = Member(5, T0, False, False, busy=True, seen_at=_ago(90))
    newer = Member(6, T0, False, False, busy=True, seen_at=_ago(40))

    kept = resumed([older, newer], T0)

    assert kept == (older, replace(newer, seen_at=T0))


@pytest.mark.parametrize(
    "member",
    [
        Member(5, T0, False, False, busy=False, seen_at=_ago(40)),
        Member(5, T0, False, False, busy=True, seen_at=_ago(5)),
        Member(5, T0, False, False, busy=True, seen_at=None),
    ],
    ids=["not busy", "seen within the gap", "never seen"],
)
def test_resumed_leaves_every_other_member_as_it_is(member):
    assert resumed([member], T0) == (member,)


def test_resumed_leaves_a_busy_head_alone_when_another_member_was_seen_within_the_gap():
    busy = Member(5, T0, False, False, busy=True, seen_at=_ago(40))
    fresh = Member(6, T0, False, False, seen_at=_ago(10))
    assert resumed([busy, fresh], T0) == (busy, fresh)


def test_a_resumed_busy_head_is_the_head_even_behind_an_older_stamp():
    busy = Member(
        9, T0 + timedelta(minutes=10), False, False, busy=True, seen_at=_ago(40), turn_at=_ago(60)
    )
    older = Member(1, T0 - timedelta(days=1), False, False)
    assert order([busy, older], T0).head == 1
    assert order(resumed([busy, older], T0), T0).head == 9


def _patch(before: Member, minutes: float = 0.0, **after) -> dict[str, object]:
    now = T0 + timedelta(minutes=minutes)
    return turn_patch(before, replace(before, **after), now)


def test_a_new_head_records_that_it_was_seen_and_its_turn():
    before = Member(5, T0, False, False)
    assert _patch(before, seen_at=T0, turn_at=T0) == {
        "head_seen_at": T0.isoformat(),
        "head_turn_at": T0.isoformat(),
    }


def test_a_new_head_that_has_a_turn_keeps_it():
    before = Member(5, T0, False, False, turn_at=_ago(30), charged_s=60.0)
    assert _patch(before, seen_at=T0) == {"head_seen_at": T0.isoformat()}


@pytest.mark.parametrize(
    ("minutes_ago", "written"),
    [(0.0, False), (HEAD_SEEN_EVERY.total_seconds() / 60 - 0.1, False), (5.0, True), (14.0, True)],
)
def test_a_head_seen_again_writes_only_every_head_seen_every(minutes_ago, written):
    before = Member(5, T0, False, False, turn_at=_ago(60), seen_at=_ago(minutes_ago))
    after = replace(before, seen_at=T0, charged_s=minutes_ago * 60)

    patch = turn_patch(before, after, T0)

    assert patch == (
        {"head_charged_s": round(minutes_ago * 60), "head_seen_at": T0.isoformat()}
        if written
        else {}
    )


def test_a_member_that_stops_being_head_folds_its_charge_and_clears_the_sighting():
    before = Member(5, T0, False, False, turn_at=_ago(60), seen_at=_ago(3), charged_s=100.0)
    patch = _patch(before, charged_s=280.0, seen_at=None)
    assert patch == {"head_charged_s": 280.0, "head_seen_at": None}


def test_a_member_that_was_never_head_has_nothing_to_write():
    before = Member(5, T0, False, False, charged_s=100.0)
    assert _patch(before) == {}


def test_a_yield_clears_the_turn_the_charge_and_the_sighting_and_marks_the_record():
    before = Member(5, T0, False, False, turn_at=_ago(60), seen_at=_ago(1), charged_s=2700.0)
    after = yielded(before, "budget_time", T0)

    assert turn_patch(before, after, T0) == {
        "head_turn_at": None,
        "head_charged_s": None,
        "head_seen_at": None,
        "head_yielded_at": T0.isoformat(),
        YIELD_MARK: 1,
    }


def test_a_yield_that_ends_the_call_as_head_again_writes_its_new_turn():
    before = Member(5, T0, False, False, turn_at=_ago(60), seen_at=_ago(1), charged_s=2700.0)
    after = observed(yielded(before, "budget_time", T0), T0)

    patch = turn_patch(before, after, T0)

    assert patch["head_turn_at"] == T0.isoformat()
    assert patch["head_seen_at"] == T0.isoformat()
    assert patch[YIELD_MARK] == 1


def test_a_member_that_yielded_long_ago_is_not_a_yield_again():
    before = Member(5, T0, False, False, yielded_at=_ago(30), yields=1)
    assert _patch(before) == {}


# --- GH-681: the fast lane facts ---------------------------------------------------------------


def _lane_head(**kw) -> Member:
    """A head that opens a lane: busy, seen a second ago, integrating, paths known, syncs left."""
    facts = {
        "busy": True,
        "seen_at": NOW - SEC,
        "integrating": True,
        "paths": frozenset({"a.py"}),
        "head_syncs_left": LANE_SYNCS_MIN,
    }
    return _m(1, **{**facts, **kw})


def _cand(issue: int = 2, **kw) -> Member:
    """A member that qualifies for the lane next to `_lane_head`."""
    facts = {
        "merge_now": True,
        "clean": True,
        "holds_master": True,
        "paths": frozenset({"b.py"}),
    }
    return _m(issue, **{**facts, **kw})


LANE_NOT_READY = {
    "not_merge_now": {"merge_now": False},
    "unread_clean": {"clean": None},
    "dirty": {"clean": False},
    "master_unknown": {"holds_master": None},
    "lacks_master": {"holds_master": False},
    "paths_unknown": {"paths": None},
    "waits_on_human": {"waits_on_human": True},
    "busy": {"busy": True},
    "live_pass": {"live_pass": True},
    "waits_on_ci": {"waits_on_ci": True},
    "account_paused": {"account_paused": True},
}


def test_lane_ready_holds_for_a_member_with_every_condition():
    assert lane_ready(_cand())
    assert lane_ready(_cand(paths=frozenset()))


@pytest.mark.parametrize("facts", list(LANE_NOT_READY.values()), ids=list(LANE_NOT_READY))
def test_lane_ready_fails_on_each_condition_alone(facts):
    assert not lane_ready(_cand(**facts))


def _lane(members: list[Member]) -> int | None:
    return order(members, NOW).lane


def test_the_lane_goes_to_a_clean_member_beside_a_busy_integrating_head():
    assert _lane([_lane_head(), _cand()]) == 2


@pytest.mark.parametrize(
    "facts",
    [
        {"busy": False},
        {"seen_at": None},
        {"seen_at": NOW - GAP_MAX - SEC},
        {"integrating": False},
        {"head_syncs_left": None},
        {"head_syncs_left": LANE_SYNCS_MIN - 1},
        {"paths": None},
    ],
    ids=[
        "idle",
        "never_seen",
        "seen_too_long_ago",
        "not_integrating",
        "syncs_none",
        "syncs_low",
        "paths_none",
    ],
)
def test_a_head_missing_a_condition_opens_no_lane(facts):
    assert _lane([_lane_head(**facts), _cand()]) is None


def test_a_head_seen_exactly_gap_max_ago_still_opens_a_lane():
    assert _lane([_lane_head(seen_at=NOW - GAP_MAX), _cand()]) == 2


def test_more_syncs_than_the_minimum_open_a_lane():
    assert _lane([_lane_head(head_syncs_left=LANE_SYNCS_MIN + 3), _cand()]) == 2


@pytest.mark.parametrize(
    ("head_paths", "member_paths"),
    [
        (frozenset({"a.py"}), frozenset({"a.py", "b.py"})),
        (frozenset({"old.py", "new.py"}), frozenset({"old.py"})),
        (frozenset({"a.py"}), None),
    ],
    ids=["overlap", "rename", "member_unknown"],
)
def test_overlapping_or_unknown_paths_open_no_lane(head_paths, member_paths):
    assert _lane([_lane_head(paths=head_paths), _cand(paths=member_paths)]) is None


def test_an_empty_head_diff_opens_a_lane_and_an_empty_member_diff_gets_it():
    assert _lane([_lane_head(paths=frozenset()), _cand()]) == 2
    assert _lane([_lane_head(), _cand(paths=frozenset())]) == 2


def test_only_the_oldest_ready_member_gets_the_lane():
    members = [_lane_head(), _cand(5), _cand(3), _cand(4)]
    assert _lane(members) == 3
    assert _lane(list(reversed(members))) == 3
    stamp = T0 + timedelta(minutes=2)
    tie = [_lane_head(), _cand(8, queued_at=stamp), _cand(6, queued_at=stamp)]
    assert _lane(tie) == 6


def test_an_older_member_that_overlaps_does_not_block_a_younger_disjoint_one():
    overlapping = _cand(2, paths=frozenset({"a.py"}))
    assert _lane([_lane_head(), overlapping, _cand(3)]) == 3


def test_a_head_that_yielded_in_this_call_opens_no_lane():
    stuck = _lane_head(busy=False, seen_at=None, charged_s=HEAD_BUDGET.total_seconds())
    result = order([stuck, _cand()], NOW)
    assert result.yielded and result.head == 2
    assert result.lane is None


def test_a_lone_eligible_member_gets_no_lane():
    assert _lane([_lane_head()]) is None
    assert _lane([_lane_head(), _cand(waits_on_human=True)]) is None


@pytest.mark.parametrize("facts", [{"waits_on_ci": True}, {"account_paused": True}])
def test_a_member_that_cannot_advance_never_gets_the_lane(facts):
    assert _lane([_lane_head(), _cand(**facts)]) is None


def test_a_busy_member_is_not_lane_ready_so_it_never_gets_the_lane():
    assert _lane([_lane_head(), _cand(busy=True)]) is None


def test_advance_passes_the_lane_through_and_leaves_the_head_and_yields_alone():
    members = [_lane_head(), _cand()]
    with_lane = advance(members, NOW)
    bare = advance([replace(m, merge_now=False, clean=None) for m in members], NOW)
    assert with_lane[0].lane == 2 and bare[0].lane is None
    assert (with_lane[0].head, with_lane[0].yielded) == (bare[0].head, bare[0].yielded)


# --- lane_reason ---------------------------------------------------------------------------------

LANE_REASONS: dict[LaneReason, tuple[Member | None, Member]] = {
    "head_not_integrating": (None, _cand()),
    "head_paths_unknown": (_lane_head(paths=None), _cand()),
    "head_sync_budget": (_lane_head(head_syncs_left=None), _cand()),
    "holds_master_unknown": (_lane_head(), _cand(holds_master=None)),
    "lacks_master": (_lane_head(), _cand(holds_master=False)),
    "member_paths_unknown": (_lane_head(), _cand(paths=None)),
    "paths_overlap": (_lane_head(), _cand(paths=frozenset({"a.py"}))),
}


def test_lane_reason_covers_the_seven_reasons_of_the_type():
    assert set(LANE_REASONS) == set(get_args(LaneReason))
    assert get_args(LaneReason) == (
        "head_not_integrating",
        "head_paths_unknown",
        "head_sync_budget",
        "holds_master_unknown",
        "lacks_master",
        "paths_overlap",
        "member_paths_unknown",
    )


@pytest.mark.parametrize("reason", list(LANE_REASONS))
def test_lane_reason_names_each_obstacle(reason):
    head_, member = LANE_REASONS[reason]
    assert lane_reason(member, head_, NOW) == reason


@pytest.mark.parametrize(
    "head_",
    [
        _lane_head(busy=False),
        _lane_head(seen_at=None),
        _lane_head(seen_at=NOW - GAP_MAX - SEC),
        _lane_head(integrating=False),
    ],
    ids=["idle", "never_seen", "seen_too_long_ago", "not_integrating"],
)
def test_a_head_that_does_not_hold_its_turn_or_integrate_is_not_integrating(head_):
    assert lane_reason(_cand(), head_, NOW) == "head_not_integrating"


def test_the_head_is_checked_before_the_member():
    member = _cand(holds_master=False, paths=None)
    assert lane_reason(member, _lane_head(integrating=False), NOW) == "head_not_integrating"
    assert lane_reason(member, _lane_head(paths=None), NOW) == "head_paths_unknown"
    assert lane_reason(member, _lane_head(head_syncs_left=1), NOW) == "head_sync_budget"
    assert lane_reason(member, _lane_head(), NOW) == "lacks_master"
    unknown = _cand(holds_master=None, paths=None)
    assert lane_reason(unknown, _lane_head(), NOW) == "holds_master_unknown"
    assert lane_reason(_cand(paths=None), _lane_head(), NOW) == "member_paths_unknown"


def test_the_syncs_are_compared_to_the_minimum():
    low = _lane_head(head_syncs_left=LANE_SYNCS_MIN - 1)
    assert lane_reason(_cand(), low, NOW) == "head_sync_budget"
    assert lane_reason(_cand(), _lane_head(head_syncs_left=LANE_SYNCS_MIN), NOW) is None


@pytest.mark.parametrize(
    "member",
    [_cand(merge_now=False), _cand(clean=False), _cand(clean=None)],
    ids=["not_merge_now", "dirty", "unread"],
)
def test_a_member_that_is_not_merge_now_and_clean_gets_no_reason(member):
    assert lane_reason(member, None, NOW) is None
    assert lane_reason(member, _lane_head(integrating=False), NOW) is None


def test_the_lane_member_has_no_reason():
    assert lane_reason(_cand(), _lane_head(), NOW) is None


def test_no_reason_does_not_mean_the_lane():
    younger = _cand(3)
    assert _lane([_lane_head(), younger, _cand(2)]) == 2
    assert lane_reason(younger, _lane_head(), NOW) is None
    live = _cand(2, live_pass=True)
    assert lane_reason(live, _lane_head(), NOW) is None
    assert _lane([_lane_head(), live]) is None


LANE_SCENARIOS = [
    [_lane_head(), _cand()],
    [_lane_head(), _cand(5), _cand(3)],
    [_lane_head(), _cand(2, paths=frozenset({"a.py"})), _cand(3)],
    [_lane_head(paths=frozenset()), _cand(2, paths=frozenset())],
    [_lane_head(integrating=False), _cand()],
    [_lane_head(), _cand(live_pass=True)],
    [_lane_head(), _cand(waits_on_ci=True)],
    [_lane_head(), _cand(holds_master=None), _cand(3)],
    [_lane_head(busy=False), _cand(), _cand(3)],
]


@pytest.mark.parametrize("members", LANE_SCENARIOS)
def test_the_lane_member_has_no_reason_and_is_ready(members):
    result = order(members, NOW)
    if result.lane is None:
        return
    by_issue = {m.issue: m for m in members}
    member = by_issue[result.lane]
    assert lane_reason(member, by_issue[result.head], NOW) is None
    assert lane_ready(member)


def test_the_consistency_table_has_scenarios_with_and_without_a_lane():
    lanes = [order(members, NOW).lane for members in LANE_SCENARIOS]
    assert any(lane is not None for lane in lanes) and any(lane is None for lane in lanes)


# --- integrates ----------------------------------------------------------------------------------

INTEGRATES = {"role": True, "land_model": True, "merge": False, "land_checks": False}


def test_integrates_is_decided_for_every_run_kind():
    assert set(get_args(rules.RunKind)) == set(INTEGRATES)
    for kind, expected in INTEGRATES.items():
        assert integrates(kind) is expected


def test_an_unknown_kind_does_not_integrate():
    assert integrates("something_else") is False


# --- defaults ------------------------------------------------------------------------------------

DEFAULT_SCENARIOS = [
    [_m(1), _m(2), _m(3)],
    [_m(1, live_pass=True), _m(2)],
    [_m(1, waits_on_human=True), _m(2)],
    [_m(1, busy=True, seen_at=NOW - SEC), _m(2)],
    [_m(1, charged_s=HEAD_BUDGET.total_seconds()), _m(2)],
    [_m(1)],
    [],
]


@pytest.mark.parametrize("members", DEFAULT_SCENARIOS)
def test_default_lane_facts_change_nothing_and_leave_the_lane_unset(members):
    result = order(members, NOW)
    assert result.lane is None
    assert result.head == head(members, NOW)
    assert advance(members, NOW)[0] == result
    assert all(not lane_ready(m) for m in members)


def test_lane_facts_alone_do_not_move_the_head_or_the_yields():
    plain = [_m(1, busy=True, seen_at=NOW - SEC), _m(2), _m(3)]
    rich = [
        replace(
            m,
            merge_now=True,
            clean=True,
            holds_master=True,
            paths=frozenset({str(m.issue)}),
            integrating=True,
        )
        for m in plain
    ]
    rich[0] = replace(rich[0], head_syncs_left=LANE_SYNCS_MIN)
    assert order(rich, NOW).lane == 2
    assert (order(rich, NOW).head, order(rich, NOW).yielded) == (
        order(plain, NOW).head,
        order(plain, NOW).yielded,
    )
    assert advance(rich, NOW)[0].head == advance(plain, NOW)[0].head


LANE_FIELD_VALUES = {
    "merge_now": True,
    "clean": True,
    "holds_master": False,
    "integrating": True,
    "paths": frozenset(),
    "head_syncs_left": 0,
}


@pytest.mark.parametrize("name", list(LANE_FIELD_VALUES))
def test_head_without_now_rejects_each_lane_fact_off_its_default(name):
    assert name in {f.name for f in fields(Member)}
    with pytest.raises(ValueError, match=name):
        head([_m(1, **{name: LANE_FIELD_VALUES[name]})])
    assert head([_m(1)]) == 1
