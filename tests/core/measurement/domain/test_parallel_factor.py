"""The parallel factor over task documents: `core.measurement.domain.parallel_factor`."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.measurement.domain import parallel_factor as pf

_ORIGIN = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def run(role, start, end, section="s"):
    """A run whose clock reads `start`/`end` as minutes past 2026-10-20T10:00:00Z."""
    out = {"role": role, "section": section, "version": 1, "cost_usd": 0.0, "turns": 1}
    if start is not None:
        out["started_at"] = _iso(start)
    if end is not None:
        out["ended_at"] = _iso(end)
    return out


def _iso(minutes):
    at = _ORIGIN + timedelta(minutes=minutes)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def dev(start, end, id_="a"):
    return run("Developer", start, end, f"Developer: {id_}")


def review(start, end, id_="a"):
    return run("Tech Lead", start, end, f"Tech Lead: review {id_}")


def doc(*runs):
    return {"runs": list(runs)}


def test_two_developers_overlapping_for_half_their_time_run_at_one_and_a_half():
    # a: 0-10, b: 5-15 -> 20 min of Developer over a 15 min union.
    assert pf.parallel_factor(doc(dev(0, 10, "a"), dev(5, 15, "b"))) == pytest.approx(20 / 15)


def test_one_developer_alone_is_one():
    assert pf.parallel_factor(doc(dev(0, 10))) == pytest.approx(1.0)


def test_a_review_widens_the_union_but_adds_nothing_to_the_numerator():
    # Developer 0-10, its review 10-14, a second Developer 12-20: 18 min over 20.
    document = doc(dev(0, 10, "a"), review(10, 14, "a"), dev(12, 20, "b"))
    assert pf.busy_seconds(document) == (18 * 60, 20 * 60)


def test_a_gap_with_nothing_running_is_not_counted():
    # 0-10 and 30-40: the 20 idle minutes in between are outside the union.
    assert pf.parallel_factor(doc(dev(0, 10, "a"), dev(30, 40, "b"))) == pytest.approx(1.0)
    assert pf.busy_seconds(doc(dev(0, 10, "a"), dev(30, 40, "b"))) == (20 * 60, 20 * 60)


def test_a_pause_between_two_invocations_is_not_counted():
    # A task paused for a day after its first Developer and resumed: only the runs count.
    day = 24 * 60
    document = doc(dev(0, 10, "a"), dev(day, day + 10, "b"), review(day + 10, day + 15, "b"))
    assert pf.busy_seconds(document) == (20 * 60, 25 * 60)


def test_runs_without_both_timestamps_are_ignored():
    document = doc(dev(0, 10, "a"), dev(None, 20, "b"), dev(5, None, "c"), dev(None, None, "d"))
    assert pf.busy_seconds(document) == (10 * 60, 10 * 60)


def test_an_unparseable_timestamp_is_ignored():
    broken = dev(0, 10, "b")
    broken["started_at"] = "yesterday"
    assert pf.busy_seconds(doc(dev(0, 10, "a"), broken)) == (10 * 60, 10 * 60)


def test_the_integrator_counts_as_a_developer():
    integrator = run("Developer", 10, 20, "Developer: integration")
    assert pf.busy_seconds(doc(dev(0, 10, "a"), integrator)) == (20 * 60, 20 * 60)


def test_the_integration_review_and_other_roles_are_not_subtask_work():
    final = run("Tech Lead", 10, 30, "Tech Lead: review integration")
    pm = run("PM", 0, 40, "PM")
    assert pf.busy_seconds(doc(dev(0, 10), final, pm)) == (10 * 60, 10 * 60)


def test_no_runs_or_no_timed_runs_is_none():
    assert pf.parallel_factor({}) is None
    assert pf.parallel_factor(doc()) is None
    assert pf.parallel_factor(doc(dev(None, None))) is None
    assert pf.parallel_factor(doc(dev(5, 5))) is None


def test_a_zero_length_run_beside_a_real_one_does_not_move_the_factor():
    assert pf.parallel_factor(doc(dev(0, 10, "a"), dev(5, 5, "b"))) == pytest.approx(1.0)


def test_the_aggregate_is_the_sum_of_numerators_over_the_sum_of_denominators():
    long_serial = doc(dev(0, 60, "a"))  # 60 over 60
    short_parallel = doc(dev(0, 10, "a"), dev(0, 10, "b"))  # 20 over 10
    # Weighted: 80 / 70, not the mean of 1.0 and 2.0.
    assert pf.aggregate([long_serial, short_parallel]) == pytest.approx(80 / 70)


def test_the_aggregate_skips_a_task_with_no_time_and_is_none_for_none():
    assert pf.aggregate([doc(), doc(dev(0, 10))]) == pytest.approx(1.0)
    assert pf.aggregate([doc()]) is None
    assert pf.aggregate([]) is None
