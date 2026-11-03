"""GH-462: the verdict of a dispatched run of the base's workflow (`dispatch_verdict`), pure."""

from __future__ import annotations

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, DispatchedRun, dispatch_verdict

HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OTHER = "f" * 40
GRACE = 180.0
TIMEOUT = 1500.0


def _job(name: str = "check", conclusion: str | None = "SUCCESS", status: str = "COMPLETED"):
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/actions/runs/1/job/{name}")


def _run(
    *,
    run_id: int = 1,
    event: str = "workflow_dispatch",
    title: str = f"CI {HEAD}",
    status: str = "completed",
    created_at: str = "2026-11-03T10:00:00Z",
    jobs: tuple[CiCheck, ...] = (_job(),),
) -> DispatchedRun:
    return DispatchedRun(run_id, event, title, status, created_at, "https://x/runs/1", jobs)


def _verdict(runs: list[DispatchedRun], elapsed_s: float = 10.0, names=("check",)):
    return dispatch_verdict(runs, HEAD, names, elapsed_s, GRACE, TIMEOUT)


def test_the_skew_margin_is_a_named_constant() -> None:
    assert ci.CI_DISPATCH_SKEW_S == 120


def test_runs_for_another_sha_or_with_only_the_short_sha_are_ignored() -> None:
    other = _run(title=f"CI {OTHER}")
    short = _run(title=f"CI {HEAD[:7]}")
    assert _verdict([other, short]).state == "waiting"
    late = _verdict([other, short], elapsed_s=GRACE)
    assert (late.state, late.cause) == ("unreachable", "no_run")


def test_a_push_run_whose_title_holds_the_sha_is_ignored() -> None:
    verdict = _verdict([_run(event="push")], elapsed_s=GRACE + 1)
    assert (verdict.state, verdict.cause) == ("unreachable", "no_run")


def test_the_newest_matching_run_by_created_at_wins_whatever_the_order() -> None:
    old = _run(run_id=1, created_at="2026-11-03T10:00:00Z", jobs=(_job(conclusion="FAILURE"),))
    new = _run(run_id=2, created_at="2026-11-03T10:05:00Z", jobs=(_job(conclusion="SUCCESS"),))
    assert _verdict([old, new]).state == "green"
    assert _verdict([new, old]).state == "green"


def test_no_run_waits_before_the_grace_and_is_unreachable_from_it() -> None:
    before = _verdict([], elapsed_s=GRACE - 1)
    assert (before.state, before.cause, before.pending) == ("waiting", None, ("check",))
    at = _verdict([], elapsed_s=GRACE)
    after = _verdict([], elapsed_s=GRACE + 600)
    assert (at.state, at.cause) == ("unreachable", "no_run")
    assert (after.state, after.cause) == ("unreachable", "no_run")


def test_a_running_run_waits_until_the_timeout_and_then_times_out() -> None:
    running = _run(status="in_progress", jobs=(_job(conclusion=None, status="IN_PROGRESS"),))
    before = _verdict([running], elapsed_s=TIMEOUT - 1)
    assert (before.state, before.pending) == ("waiting", ("check",))
    assert _verdict([running], elapsed_s=TIMEOUT).state == "timed_out"
    assert _verdict([running], elapsed_s=GRACE + 5000).state == "timed_out"


def test_a_running_run_is_not_cut_at_the_grace() -> None:
    queued = _run(status="queued", jobs=())
    assert _verdict([queued], elapsed_s=GRACE + 60).state == "waiting"


def test_a_completed_run_without_the_declared_job_is_jobs_missing() -> None:
    verdict = _verdict([_run(jobs=(_job("lint"),))], elapsed_s=TIMEOUT + 1)
    assert (verdict.state, verdict.cause) == ("unreachable", "jobs_missing")


def test_a_completed_run_with_the_declared_job_skipped_is_jobs_missing_not_red() -> None:
    skipped = _run(jobs=(_job("check", "SKIPPED"),))
    verdict = _verdict([skipped])
    assert (verdict.state, verdict.cause) == ("unreachable", "jobs_missing")
    assert _verdict([skipped], elapsed_s=TIMEOUT + 1).cause == "jobs_missing"


def test_jobs_missing_takes_precedence_over_a_red_job() -> None:
    run = _run(jobs=(_job("lint", "FAILURE"),))
    assert _verdict([run], names=("check", "lint")).cause == "jobs_missing"


def test_green_check_with_an_extra_matrix_job_is_green() -> None:
    run = _run(jobs=(_job("check"), _job("check (3.12)", "FAILURE"), _job("docs", "FAILURE")))
    verdict = _verdict([run])
    assert (verdict.state, verdict.failing, verdict.cause) == ("green", (), None)


def test_a_red_declared_job_is_red() -> None:
    red = _job("check", "FAILURE")
    verdict = _verdict([_run(jobs=(red,))])
    assert (verdict.state, verdict.failing) == ("red", (red,))


def test_a_declared_job_that_did_not_start_is_unreachable_not_started() -> None:
    for conclusion in ("STARTUP_FAILURE", "CANCELLED"):
        verdict = _verdict([_run(jobs=(_job("check", conclusion),))])
        assert (verdict.state, verdict.cause) == ("unreachable", "not_started")
