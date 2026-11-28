"""GH-500 (ADR-0045 addendum): the pure exempt queue. `rank` and `ordered` give the one order
of the merges and exempt lands across products, `startable` and `behind` gate what a poller
publishes, `backed_off` honours a refusal only on its own head and master, and `stale_keys`
deletes an attempt record only on a fact."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.domain import exempt_queue as eq
from agentic.core.process.domain import rules

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)
HEAD = "a" * 40
MASTER = "b" * 40
OTHER = "c" * 40


def _cand(issue, *, kind="merge", product="p", root="r1", first=None, last=None):
    return eq.ExemptCandidate(kind, product, root, issue, first, last)


def _issues(candidates):
    return [(c.kind, c.issue) for c in eq.ordered(candidates)]


def test_the_constants_are_defined_once_here():
    assert eq.EXEMPT_WAIT_TTL == timedelta(minutes=15)
    assert eq.EXEMPT_REFUSAL_BACKOFF == timedelta(minutes=30)
    assert eq.EXEMPT_STARVED_AFTER == timedelta(minutes=20)
    assert not hasattr(rules, "EXEMPT_REFUSAL_BACKOFF")


def test_without_records_every_merge_goes_before_any_land_lowest_issue_first():
    cands = [_cand(9, kind="land"), _cand(7), _cand(3, kind="land"), _cand(5)]
    assert _issues(cands) == [("merge", 5), ("merge", 7), ("land", 3), ("land", 9)]


def test_who_waited_longest_since_its_last_attempt_goes_first():
    cands = [
        _cand(478, last=NOW - timedelta(seconds=10)),
        _cand(487, last=NOW - timedelta(minutes=5)),
        _cand(492, last=NOW - timedelta(minutes=9)),
    ]
    assert _issues(cands) == [("merge", 492), ("merge", 487), ("merge", 478)]


def test_a_candidate_never_attempted_goes_before_any_attempted_one():
    cands = [_cand(1, last=NOW), _cand(9, first=NOW - timedelta(hours=1)), _cand(5)]
    assert _issues(cands) == [("merge", 5), ("merge", 9), ("merge", 1)]


def test_a_land_that_waited_longer_goes_before_a_merge_attempted_later():
    cands = [_cand(4, last=NOW), _cand(9, kind="land", last=NOW - timedelta(minutes=3))]
    assert _issues(cands) == [("land", 9), ("merge", 4)]


def test_equal_instants_across_products_fall_back_on_the_root_key_then_the_issue():
    cands = [_cand(2, product="q", root="r2"), _cand(8, root="r1"), _cand(3, root="r1")]
    assert [(c.root_key, c.issue) for c in eq.ordered(cands)] == [("r1", 3), ("r1", 8), ("r2", 2)]


def test_rank_since_is_the_last_attempt_then_the_first_seen_then_never():
    assert eq.rank(_cand(1, first=NOW, last=NOW - timedelta(hours=1))).since == NOW - timedelta(
        hours=1
    )
    assert eq.rank(_cand(1, first=NOW)).since == NOW
    assert eq.rank(_cand(1)).since == eq.NEVER


def test_a_candidate_is_startable_only_when_nothing_holds_it():
    cand = _cand(1)
    free = {"backed_off": False, "held": False, "own_live_exempt": False}
    assert eq.startable(cand, **free) is True
    for reason in free:
        assert eq.startable(cand, **{**free, reason: True}) is False


def test_startable_no_longer_asks_whether_the_machine_is_busy():
    free = {"backed_off": False, "held": False, "own_live_exempt": False, "busy": True}
    with pytest.raises(TypeError):
        eq.startable(_cand(1), **free)


def test_the_machine_allows_two_exempt_children_one_per_product():
    assert eq.EXEMPT_SLOTS_MAX == 2


def _wait(root, since):
    return eq.ExemptWait("q", root, NOW, since)


EARLIER = NOW - timedelta(minutes=1)
LATER = NOW + timedelta(minutes=1)


def test_behind_yields_to_an_earlier_fresh_wait_and_ties_go_to_the_lower_root_key():
    own = eq.rank(_cand(1, root="r5", last=NOW))
    assert eq.behind(own, [_wait("r9", EARLIER)], 1) is True
    assert eq.behind(own, [_wait("r1", LATER)], 1) is False
    assert eq.behind(own, [], 1) is False
    assert eq.behind(own, [_wait("r1", NOW)], 1) is True
    assert eq.behind(own, [_wait("r9", NOW)], 1) is False


def test_a_product_with_no_head_is_behind_any_fresh_wait():
    assert eq.behind(None, [_wait("r1", NOW)], 1) is True
    assert eq.behind(None, [], 1) is False


def test_behind_with_no_free_machine_slot_yields_whatever_the_waits():
    own = eq.rank(_cand(1, root="r5", last=NOW))
    assert eq.behind(own, [], 0) is True
    assert eq.behind(own, [_wait("r9", LATER)], 0) is True
    assert eq.behind(None, [], 0) is True


def test_behind_yields_only_when_enough_earlier_waits_fill_the_free_slots():
    own = eq.rank(_cand(1, root="r5", last=NOW))
    one, two = [_wait("r1", EARLIER)], [_wait("r1", EARLIER), _wait("r2", EARLIER)]
    later = _wait("r3", LATER)
    # two free slots: one earlier wait still leaves a slot for this product
    assert eq.behind(own, [], 2) is False
    assert eq.behind(own, one, 2) is False
    assert eq.behind(own, [*one, later], 2) is False
    assert eq.behind(own, two, 2) is True
    # one free slot: the earlier wait takes it
    assert eq.behind(own, one, 1) is True
    assert eq.behind(own, [later], 1) is False


def test_with_a_single_product_and_a_free_machine_nothing_is_behind():
    own = eq.rank(_cand(1, last=NOW))
    assert eq.behind(own, [], eq.EXEMPT_SLOTS_MAX) is False
    assert eq.behind(None, [], eq.EXEMPT_SLOTS_MAX) is False


def test_live_exempt_counts_the_own_and_the_machine_children_by_kind():
    running = [
        ("a", "GH-1", "merge"),
        ("a", "GH-2", "role"),
        ("b", "GH-3", "land_checks"),
        ("b", "GH-4", "land_model"),
    ]
    own, machine, _, _ = eq.live_exempt(running, "a")
    assert (own, machine) == (1, 2)
    assert eq.live_exempt(running, "b")[0] == 1
    assert eq.live_exempt(running, "c") == (
        0,
        2,
        (("a", "GH-1", "merge"), ("b", "GH-3", "land_checks")),
        (),
    )
    assert eq.live_exempt([], "a") == (0, 0, (), ())
    assert eq.live_exempt([("a", "GH-2", "role")], "a") == (0, 0, (), ())


def test_live_exempt_lists_the_own_children_sorted_by_task():
    running = [("a", "GH-9", "merge"), ("b", "GH-1", "merge"), ("a", "GH-10", "land_checks")]
    assert eq.live_exempt(running, "a")[3] == (
        ("a", "GH-10", "land_checks"),
        ("a", "GH-9", "merge"),
    )
    assert eq.live_exempt(list(reversed(running)), "a")[3] == eq.live_exempt(running, "a")[3]


def test_live_exempt_holders_leave_out_the_own_product_and_sort_by_product_then_task():
    running = [
        ("z", "GH-1", "merge"),
        ("b", "GH-20", "merge"),
        ("a", "GH-9", "merge"),
        ("b", "GH-3", "land_checks"),
    ]
    expected = (
        ("b", "GH-20", "merge"),
        ("b", "GH-3", "land_checks"),
        ("z", "GH-1", "merge"),
    )
    assert eq.live_exempt(running, "a")[2] == expected
    assert eq.live_exempt(list(reversed(running)), "a")[2] == expected


def _refusal(**more):
    return {
        "reason": "ci_untrusted_no_dispatch",
        "head": HEAD,
        "master": MASTER,
        "at": (NOW - timedelta(minutes=5)).isoformat(),
        **more,
    }


def test_a_refusal_holds_on_the_same_head_and_master_inside_the_backoff():
    assert eq.backed_off(_refusal(), HEAD, MASTER, NOW) is True
    # an abbreviation of the same commit is the same commit
    assert eq.backed_off(_refusal(), HEAD[:12], MASTER, NOW) is True


def test_a_moved_head_or_master_releases_the_refusal():
    assert eq.backed_off(_refusal(), OTHER, MASTER, NOW) is False
    assert eq.backed_off(_refusal(), HEAD, OTHER, NOW) is False
    assert eq.backed_off(_refusal(), None, MASTER, NOW) is False


def test_an_expired_backoff_releases_the_refusal():
    inside = NOW + eq.EXEMPT_REFUSAL_BACKOFF - timedelta(minutes=6)
    assert eq.backed_off(_refusal(), HEAD, MASTER, inside) is True
    assert eq.backed_off(_refusal(), HEAD, MASTER, NOW + eq.EXEMPT_REFUSAL_BACKOFF) is False


@pytest.mark.parametrize(
    "refusal",
    [
        None,
        {},
        "ci_untrusted_no_dispatch",
        _refusal(reason="merge_conflict"),
        _refusal(reason="master_moved"),
        _refusal(reason=None),
        _refusal(head=None),
        _refusal(master=7),
        _refusal(at="yesterday"),
        _refusal(at=None),
        _refusal(at="2026-11-04T17:55:00"),
        _refusal(at=(NOW + timedelta(minutes=5)).isoformat()),
    ],
)
def test_a_missing_malformed_or_other_reason_refusal_backs_nothing_off(refusal):
    assert eq.backed_off(refusal, HEAD, MASTER, NOW) is False


def _key(issue, kind="merge", product="p"):
    return (product, issue, kind)


def test_a_record_is_stale_only_on_a_fact_about_its_task():
    facts = {
        ("p", 1): eq.ExemptFacts(closed=True, pull_request="u", pr_state="OPEN"),
        ("p", 2): eq.ExemptFacts(closed=False, pull_request=None, pr_state=None),
        ("p", 3): eq.ExemptFacts(closed=False, pull_request="u", pr_state="MERGED"),
        ("p", 4): eq.ExemptFacts(closed=False, pull_request="u", pr_state="CLOSED"),
        ("p", 5): eq.ExemptFacts(closed=False, pull_request="u", pr_state="OPEN"),
        ("p", 6): eq.ExemptFacts(closed=False, pull_request="u", pr_state=None),
        ("p", 7): eq.ExemptFacts(closed=False, pull_request="", pr_state=None),
    }
    records = [_key(i) for i in range(1, 8)]
    assert eq.stale_keys(records, facts) == [_key(1), _key(2), _key(3), _key(4), _key(7)]


def test_a_record_with_no_facts_is_never_stale_whatever_this_round_lists():
    records = [_key(1), _key(2, "land"), _key(3, product="other")]
    assert eq.stale_keys(records, {}) == []
    only_other = {("other", 3): eq.ExemptFacts(True, None, None)}
    assert eq.stale_keys(records, only_other) == [_key(3, product="other")]


def test_both_kinds_of_one_issue_go_together_when_the_task_is_closed():
    facts = {("p", 1): eq.ExemptFacts(closed=True, pull_request="u", pr_state="OPEN")}
    assert eq.stale_keys([_key(1), _key(1, "land")], facts) == [_key(1, "land"), _key(1)]


def test_the_backoff_ends_at_the_refusal_plus_the_backoff_for_the_same_pair_only():
    written = NOW - timedelta(minutes=5)
    assert eq.backoff_ends(_refusal(), HEAD, MASTER) == written + eq.EXEMPT_REFUSAL_BACKOFF
    # a refusal for another pair, or a malformed one, has no end to wait for
    assert eq.backoff_ends(_refusal(), OTHER, MASTER) is None
    assert eq.backoff_ends(_refusal(), HEAD, OTHER) is None
    assert eq.backoff_ends(_refusal(reason="master_moved"), HEAD, MASTER) is None
    assert eq.backoff_ends(_refusal(at="yesterday"), HEAD, MASTER) is None
    assert eq.backoff_ends(None, HEAD, MASTER) is None
