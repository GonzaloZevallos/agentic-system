"""`ci.waited_seconds` and `ci.timing` (GH-467): the one derivation of a CI wait and the pure
builder of the instants it is derived from. Domain only: no clock, no network."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck

PUSHED = "2026-11-06T10:00:00+00:00"
NODE = "2026-11-06T09:59:00+00:00"


def timing(**over):
    base = {
        "pushed_at": PUSHED,
        "node_started_at": NODE,
        "run_started_at": "2026-11-06T10:00:20Z",
        "run_ended_at": "2026-11-06T10:05:00Z",
        "verdict": "green",
    }
    return {**base, **over}


def check(name, started, completed, conclusion="SUCCESS"):
    return CiCheck(
        name,
        "COMPLETED" if completed else "IN_PROGRESS",
        conclusion if completed else None,
        "link",
        started_at=started,
        completed_at=completed,
    )


def test_wait_counts_from_the_later_of_push_and_node_start():
    assert ci.waited_seconds(timing()) == (300.0, False)
    # A node that started after the push (a resume): counted from the node.
    assert ci.waited_seconds(timing(node_started_at="2026-11-06T10:02:00+00:00")) == (180.0, False)


def test_a_run_that_ended_before_the_wait_began_is_clamped_to_zero_and_flagged():
    late = timing(node_started_at="2026-11-06T10:10:00+00:00")
    assert ci.waited_seconds(late) == (0.0, True)


@pytest.mark.parametrize("missing", ["run_ended_at", "pushed_at", "node_started_at"])
def test_a_missing_bound_is_unknown_never_zero(missing):
    assert ci.waited_seconds(timing(**{missing: None})) == (None, False)


def test_an_unreadable_instant_is_unknown():
    assert ci.waited_seconds(timing(run_ended_at="yesterday")) == (None, False)


def test_a_no_verdict_is_ended_only_by_the_instant_handed_in():
    none = timing(verdict="no_verdict", run_ended_at=None)
    assert ci.waited_seconds(none) == (None, False)
    assert ci.waited_seconds(none, until="2026-11-06T10:30:00+00:00") == (1800.0, False)
    assert ci.waited_seconds(none, until="2026-11-06T09:00:00+00:00") == (0.0, True)


def test_the_run_end_wins_over_until():
    assert ci.waited_seconds(timing(), until="2026-11-06T11:00:00+00:00") == (300.0, False)


def test_the_builder_takes_the_declared_runs_of_this_push_and_derives_the_jobs():
    checks = (
        check("web", "2026-11-06T10:00:20Z", "2026-11-06T10:03:20Z"),
        check("e2e", "2026-11-06T10:00:30Z", "2026-11-06T10:05:00Z"),
        check("unrelated", "2026-11-06T10:00:10Z", "2026-11-06T10:09:00Z"),
    )
    built = ci.timing(PUSHED, NODE, "green", checks, ("web", "e2e"))
    assert built == {
        "pushed_at": PUSHED,
        "node_started_at": NODE,
        "run_started_at": "2026-11-06T10:00:20Z",
        "run_ended_at": "2026-11-06T10:05:00Z",
        "verdict": "green",
        "jobs": [{"name": "web", "duration_s": 180.0}, {"name": "e2e", "duration_s": 270.0}],
    }


def test_a_run_older_than_the_skew_before_the_push_is_excluded():
    old = check("web", "2026-11-06T09:00:00Z", "2026-11-06T09:04:00Z")
    fresh = check("e2e", "2026-11-06T10:00:30Z", "2026-11-06T10:05:00Z")
    inside_skew = check("lint", "2026-11-06T09:58:30Z", "2026-11-06T10:01:00Z")
    built = ci.timing(PUSHED, NODE, "green", (old, fresh, inside_skew), ("web", "e2e", "lint"))
    assert built["run_started_at"] == "2026-11-06T09:58:30Z"
    assert built["run_ended_at"] == "2026-11-06T10:05:00Z"
    assert [job["name"] for job in built["jobs"]] == ["e2e", "lint"]


def test_checks_without_times_leave_the_run_instants_unknown_and_no_jobs():
    built = ci.timing(PUSHED, NODE, "red", (check("web", None, None, "FAILURE"),), ("web",))
    assert built["run_started_at"] is None
    assert built["run_ended_at"] is None
    assert "jobs" not in built


def test_a_check_still_running_has_a_start_but_no_end_and_no_job_row():
    running = check("web", "2026-11-06T10:00:30Z", None)
    failed = check("e2e", "2026-11-06T10:00:40Z", "2026-11-06T10:02:00Z", "FAILURE")
    built = ci.timing(PUSHED, NODE, "red", (running, failed), ("web", "e2e"))
    assert built["run_started_at"] == "2026-11-06T10:00:30Z"
    assert built["run_ended_at"] == "2026-11-06T10:02:00Z"
    assert built["jobs"] == [{"name": "e2e", "duration_s": 80.0}]


def test_a_no_verdict_carries_no_run_instants():
    checks = (check("web", "2026-11-06T10:00:30Z", "2026-11-06T10:03:00Z", "CANCELLED"),)
    built = ci.timing(PUSHED, NODE, "no_verdict", checks, ("web",))
    assert built["run_started_at"] is None
    assert built["run_ended_at"] is None
    assert "jobs" not in built


def test_an_unreadable_push_instant_reads_the_run_unknown():
    done = check("web", "2026-11-06T10:00:30Z", "2026-11-06T10:03:00Z")
    built = ci.timing("never", NODE, "green", (done,), ("web",))
    assert built["run_started_at"] is None
    assert built["run_ended_at"] is None
