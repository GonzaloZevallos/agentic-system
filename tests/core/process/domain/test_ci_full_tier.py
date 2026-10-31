"""`domain/ci.full_tier_verdict` (GH-376): what the full tier reads from the CI of the
integrated head. Pure; one row per input and the answer it must give, with the inputs it
must reject beside the ones it accepts (ADR-0023)."""

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, FullTierVerdict

HEAD = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"
OTHER = "9f3e1a2b4c5d6e7f8091a2b3c4d5e6f708192a3b"
NAMES = ("unit", "web")
BOUND = 600.0


def run(name: str, conclusion: str | None, status: str = "COMPLETED") -> CiCheck:
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/runs/{name}")


def report(*checks: CiCheck, sha: str = HEAD) -> CiReport:
    return CiReport(sha, None, None, None, tuple(checks))


def verdict(read: CiReport | CiUnreadable, elapsed_s: float = 0.0) -> FullTierVerdict:
    return ci.full_tier_verdict(read, HEAD, NAMES, elapsed_s, BOUND)


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


def test_no_declared_job_on_the_head_is_waiting_then_no_run() -> None:
    other_jobs = report(run("lint", "SUCCESS"))
    assert verdict(other_jobs, BOUND - 1).state == "waiting"
    at_bound = verdict(other_jobs, BOUND)
    assert (at_bound.state, at_bound.cause) == ("unreachable", "no_run")


def test_a_report_of_another_sha_is_ignored_even_when_green() -> None:
    green_elsewhere = report(run("unit", "SUCCESS"), run("web", "SUCCESS"), sha=OTHER)
    assert verdict(green_elsewhere, BOUND - 1).state == "waiting"
    at_bound = verdict(green_elsewhere, BOUND)
    assert (at_bound.state, at_bound.cause) == ("unreachable", "no_run")


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
        ([run("e2e (3.12)", "SUCCESS")], "uncovered"),
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
