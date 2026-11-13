"""GhostWatch (GH-601 part 1): the pure rule that tells a ghost run, a run queued with no job
for longer than a bound, counted on one local clock from the first read that saw it so."""

from __future__ import annotations

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import (
    CiCheck,
    CiReport,
    CiUnreadable,
    DispatchedRun,
    GhostWatch,
)

HEAD = "a" * 40


def _read(running=(), empty=()):
    return CiReport(
        HEAD,
        None,
        None,
        None,
        (),
        running_runs=frozenset(running),
        queued_empty_runs=frozenset(empty),
    )


def _run(run_id=7, status="queued", jobs=(), created_at="2026-11-12T10:00:00Z"):
    return DispatchedRun(
        run_id, "workflow_dispatch", f"ci {HEAD}", status, created_at, "https://x/run", jobs
    )


def test_a_run_seen_at_180_is_a_ghost_at_780_with_bound_600_and_maturing_before():
    watch = GhostWatch().observe_report(_read(["9"], ["9"]), 180.0)
    assert watch.maturing(779.0, 600.0) == frozenset({"9"})
    assert watch.ghosts(779.0, 600.0) == frozenset()
    assert watch.ghosts(780.0, 600.0) == frozenset({"9"})  # exactly the bound counts
    assert watch.maturing(780.0, 600.0) == frozenset()


def test_a_clock_that_went_back_leaves_the_id_maturing_without_raising():
    watch = GhostWatch().observe_report(_read(["9"], ["9"]), 500.0)
    assert watch.ghosts(100.0, 600.0) == frozenset()
    assert watch.maturing(100.0, 600.0) == frozenset({"9"})


def test_a_run_that_gains_a_job_leaves_the_map():
    watch = GhostWatch().observe_report(_read(["9"], ["9"]), 180.0)
    watch = watch.observe_report(_read(["9"], []), 540.0)
    assert watch.ghosts(10_000.0, 600.0) == frozenset()
    assert watch.maturing(10_000.0, 600.0) == frozenset()


def test_a_run_that_is_no_longer_running_leaves_the_map():
    watch = GhostWatch().observe_report(_read(["9"], ["9"]), 180.0)
    watch = watch.observe_report(_read([], []), 200.0)
    assert watch.seen == {}


def test_an_unreadable_read_keeps_the_instant():
    watch = GhostWatch().observe_report(_read(["9"], ["9"]), 180.0)
    after = watch.observe_report(CiUnreadable("gh_unavailable"), 400.0)
    assert after is watch
    assert after.ghosts(780.0, 600.0) == frozenset({"9"})


def test_a_run_never_queued_and_empty_is_never_added():
    watch = GhostWatch().observe_report(_read(["9", "10"], []), 180.0)
    assert watch.seen == {}


def test_a_known_id_keeps_its_first_instant_on_a_later_read():
    watch = GhostWatch().observe_report(_read(["9"], ["9"]), 180.0)
    watch = watch.observe_report(_read(["9"], ["9"]), 300.0)
    assert watch.seen == {"9": 180.0}


def test_observing_does_not_mutate_the_watch_it_was_called_on():
    first = GhostWatch().observe_report(_read(["9"], ["9"]), 180.0)
    first.observe_report(_read([], []), 200.0)
    assert first.seen == {"9": 180.0}


def test_observe_dispatched_adds_a_queued_run_without_jobs_under_a_str_key():
    watch = GhostWatch().observe_dispatched([_run(7)], HEAD, 50.0)
    assert watch.seen == {"7": 50.0}


def test_observe_dispatched_drops_a_completed_run():
    watch = GhostWatch({"7": 50.0}).observe_dispatched([_run(7, status="completed")], HEAD, 90.0)
    assert watch.seen == {}


def test_observe_dispatched_drops_a_queued_run_that_has_jobs():
    job = CiCheck("check", "QUEUED", None, "https://x/job")
    watch = GhostWatch({"7": 50.0}).observe_dispatched([_run(7, jobs=(job,))], HEAD, 90.0)
    assert watch.seen == {}


def test_observe_dispatched_without_a_chosen_run_changes_nothing():
    watch = GhostWatch({"7": 50.0})
    assert ci.chosen_run([], HEAD) is None
    assert watch.observe_dispatched([], HEAD, 90.0) is watch


def test_observe_dispatched_leaves_the_other_ids_alone():
    watch = GhostWatch({"3": 10.0}).observe_dispatched([_run(7)], HEAD, 50.0)
    assert watch.seen == {"3": 10.0, "7": 50.0}


def test_a_known_dispatched_run_keeps_its_first_instant():
    watch = GhostWatch().observe_dispatched([_run(7)], HEAD, 50.0)
    watch = watch.observe_dispatched([_run(7)], HEAD, 90.0)
    assert watch.seen == {"7": 50.0}


def test_an_id_seen_by_a_report_keeps_its_instant_through_observe_dispatched():
    watch = GhostWatch().observe_report(_read(["7"], ["7"]), 20.0)
    watch = watch.observe_dispatched([_run(7)], HEAD, 90.0)
    assert watch.seen == {"7": 20.0}


def test_ghost_run_reads_green_today():
    """Pins today's behaviour, replaced by GH-601 part 2: a ghost run id in `running_runs` and
    `queued_empty_runs` does not stop the exact `check` job being green with `runs_completed`
    not known."""
    green = CiCheck("check", "COMPLETED", "SUCCESS", "https://x/job")
    report = CiReport(
        HEAD,
        None,
        None,
        None,
        (green,),
        runs_completed=None,
        running_runs=frozenset({"9"}),
        queued_empty_runs=frozenset({"9"}),
    )
    assert ci.ci_state(report, ("check",)).state == "green"
