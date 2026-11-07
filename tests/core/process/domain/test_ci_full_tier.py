"""`domain/ci.full_tier_verdict` (GH-376): what the full tier reads from the CI of the
integrated head. Pure; one row per input and the answer it must give, with the inputs it
must reject beside the ones it accepts (ADR-0023)."""

from typing import Literal

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, FullTierVerdict
from agentic.core.process.domain.task import CiCause

HEAD = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"
OTHER = "9f3e1a2b4c5d6e7f8091a2b3c4d5e6f708192a3b"
NAMES = ("unit", "web")
BOUND = 600.0
GRACE = 120.0


def run(name: str, conclusion: str | None, status: str = "COMPLETED") -> CiCheck:
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/runs/{name}")


def report(*checks: CiCheck, sha: str = HEAD) -> CiReport:
    return CiReport(sha, None, None, None, tuple(checks))


def verdict(read: CiReport | CiUnreadable, elapsed_s: float = 0.0) -> FullTierVerdict:
    return ci.full_tier_verdict(read, HEAD, NAMES, elapsed_s, BOUND, GRACE)


def test_every_declared_job_green_is_green() -> None:
    assert verdict(report(run("unit", "SUCCESS"), run("web", "NEUTRAL"))) == FullTierVerdict(
        "green", (), (), None
    )


def test_a_failed_job_is_red_with_its_run() -> None:
    web = run("web", "FAILURE")
    got = verdict(report(run("unit", "SUCCESS"), web))
    assert got == FullTierVerdict("red", (web,), (), None)


def test_a_timed_out_job_is_red_not_unreachable() -> None:
    got = verdict(report(run("unit", "SUCCESS"), run("web", "TIMED_OUT")))
    assert got.state == "red"
    assert got.cause is None


def test_skipped_on_a_declared_name_is_red_nothing_is_inferred() -> None:
    assert verdict(report(run("unit", "SUCCESS"), run("web", "SKIPPED"))).state == "red"


def test_startup_failure_mixed_with_failure_is_red() -> None:
    got = verdict(report(run("unit", "STARTUP_FAILURE"), run("web", "FAILURE")))
    assert got.state == "red"
    assert [c.name for c in got.failing] == ["unit", "web"]


@pytest.mark.parametrize("conclusion", ["CANCELLED", "STARTUP_FAILURE"])
def test_only_jobs_that_never_started_are_unreachable_not_started(conclusion: str) -> None:
    got = verdict(report(run("unit", conclusion), run("web", "CANCELLED")))
    assert (got.state, got.cause) == ("unreachable", "not_started")


def test_a_cancelled_job_beside_a_failed_one_is_red() -> None:
    assert verdict(report(run("unit", "CANCELLED"), run("web", "FAILURE"))).state == "red"


def test_a_pending_job_before_the_bound_is_waiting_and_names_what_is_pending() -> None:
    got = verdict(report(run("unit", "SUCCESS"), run("web", None, "IN_PROGRESS")), BOUND - 1)
    assert got == FullTierVerdict("waiting", (), ("web",), None)


def test_a_job_with_no_run_yet_is_pending_while_the_other_has_one() -> None:
    got = verdict(report(run("unit", "SUCCESS")), BOUND - 1)
    assert (got.state, got.pending) == ("waiting", ("web",))


def test_a_pending_job_at_the_bound_is_timed_out() -> None:
    got = verdict(report(run("unit", "SUCCESS"), run("web", None, "QUEUED")), BOUND)
    assert (got.state, got.pending, got.cause) == ("timed_out", ("web",), None)


def test_a_red_job_wins_over_the_bound() -> None:
    got = verdict(report(run("unit", "FAILURE"), run("web", None, "QUEUED")), BOUND + 1)
    assert got.state == "red"


def test_no_declared_job_on_the_head_is_waiting_then_no_run_at_the_grace() -> None:
    other_jobs = report(run("lint", "SUCCESS"))
    assert verdict(other_jobs, GRACE - 1).state == "waiting"
    at_grace = verdict(other_jobs, GRACE)
    assert (at_grace.state, at_grace.cause) == ("unreachable", "no_run")


def test_a_report_of_another_sha_is_ignored_even_when_green() -> None:
    green_elsewhere = report(run("unit", "SUCCESS"), run("web", "SUCCESS"), sha=OTHER)
    assert verdict(green_elsewhere, GRACE - 1).state == "waiting"
    at_grace = verdict(green_elsewhere, GRACE)
    assert (at_grace.state, at_grace.cause) == ("unreachable", "no_run")


def test_a_short_sha_of_the_head_is_the_same_commit() -> None:
    both_green = (run("unit", "SUCCESS"), run("web", "SUCCESS"))
    assert verdict(report(*both_green, sha=HEAD[:12])).state == "green"


@pytest.mark.parametrize(
    "unreadable", [CiUnreadable("gh_unavailable"), CiUnreadable("gh_unreadable")]
)
def test_an_unreadable_ci_waits_until_the_bound_then_names_its_cause(
    unreadable: CiUnreadable,
) -> None:
    assert verdict(unreadable, BOUND - 1) == FullTierVerdict("waiting", (), NAMES, None)
    at_bound = verdict(unreadable, BOUND)
    assert (at_bound.state, at_bound.cause) == ("unreachable", unreadable.cause)


# --- covering_state (GH-453): what the CI says about the job that covers a deferred check ----


def covering(read, job="e2e", head=HEAD):
    return ci.covering_state(read, job, head)


@pytest.mark.parametrize(
    ("checks", "state"),
    [
        ([run("e2e", "SUCCESS")], "covered"),
        ([run("e2e", "NEUTRAL")], "covered"),
        ([run("e2e", "FAILURE")], "red"),
        ([run("e2e", "TIMED_OUT")], "red"),
        ([run("e2e", "SUCCESS"), run("e2e", "FAILURE")], "red"),
        ([run("e2e", None, status="IN_PROGRESS")], "pending"),
        ([run("e2e", "PENDING")], "pending"),
        ([run("e2e", "SUCCESS"), run("e2e", None, status="QUEUED")], "pending"),
        ([run("e2e", "FAILURE"), run("e2e", None, status="QUEUED")], "red"),
        ([run("e2e", "SKIPPED")], "uncovered"),
        ([run("e2e", "SUCCESS"), run("e2e", "SKIPPED")], "uncovered"),
        ([run("unit", "SUCCESS")], "uncovered"),
        ([], "uncovered"),
        # GH-535: a matrix expansion is the declared id's run, and on a report not known to be
        # complete an expansion-only job is still waiting, never covered.
        ([run("e2e (3.12)", "SUCCESS")], "pending"),
    ],
)
def test_covering_state_of_the_job_on_the_head(checks, state):
    assert covering(report(*checks)) == state


def test_a_report_of_another_head_does_not_cover_even_when_green():
    assert covering(report(run("e2e", "SUCCESS"), sha=OTHER)) == "uncovered"
    assert covering(report(run("e2e", "SUCCESS"), sha=HEAD[:12])) == "covered"


@pytest.mark.parametrize(
    "read", [CiUnreadable("gh_unavailable"), CiUnreadable("gh_unreadable"), None]
)
def test_an_unreadable_or_absent_report_does_not_cover(read):
    assert covering(read) == "uncovered"


def test_covering_state_leaves_ci_state_reading_a_skip_as_red():
    skipped = report(run("e2e", "SKIPPED"))
    assert ci.ci_state(skipped, ("e2e",)).state == "red"
    assert covering(skipped) == "uncovered"


# --- the grace period (GH-462): a CI that never starts is caught well before the bound ------


def test_the_grace_is_below_the_bound_in_these_tests() -> None:
    assert 0 < GRACE < BOUND


@pytest.mark.parametrize("elapsed", [0.0, GRACE - 1])
def test_no_run_on_the_head_before_the_grace_is_waiting(elapsed: float) -> None:
    assert verdict(report(sha=OTHER), elapsed).state == "waiting"


@pytest.mark.parametrize("elapsed", [GRACE, GRACE + 1, BOUND - 1])
def test_no_run_on_the_head_at_and_past_the_grace_is_no_run(elapsed: float) -> None:
    got = verdict(report(), elapsed)
    assert (got.state, got.cause, got.failing) == ("unreachable", "no_run", ())


def test_a_report_of_another_commit_past_the_grace_is_no_run() -> None:
    got = verdict(report(run("unit", "SUCCESS"), run("web", "SUCCESS"), sha=OTHER), GRACE)
    assert (got.state, got.cause) == ("unreachable", "no_run")


@pytest.mark.parametrize("status", ["QUEUED", "IN_PROGRESS"])
def test_a_started_run_past_the_grace_keeps_waiting_until_the_bound(status: str) -> None:
    started = run("unit", None, status)
    past = verdict(report(started), GRACE + 100)
    assert past.state == "waiting"
    assert past.cause is None
    at_bound = verdict(report(started), BOUND)
    assert (at_bound.state, at_bound.cause) == ("timed_out", None)


def test_one_declared_job_reported_and_the_other_missing_is_not_no_run() -> None:
    got = verdict(report(run("unit", "SUCCESS")), GRACE)
    assert (got.state, got.pending) == ("waiting", ("web",))


def test_a_green_head_past_the_grace_is_still_green() -> None:
    both = report(run("unit", "SUCCESS"), run("web", "SUCCESS"))
    assert verdict(both, GRACE + 1).state == "green"


@pytest.mark.parametrize(
    "unreadable", [CiUnreadable("gh_unavailable"), CiUnreadable("gh_unreadable")]
)
def test_an_unreadable_ci_past_the_grace_keeps_waiting_until_the_bound(
    unreadable: CiUnreadable,
) -> None:
    assert verdict(unreadable, GRACE + 1) == FullTierVerdict("waiting", (), NAMES, None)


# -- GH-485: a required name no check run carries is a planning defect, not a slow CI ------

MATRIX = tuple(run(f"e2e ({n})", "SUCCESS") for n in range(1, 5))


def complete(*checks: CiCheck, completed: bool | None = True) -> CiReport:
    return CiReport(HEAD, None, None, None, tuple(checks), completed)


def test_unreported_lists_the_names_no_check_run_carries_in_declared_order_once() -> None:
    read = complete(*MATRIX, run("lint", "SUCCESS"))
    # GH-535: `e2e` is carried by its expansions `e2e (n)`.
    assert ci.unreported(read, ("lint", "pytest", "e2e", "pytest"), True) == ("pytest",)


def test_unreported_matches_exactly_never_by_prefix() -> None:
    assert ci.unreported(complete(run("e2e-shard (1)", "SUCCESS")), ("e2e",), True) == ("e2e",)
    assert ci.unreported(complete(run("e2e", "SUCCESS")), ("e2e",), True) == ()
    assert ci.unreported(complete(*MATRIX), ("e2e",), True) == ()


@pytest.mark.parametrize(
    ("completed", "confirmed"),
    [(None, True), (False, True), (True, False)],
    ids=["status-unknown", "status-false", "not-confirmed"],
)
def test_unreported_decides_nothing_without_a_confirmed_completed_run(completed, confirmed) -> None:
    read = complete(*MATRIX, completed=completed)
    assert ci.unreported(read, ("e2e",), confirmed) == ()


def gh543(read: CiReport, confirmed: bool = True, elapsed_s: float = 0.0) -> FullTierVerdict:
    return ci.full_tier_verdict(read, HEAD, ("integration",), elapsed_s, BOUND, GRACE, confirmed)


def test_gh543_matrix_runs_green_with_the_job_id_required_is_unknown_not_a_timeout() -> None:
    # GH-535: the name is one no run nor expansion carries (a matrix id is now carried).
    assert gh543(complete(*MATRIX)) == FullTierVerdict(
        "unreachable", (), ("integration",), "unknown_names", ("integration",)
    )
    # Before either bound: the wait is not run out first.
    assert gh543(complete(*MATRIX), elapsed_s=BOUND * 5).cause == "unknown_names"


def test_the_same_report_with_the_run_status_not_known_is_a_wait() -> None:
    assert gh543(complete(*MATRIX, completed=None)).state == "waiting"
    # Past the grace it is today's `no_run`, never `unknown_names`.
    assert gh543(complete(*MATRIX, completed=None), elapsed_s=GRACE).cause == "no_run"


def test_the_first_read_alone_is_a_wait_so_a_name_that_appears_next_is_not_unknown() -> None:
    assert gh543(complete(*MATRIX), confirmed=False).state == "waiting"
    assert gh543(complete(*MATRIX, run("integration", "SUCCESS")), confirmed=True).state == "green"


def test_red_wins_over_an_unknown_name() -> None:
    bad = run("unit", "FAILURE")
    got = ci.full_tier_verdict(complete(bad), HEAD, ("unit", "e2e"), 0.0, BOUND, GRACE, True)
    assert got.state == "red" and got.failing == (bad,) and got.unknown == ()


# --- `dispatch_after_no_run` (GH-531): the one place that says a no-run push is dispatched ----


@pytest.mark.parametrize(
    ("state", "cause", "available", "expected"),
    [
        ("unreachable", "no_run", True, True),
        ("unreachable", "no_run", False, False),
        ("unreachable", "publish_failed", True, False),
        ("unreachable", "not_started", True, False),
        ("unreachable", "gh_unavailable", True, False),
        ("unreachable", "unknown_names", True, False),
        ("timed_out", None, True, False),
        ("red", None, True, False),
        ("green", None, True, False),
        ("waiting", None, True, False),
    ],
)
def test_only_a_no_run_push_is_dispatched_and_only_when_the_fallback_is_available(
    state: Literal["green", "red", "waiting", "timed_out", "unreachable"],
    cause: CiCause | None,
    available: bool,
    expected: bool,
) -> None:
    verdict = FullTierVerdict(state, (), (), cause)
    assert ci.dispatch_after_no_run(verdict, available) is expected


@pytest.mark.parametrize(
    ("state", "cause", "expected"),
    [
        ("unreachable", "no_run", True),
        ("unreachable", "not_started", False),
        ("unreachable", "publish_failed", False),
        ("timed_out", None, False),
        ("green", None, False),
    ],
)
def test_only_a_push_with_no_run_is_worth_reading_the_upstream_for(
    state: Literal["green", "red", "waiting", "timed_out", "unreachable"],
    cause: CiCause | None,
    expected: bool,
) -> None:
    assert ci.push_got_no_run(FullTierVerdict(state, (), (), cause)) is expected


# GH-539: a run superseded or cancelled is never red.
OLD, NEW = 42393982482, 42394295598


def job(name: str, conclusion: str | None, run_id: int, status: str = "COMPLETED") -> CiCheck:
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/actions/runs/{run_id}/job/x")


def cancelled_read(*checks: CiCheck, completed: bool | None = True, suites: tuple[int, ...] = ()):
    return CiReport(
        HEAD, None, None, None, tuple(checks), completed, frozenset(str(i) for i in suites)
    )


def test_a_declared_job_failing_only_because_its_needs_were_cancelled_is_cancelled_not_red() -> (
    None
):
    """The issue's case: `web` needs `unit`; `unit` was cancelled and `web` failed with it."""
    old = (job("unit", "CANCELLED", OLD), job("web", "FAILURE", OLD))
    got = verdict(cancelled_read(*old, suites=(OLD,)))
    assert got.state == "cancelled"
    assert got.failing == ()
    assert [check.name for check in got.cancelled] == ["unit", "web"]
    assert got.cause is None


def test_cancelled_waits_until_every_workflow_run_is_known_completed() -> None:
    old = (job("unit", "CANCELLED", OLD), job("web", "FAILURE", OLD))
    for completed in (None, False):
        got = verdict(cancelled_read(*old, completed=completed, suites=(OLD,)))
        assert got.state == "waiting"
        assert got.failing == ()
        assert got.cancelled != ()


def test_a_cancelled_ci_ok_with_a_newer_pending_run_waits_it_is_not_unreachable() -> None:
    old = (job("unit", "CANCELLED", OLD), job("web", "CANCELLED", OLD))
    newer = (job("unit", None, NEW, "IN_PROGRESS"), job("web", None, NEW, "QUEUED"))
    got = verdict(cancelled_read(*old, *newer, completed=None))
    assert got.state == "waiting"
    assert got.cause is None


def test_a_cancelled_job_of_a_cancelled_suite_without_a_newer_run_is_cancelled() -> None:
    old = (job("unit", "SUCCESS", OLD), job("web", "CANCELLED", OLD))
    got = verdict(cancelled_read(*old, suites=(OLD,)))
    assert got.state == "cancelled"
    assert [check.name for check in got.cancelled] == ["web"]


def test_a_newer_green_run_of_the_same_head_makes_the_old_cancelled_one_green() -> None:
    old = (job("unit", "CANCELLED", OLD), job("web", "FAILURE", OLD))
    newer = (job("unit", "SUCCESS", NEW), job("web", "SUCCESS", NEW))
    assert verdict(cancelled_read(*old, *newer)) == FullTierVerdict("green", (), (), None)


def test_a_cancellation_beside_a_really_red_name_is_red() -> None:
    old = (job("unit", "CANCELLED", OLD), job("web", "FAILURE", OLD))
    newer = (job("unit", "FAILURE", NEW), job("web", "SUCCESS", NEW))
    got = verdict(cancelled_read(*old, *newer))
    assert got.state == "red"
    assert [check.name for check in got.failing] == ["unit"]


def test_a_cancelled_name_beside_a_missing_one_is_not_cancelled_yet() -> None:
    old = (job("unit", "CANCELLED", OLD),)
    got = verdict(cancelled_read(*old, suites=(OLD,)))
    assert got.state == "waiting"
    assert got.pending == ("web",)
    assert [check.name for check in got.cancelled] == ["unit"]


def test_a_cancelled_run_the_classification_does_not_know_is_still_not_started() -> None:
    """No run id in the link, a run that is neither superseded nor cancelled: as before."""
    got = verdict(report(run("unit", "SUCCESS"), run("web", "CANCELLED")))
    assert got.state == "unreachable"
    assert got.cause == "not_started"
    alone = verdict(cancelled_read(job("unit", "SUCCESS", NEW), job("web", "CANCELLED", NEW)))
    assert (alone.state, alone.cause) == ("unreachable", "not_started")


def test_startup_failure_without_a_cancellation_signal_is_still_not_started() -> None:
    plain = verdict(cancelled_read(job("unit", "SUCCESS", OLD), job("web", "STARTUP_FAILURE", OLD)))
    assert (plain.state, plain.cause) == ("unreachable", "not_started")
