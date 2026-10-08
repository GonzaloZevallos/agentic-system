"""`domain/ci.py` (GH-216): the one place the CI verdict lives, from structured enum
fields. Pure: one row per input and the answer it must give, with the input it must
reject beside the one it accepts (ADR-0023)."""

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, CiReport, CiVerdict

FULL = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"
OTHER = "9f3e1a2b4c5d6e7f8091a2b3c4d5e6f708192a3b"


def run(name: str, conclusion: str | None, status: str = "COMPLETED") -> CiCheck:
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/runs/{name}")


def report(*checks: CiCheck) -> CiReport:
    return CiReport(FULL, "master", OTHER, "2026-10-07T10:00:00Z", tuple(checks))


GREEN_RUN = run("check", "SUCCESS")


def test_a_declared_check_that_succeeded_is_green() -> None:
    verdict = ci.ci_state(report(GREEN_RUN), ("check",))
    assert verdict == CiVerdict("green", (), ())


def test_neutral_is_green_too() -> None:
    assert ci.ci_state(report(run("check", "NEUTRAL")), ("check",)).state == "green"


@pytest.mark.parametrize(
    "conclusion",
    [
        "FAILURE",
        "CANCELLED",
        "TIMED_OUT",
        "ACTION_REQUIRED",
        "STARTUP_FAILURE",
        "SKIPPED",
        "STALE",
        "ERROR",
        "SOMETHING_GITHUB_ADDS_NEXT_YEAR",
        None,
    ],
)
def test_every_conclusion_that_is_not_green_or_pending_is_red(conclusion: str | None) -> None:
    red = run("check", conclusion)
    verdict = ci.ci_state(report(red), ("check",))
    assert verdict == CiVerdict("red", (red,), ())


@pytest.mark.parametrize("status", ["QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"])
def test_a_run_that_has_not_completed_is_pending(status: str) -> None:
    verdict = ci.ci_state(report(run("check", None, status)), ("check",))
    assert verdict == CiVerdict("pending", (), ())


@pytest.mark.parametrize("conclusion", ["PENDING", "EXPECTED"])
def test_a_status_context_still_pending_or_expected_is_pending(conclusion: str) -> None:
    verdict = ci.ci_state(report(run("check", conclusion, "PENDING")), ("check",))
    assert verdict.state == "pending"
    # `COMPLETED` with those conclusions is pending as well: the conclusion decides.
    assert ci.ci_state(report(run("check", conclusion)), ("check",)).state == "pending"


def test_a_pending_status_with_a_stale_success_conclusion_is_pending_not_green() -> None:
    assert ci.ci_state(report(run("check", "SUCCESS", "IN_PROGRESS")), ("check",)).state == (
        "pending"
    )


def test_a_matrix_name_does_not_match_a_declared_prefix() -> None:
    matrix = run("check (3.12)", "SUCCESS")
    verdict = ci.ci_state(report(matrix), ("check",))
    assert verdict == CiVerdict("pending", (), ("check",))


def test_a_declared_name_is_case_sensitive() -> None:
    verdict = ci.ci_state(report(run("Check", "SUCCESS")), ("check",))
    assert verdict.missing == ("check",)


def test_a_check_that_is_not_declared_is_ignored_even_when_red() -> None:
    verdict = ci.ci_state(report(GREEN_RUN, run("lint", "FAILURE")), ("check",))
    assert verdict == CiVerdict("green", (), ())


def test_two_runs_of_one_name_with_one_red_make_the_name_red() -> None:
    red = run("check", "FAILURE")
    verdict = ci.ci_state(report(GREEN_RUN, red), ("check",))
    assert verdict == CiVerdict("red", (red,), ())
    assert ci.ci_state(report(red, GREEN_RUN), ("check",)).state == "red"


def test_two_runs_of_one_name_green_and_pending_make_it_pending() -> None:
    verdict = ci.ci_state(report(GREEN_RUN, run("check", None, "QUEUED")), ("check",))
    assert verdict.state == "pending"


def test_red_wins_over_pending_in_the_aggregate() -> None:
    red = run("web", "FAILURE")
    verdict = ci.ci_state(
        report(red, run("e2e", None, "IN_PROGRESS"), GREEN_RUN), ("check", "web", "e2e")
    )
    assert verdict == CiVerdict("red", (red,), ())


def test_a_declared_name_that_was_never_reported_is_pending_and_listed() -> None:
    verdict = ci.ci_state(report(GREEN_RUN), ("check", "e2e", "web"))
    assert verdict == CiVerdict("pending", (), ("e2e", "web"))


def test_a_missing_name_does_not_hide_a_red_one() -> None:
    red = run("web", "FAILURE")
    verdict = ci.ci_state(report(red), ("e2e", "web"))
    assert verdict == CiVerdict("red", (red,), ("e2e",))


def test_failing_lists_the_red_runs_in_declared_order() -> None:
    first, second = run("e2e", "FAILURE"), run("web", "TIMED_OUT")
    verdict = ci.ci_state(report(second, first), ("e2e", "web"))
    assert verdict.failing == (first, second)


def test_a_name_declared_twice_counts_once() -> None:
    red = run("web", "FAILURE")
    assert ci.ci_state(report(red), ("web", "web")).failing == (red,)


@pytest.mark.parametrize(
    ("base", "master", "contains", "expected"),
    [
        (None, None, None, None),
        (FULL, None, None, None),
        (None, FULL, None, None),
        (FULL, OTHER, None, None),
        (FULL, FULL, None, True),
        (FULL[:7], FULL, None, True),
        (FULL, OTHER, "yes", True),
        (None, None, "yes", True),
        (FULL, OTHER, "no", False),
        (FULL, OTHER, "unknown", False),
        (None, None, "unknown", False),
        (None, None, "no", False),
        # Same base and master is fresh whatever the containment answer: the run was on it.
        (FULL, FULL, "unknown", True),
        # An empty id matches nothing, so two empty ones are not "the same master".
        ("", "", None, None),
    ],
)
def test_ci_fresh(
    base: str | None, master: str | None, contains: ci.Containment | None, expected: bool | None
) -> None:
    assert ci.ci_fresh(base, master, contains) is expected


@pytest.mark.parametrize(
    ("pushed_head", "pushed_at", "pr_head", "opened_at", "expected"),
    [
        (FULL, "2026-10-07T11:00:00Z", FULL, "2026-10-07T10:00:00Z", "2026-10-07T11:00:00Z"),
        (FULL[:7], "2026-10-07T11:00:00Z", FULL, "2026-10-07T10:00:00Z", "2026-10-07T11:00:00Z"),
        # The push was of another head: its time says nothing about this head's CI.
        (OTHER, "2026-10-07T11:00:00Z", FULL, "2026-10-07T10:00:00Z", "2026-10-07T10:00:00Z"),
        # No recorded pushed head (the run died between push and save, or an old document).
        (None, "2026-10-07T11:00:00Z", FULL, "2026-10-07T10:00:00Z", "2026-10-07T10:00:00Z"),
        (FULL, "2026-10-07T11:00:00Z", None, "2026-10-07T10:00:00Z", "2026-10-07T10:00:00Z"),
        (OTHER, "2026-10-07T11:00:00Z", FULL, None, None),
        (None, None, None, None, None),
    ],
)
def test_ci_wait_anchor(
    pushed_head: str | None,
    pushed_at: str | None,
    pr_head: str | None,
    opened_at: str | None,
    expected: str | None,
) -> None:
    assert ci.ci_wait_anchor(pushed_head, pushed_at, pr_head, opened_at) == expected


def test_a_matching_push_without_a_time_falls_back_to_the_pr_creation() -> None:
    assert ci.ci_wait_anchor(FULL, None, FULL, "2026-10-07T10:00:00Z") == "2026-10-07T10:00:00Z"


def test_the_wait_bound_is_an_hour() -> None:
    assert ci.CI_WAIT_MAX_S == 3600
