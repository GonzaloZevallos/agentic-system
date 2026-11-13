"""The pure rules of GH-601 part 2: how a maturing ghost extends the full tier's wait, when a
timed-out push is a dispatch candidate, when a recorded dispatch stands, and how a dispatched run
that is itself a ghost ends. One row per input and the answer it must give, rejections beside
acceptances."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import (
    CiCheck,
    CiReport,
    CiUnreadable,
    DispatchedRun,
    FullTierVerdict,
)

HEAD = "a" * 40
OTHER = "b" * 40
NAMES = ("unit",)
BOUND = 600.0
GRACE = 120.0


def _report(running=(), empty=()):
    return CiReport(
        HEAD,
        None,
        None,
        None,
        (),
        running_runs=frozenset(running),
        queued_empty_runs=frozenset(empty),
    )


def _verdict(read, elapsed, **kwargs):
    return ci.full_tier_verdict(read, HEAD, NAMES, elapsed, BOUND, GRACE, **kwargs)


# -- full_tier_verdict with a maturing ghost --


@pytest.mark.parametrize(
    ("elapsed", "ceiling", "expected"),
    [
        (599.0, None, "waiting"),  # today: before the bound
        (600.0, None, "waiting"),  # the bound, ghost maturing, under the ceiling: extended
        (1199.0, None, "waiting"),
        (1200.0, None, "timed_out"),  # the ceiling 2 * timeout: today's result
        (600.0, 1200.0, "timed_out"),  # the node's own ceiling elapsed overrides the wait's
        (1300.0, 700.0, "waiting"),  # the wait's elapsed is past the ceiling, the node's is not
    ],
)
def test_a_maturing_ghost_extends_the_bound_until_twice_the_timeout(elapsed, ceiling, expected):
    read = _report(running=["9"], empty=["9"])
    got = _verdict(
        read,
        elapsed,
        maturing=frozenset({"9"}),
        watched=frozenset({"9"}),
        ceiling_elapsed_s=ceiling,
    )
    assert got.state == expected


def test_with_no_maturing_id_the_bound_is_todays():
    read = _report(running=["9"], empty=["9"])
    got = _verdict(read, 600.0, maturing=frozenset(), watched=frozenset({"9"}))
    assert got == _verdict(read, 600.0)
    assert got.state == "timed_out"


def test_a_live_run_outside_the_map_keeps_todays_bound():
    read = _report(running=["9", "10"], empty=["9"])
    got = _verdict(read, 600.0, maturing=frozenset({"9"}), watched=frozenset({"9"}))
    assert got.state == "timed_out"
    assert got == _verdict(read, 600.0)


def test_a_ghost_already_past_the_bound_is_in_the_map_but_not_maturing_so_the_bound_is_todays():
    read = _report(running=["9"], empty=["9"])
    got = _verdict(read, 600.0, maturing=frozenset(), watched=frozenset({"9"}), ceiling_elapsed_s=0)
    assert got.state == "timed_out"


def test_an_unreadable_read_is_not_extended():
    got = _verdict(
        CiUnreadable("gh_unreadable"), 600.0, maturing=frozenset({"9"}), watched=frozenset({"9"})
    )
    assert got.state == "unreachable"


def test_the_defaults_give_todays_verdict():
    read = _report(running=["9"], empty=["9"])
    assert _verdict(read, 600.0).state == "timed_out"
    assert _verdict(read, 599.0).state == "waiting"


# -- ghost_dispatch_due --


_TIMED_OUT = FullTierVerdict("timed_out", (), ("unit",), None)


@pytest.mark.parametrize(
    ("verdict", "read", "ghosts", "expected"),
    [
        (_TIMED_OUT, _report(running=["9"]), frozenset({"9"}), True),
        (_TIMED_OUT, _report(running=["9", "10"]), frozenset({"9", "10"}), True),
        (_TIMED_OUT, _report(running=["9", "10"]), frozenset({"9"}), False),  # a live non-ghost
        (_TIMED_OUT, _report(running=["9"]), frozenset(), False),  # nothing is a ghost
        (_TIMED_OUT, _report(running=[]), frozenset({"9"}), False),  # no live run
        (_TIMED_OUT, CiUnreadable("gh_unreadable"), frozenset({"9"}), False),
        (FullTierVerdict("waiting", (), (), None), _report(running=["9"]), frozenset({"9"}), False),
        (FullTierVerdict("green", (), (), None), _report(running=["9"]), frozenset({"9"}), False),
        (
            FullTierVerdict("unreachable", (), (), "no_run"),
            _report(running=["9"]),
            frozenset({"9"}),
            False,
        ),
    ],
)
def test_ghost_dispatch_due(verdict, read, ghosts, expected):
    assert ci.ghost_dispatch_due(verdict, read, ghosts) is expected


# -- ghost_fact_in_force --


def _fact(head=HEAD, escalated=False):
    return {
        "head": head,
        "ghost_run_ids": ["9"],
        "dispatched_at": "2026-11-13T10:00:00+00:00",
        "escalated": escalated,
    }


NOW = "2026-11-13T10:05:00+00:00"  # 300 s after the fact's dispatched_at, of a bound of 600 s


@pytest.mark.parametrize(
    ("fact", "head", "now", "expected"),
    [
        (_fact(), HEAD, NOW, True),  # young
        (_fact(head=HEAD[:12]), HEAD, NOW, True),  # the same commit, short or whole
        (_fact(), HEAD, "2026-11-13T10:09:59+00:00", True),  # a second under the bound
        (_fact(), HEAD, "2026-11-13T10:10:00+00:00", False),  # exactly at the bound
        (_fact(), HEAD, "2026-11-13T12:00:00+00:00", False),  # old
        (_fact(), HEAD, "2026-11-13T09:00:00+00:00", True),  # a clock that went back is young
        (_fact(escalated=True), HEAD, NOW, False),
        (_fact(head=OTHER), HEAD, NOW, False),
        ({**_fact(), "dispatched_at": "not an instant"}, HEAD, NOW, False),
        (_fact(), HEAD, "not an instant", False),
        (None, HEAD, NOW, False),
    ],
)
def test_ghost_fact_in_force(fact, head, now, expected):
    assert ci.ghost_fact_in_force(fact, head, now, BOUND) is expected


# -- dispatch_verdict with ghosts and maturing (GH-652) --


def _job(conclusion="SUCCESS", status="COMPLETED"):
    return CiCheck("unit", status, conclusion, "u")


def _dispatched(run_id=7, status="queued", jobs=()):
    return DispatchedRun(
        run_id, "workflow_dispatch", f"ci {HEAD}", status, "2026-11-13T10:00:00Z", "u", jobs
    )


def _dv(runs, elapsed, **kwargs):
    return ci.dispatch_verdict(runs, HEAD, NAMES, elapsed, GRACE, BOUND, **kwargs)


def test_a_ghost_chosen_run_at_the_bound_is_unreachable_ghost_run():
    got = _dv([_dispatched()], BOUND, ghosts=frozenset({"7"}))
    assert (got.state, got.cause) == ("unreachable", "ghost_run")
    assert got.pending == NAMES


def test_a_ghost_under_the_bound_is_waiting():
    assert _dv([_dispatched()], BOUND - 0.001, ghosts=frozenset({"7"})).state == "waiting"


def test_an_id_in_ghosts_and_maturing_is_a_ghost():
    got = _dv([_dispatched()], BOUND, ghosts=frozenset({"7"}), maturing=frozenset({"7"}))
    assert (got.state, got.cause) == ("unreachable", "ghost_run")


@pytest.mark.parametrize(
    ("elapsed", "state"),
    [
        (BOUND - 0.001, "waiting"),
        (BOUND, "waiting"),
        (2 * BOUND - 0.001, "waiting"),
        (2 * BOUND, "timed_out"),
        (3 * BOUND, "timed_out"),
    ],
)
def test_a_maturing_chosen_run_waits_until_twice_the_timeout(elapsed, state):
    got = _dv([_dispatched()], elapsed, maturing=frozenset({"7"}))
    assert (got.state, got.cause) == (state, None)
    assert got.pending == NAMES


def test_an_id_that_is_not_the_chosen_run_changes_nothing():
    runs = [_dispatched(7)]
    other = frozenset({"8"})
    assert _dv(runs, BOUND, ghosts=other) == _dv(runs, BOUND)
    assert _dv(runs, BOUND, maturing=other) == _dv(runs, BOUND)
    older = [
        _dispatched(7),
        DispatchedRun(
            8, "workflow_dispatch", f"ci {HEAD}", "queued", "2026-11-13T09:00:00Z", "u", ()
        ),
    ]
    assert _dv(older, BOUND, ghosts=frozenset({"8"})) == _dv(older, BOUND)


def test_a_completed_chosen_run_ignores_ghosts_and_maturing():
    runs = [_dispatched(7, "completed", (_job(),))]
    both = frozenset({"7"})
    assert _dv(runs, BOUND, ghosts=both, maturing=both) == _dv(runs, BOUND)
    assert _dv(runs, BOUND, ghosts=both).state == "green"


@pytest.mark.parametrize(
    ("runs", "elapsed"),
    [
        ([], 0.0),
        ([], GRACE),
        ([_dispatched()], 10.0),
        ([_dispatched()], BOUND),
        ([_dispatched(7, "in_progress", (_job(None, "IN_PROGRESS"),))], BOUND),
        ([_dispatched(7, "completed", (_job(),))], 10.0),
        ([_dispatched(7, "completed", (_job("FAILURE"),))], 10.0),
        ([_dispatched(7, "completed", ())], 10.0),
        ([_dispatched(7, "completed", (_job("SKIPPED"),))], 10.0),
    ],
)
def test_empty_ghosts_and_maturing_give_todays_result(runs, elapsed):
    assert _dv(runs, elapsed, ghosts=frozenset(), maturing=frozenset()) == _dv(runs, elapsed)


def test_todays_results_for_a_queued_run_at_the_bound():
    got = _dv([_dispatched()], BOUND)
    assert (got.state, got.cause) == ("timed_out", None)


# -- ghost_run_ids --


def test_ghost_run_ids_keeps_order_and_drops_duplicates_at_their_first_position():
    assert ci.ghost_run_ids(["3", "1", "3", "2", "1"]) == ["3", "1", "2"]
    assert ci.ghost_run_ids([]) == []


def test_ghost_run_ids_leaves_eight_ids_unchanged():
    ids = [str(n) for n in range(8)]
    assert ci.ghost_run_ids(ids) == ids


@pytest.mark.parametrize("count", [9, 12])
def test_ghost_run_ids_caps_to_the_first_and_the_seven_newest(count):
    ids = [str(n) for n in range(count)]
    got = ci.ghost_run_ids(ids)
    assert got == ["0", *ids[-7:]]
    assert len(got) == 8


# -- ghost_end --


@pytest.mark.parametrize(
    ("cause", "ids", "expected"),
    [
        ("no_run", ["1"], "ghost_run"),
        ("no_run", [], "no_run"),
        ("ghost_run", ["1"], "ghost_run"),
        ("ghost_run", [], "ghost_run"),
        ("gh_unavailable", ["1"], "gh_unavailable"),
        ("run_unfinished", ["1", "2"], "run_unfinished"),
        ("jobs_missing", [], "jobs_missing"),
    ],
)
def test_ghost_end(cause, ids, expected):
    assert ci.ghost_end(cause, ids) == expected


# -- ghost_ids_text --


def test_ghost_ids_text_never_claims_a_total_it_does_not_hold():
    assert ci.ghost_ids_text([]) == "no run id recorded"
    assert ci.ghost_ids_text(["11"]) == "run 11"
    assert ci.ghost_ids_text(["11", "22"]) == "runs 11 … 22 (2 recorded)"
