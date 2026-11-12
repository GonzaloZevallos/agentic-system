"""GH-462: the verdict of a dispatched run of the base's workflow (`dispatch_verdict`), pure."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, DispatchedRun, FailedStage, dispatch_verdict
from tests.core.process.ci_truncated_names import TRUNCATED_PYTEST

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


@pytest.mark.parametrize(
    ("step", "stage"),
    [
        ("Set up job", "setup"),
        ("Run actions/checkout@v7", "checkout"),
        ("Run actions/checkout@v4", "checkout"),
        ("Run checkout tests", "suite"),
        ("e2e checkout flow", "suite"),
        ("Post Run actions/checkout@v7", "suite"),
        ("Checkout", "suite"),
        ("set up job", "suite"),
        ("Set up job 2", "suite"),
        ("Run uv sync --locked", "suite"),
        ("Run astral-sh/setup-uv@v10.2.0", "suite"),
        ("", "suite"),
    ],
)
def test_failed_stage_names_only_the_exact_setup_and_the_checkout_prefix(
    step: str, stage: str
) -> None:
    assert ci.failed_stage(step) == stage


def _staged(stage: FailedStage | None, name: str = "check") -> CiCheck:
    step = {"setup": "Set up job", "checkout": "Run actions/checkout@v7", "suite": "Run pytest"}
    return CiCheck(
        name,
        "COMPLETED",
        "FAILURE",
        f"https://x/job/{name}",
        None if stage is None else step[stage],
    )


def test_a_ci_check_without_a_failed_stage_keeps_the_graphql_shape() -> None:
    assert CiCheck("check", "COMPLETED", "FAILURE", "l").failed_stage is None


@pytest.mark.parametrize("stage", ["checkout", "setup"])
def test_a_job_that_failed_only_at_checkout_or_setup_is_unreachable_checkout_failed(
    stage: FailedStage,
) -> None:
    job = _staged(stage)
    verdict = _verdict([_run(jobs=(job,))])
    assert (verdict.state, verdict.cause, verdict.failing) == (
        "unreachable",
        "checkout_failed",
        (job,),
    )


@pytest.mark.parametrize("stage", ["suite", None])
def test_a_job_that_failed_in_the_suite_or_unreadably_is_red(stage: FailedStage | None) -> None:
    verdict = _verdict([_run(jobs=(_staged(stage),))])
    assert (verdict.state, verdict.cause) == ("red", None)


def test_a_mix_of_checkout_and_suite_failures_is_red() -> None:
    jobs = (_staged("checkout", "lint"), _staged("suite", "check"))
    verdict = _verdict([_run(jobs=jobs)], names=("check", "lint"))
    assert (verdict.state, verdict.cause) == ("red", None)
    assert len(verdict.failing) == 2


def test_an_undeclared_job_failing_at_checkout_does_not_make_a_green_run_unreachable() -> None:
    jobs = (_job("check"), _staged("checkout", "docs"))
    assert _verdict([_run(jobs=jobs)]).state == "green"


def test_a_run_still_in_progress_is_not_judged_even_with_a_red_job() -> None:
    running = _run(status="in_progress", jobs=(_job("check", "FAILURE"),))
    assert _verdict([running], elapsed_s=GRACE + 60).state == "waiting"
    assert _verdict([running], elapsed_s=TIMEOUT).state == "timed_out"


def test_a_dispatched_run_is_never_cancelled_whatever_its_jobs_conclude() -> None:
    """GH-539: the dispatched report has no cancelled suites: a cancelled job is `not_started`."""
    for conclusion in ("CANCELLED", "FAILURE", "SUCCESS", None):
        status = "completed" if conclusion else "in_progress"
        got = _verdict([_run(jobs=(_job(conclusion=conclusion),), status=status)])
        assert got.state != "cancelled"
        assert got.cancelled == ()
    cancelled = _verdict([_run(jobs=(_job(conclusion="CANCELLED"),))])
    assert (cancelled.state, cancelled.cause) == ("unreachable", "not_started")


# -- GH-535: a declared name that is a matrix job id --------------------------------------

PY = ("pytest (areas-1, tests/x)", "pytest (always, y)")


def _matrix(*conclusions: str | None, names: tuple[str, ...] = PY, **over):
    jobs = tuple(
        _job(name, conclusion) for name, conclusion in zip(names, conclusions, strict=True)
    )
    return _run(jobs=jobs, **over)


def test_a_green_matrix_run_with_the_id_declared_is_green() -> None:
    verdict = _verdict([_matrix("SUCCESS", "SUCCESS")], names=("pytest",))
    assert (verdict.state, verdict.cause, verdict.red) == ("green", None, ())


def test_one_red_expansion_is_red_and_names_the_declared_id() -> None:
    verdict = _verdict([_matrix("SUCCESS", "FAILURE")], names=("pytest",))
    assert verdict.state == "red"
    assert verdict.red_names == ("pytest",)
    assert [check.name for check in verdict.failing] == [PY[1]]


def test_one_skipped_expansion_is_jobs_missing() -> None:
    verdict = _verdict([_matrix("SUCCESS", "SKIPPED")], names=("pytest",))
    assert (verdict.state, verdict.cause) == ("unreachable", "jobs_missing")


def test_a_green_run_whose_jobs_are_truncated_matrix_names_is_green() -> None:
    """GH-556: GitHub truncates long matrix names, the closing parenthesis goes with it."""
    verdict = _verdict([_matrix("SUCCESS", "SUCCESS", names=TRUNCATED_PYTEST)], names=("pytest",))
    assert (verdict.state, verdict.cause, verdict.red) == ("green", None, ())


def test_a_matrix_id_with_no_expansion_is_jobs_missing_and_a_prefix_is_not_one() -> None:
    gone = _verdict([_run(jobs=(_job("docs"),))], names=("pytest",))
    assert (gone.state, gone.cause, gone.pending) == ("unreachable", "jobs_missing", ("pytest",))
    shard = _run(jobs=(_job("e2e-shard (1)"),))
    assert _verdict([shard], names=("e2e",)).cause == "jobs_missing"


def test_red_beside_a_pending_expansion_is_red() -> None:
    run = _run(status="in_progress", jobs=(_job(PY[0], "FAILURE"), _job(PY[1], None, "QUEUED")))
    verdict = _verdict([run], names=("pytest",))
    assert verdict.state == "waiting"  # the run is not completed yet: nothing is decided
    done = _run(jobs=(_job(PY[0], "FAILURE"), _job(PY[1], None, "QUEUED")))
    assert _verdict([done], names=("pytest",)).state == "red"


def test_the_failing_returns_carry_the_declared_names() -> None:
    cancelled = _verdict([_matrix("CANCELLED", "SUCCESS")], names=("pytest",))
    assert (cancelled.cause, cancelled.red_names) == ("not_started", ("pytest",))
    job = CiCheck(PY[0], "COMPLETED", "FAILURE", "l", failed_step="Run actions/checkout@v7")
    checkout = _verdict([_run(jobs=(job, _job(PY[1])))], names=("pytest",))
    assert (checkout.cause, checkout.red_names) == ("checkout_failed", ("pytest",))
    assert _verdict([_matrix("SUCCESS", "SUCCESS")], names=("pytest",)).red == ()
    assert _verdict([], elapsed_s=GRACE).red == ()
