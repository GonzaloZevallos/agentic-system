"""The pure rules of GH-601 part 2: how a maturing ghost extends the full tier's wait, when a
timed-out push is a dispatch candidate, when a recorded dispatch stands, and when a dispatched run
is itself a ghost. One row per input and the answer it must give, rejections beside acceptances."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import (
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


@pytest.mark.parametrize(
    ("fact", "head", "expected"),
    [
        (_fact(), HEAD, True),
        (_fact(head=HEAD[:12]), HEAD, True),  # the same commit, short or whole
        (_fact(escalated=True), HEAD, False),
        (_fact(head=OTHER), HEAD, False),
        (None, HEAD, False),
    ],
)
def test_ghost_fact_in_force(fact, head, expected):
    assert ci.ghost_fact_in_force(fact, head) is expected


# -- dispatched_ghost --


def _run(run_id=7, title=f"ci {HEAD}", created_at="2026-11-13T10:00:00Z"):
    return DispatchedRun(run_id, "workflow_dispatch", title, "queued", created_at, "u", ())


@pytest.mark.parametrize(
    ("verdict", "runs", "ghosts", "expected"),
    [
        (_TIMED_OUT, (_run(7),), frozenset({"7"}), True),
        (_TIMED_OUT, [_run(7)], frozenset({"7"}), True),
        (_TIMED_OUT, (_run(7),), frozenset({"8"}), False),
        (_TIMED_OUT, (_run(7),), frozenset(), False),
        (_TIMED_OUT, (_run(7, title="ci other"),), frozenset({"7"}), False),  # not this head
        # The newest run judges: the ghost is ignored once a newer run exists.
        (
            _TIMED_OUT,
            (_run(7, created_at="2026-11-13T10:00:00Z"), _run(8, created_at="2026-11-13T10:05:00Z")),
            frozenset({"7"}),
            False,
        ),
        (_TIMED_OUT, CiUnreadable("gh_unreadable"), frozenset({"7"}), False),
        (FullTierVerdict("waiting", (), (), None), (_run(7),), frozenset({"7"}), False),
        (FullTierVerdict("green", (), (), None), (_run(7),), frozenset({"7"}), False),
    ],
)
def test_dispatched_ghost(verdict, runs, ghosts, expected):
    assert ci.dispatched_ghost(verdict, runs, HEAD, ghosts) is expected
