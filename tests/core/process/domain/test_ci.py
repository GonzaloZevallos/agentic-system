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


def test_a_matrix_expansion_matches_the_declared_id() -> None:
    matrix = run("check (3.12)", "SUCCESS")
    assert ci.matches("check", matrix.name)
    complete = CiReport(FULL, "master", OTHER, "2026-10-07T10:00:00Z", (matrix,), True)
    assert ci.ci_state(complete, ("check",)) == CiVerdict("green", (), ())
    # Not known to be complete: an expansion-only name is never green (GH-535).
    assert ci.ci_state(report(matrix), ("check",)).state == "pending"


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


def test_pending_names_lists_only_declared_names_with_a_run_still_waiting():
    report = CiReport(
        "a" * 40,
        None,
        None,
        None,
        (
            CiCheck("web", "IN_PROGRESS", None, "l1"),
            CiCheck("e2e", "COMPLETED", "SUCCESS", "l2"),
            CiCheck("other", "QUEUED", None, "l3"),
        ),
    )
    assert ci.pending_names(report, ("web", "e2e", "gone")) == ("web",)
    assert ci.pending_names(report, ("e2e",)) == ()


# -- run_id_of_link (GH-463) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("link", "run_id"),
    [
        ("https://github.com/o/r/actions/runs/123456789/job/987", "123456789"),
        ("https://github.com/o/r/actions/runs/42", "42"),
        ("https://github.com/o/r/actions/runs/42/", "42"),
        ("https://github.com/o/r/actions/runs/42?pr=7", "42"),
    ],
)
def test_a_details_url_with_a_run_segment_gives_its_run_id(link: str, run_id: str) -> None:
    assert ci.run_id_of_link(link) == run_id


@pytest.mark.parametrize(
    "link",
    [
        "",
        "https://github.com/o/r/pull/7",
        "https://ci.example.com/build/17",  # a commit status of another system
        "https://github.com/o/r/actions/runs/",
        "https://github.com/o/r/actions/runs/abc/job/1",
        "https://github.com/o/r/actions/runs/12abc",
        "not a url at all /actions/runs/",
    ],
)
def test_a_link_with_no_parsable_run_segment_gives_none(link: str) -> None:
    assert ci.run_id_of_link(link) is None


# GH-539: a run superseded or cancelled is not red. Links carry the Actions run id, which is
# what the classification reads.
def job(name: str, conclusion: str | None, run_id: int, status: str = "COMPLETED") -> CiCheck:
    link = f"https://github.com/o/r/actions/runs/{run_id}/job/{name}"
    return CiCheck(name, status, conclusion, link)


OLD, NEW = 42393982482, 42394295598
DECLARED = ("docs", "pytest", "e2e", "ci-ok")


def old_run_cancelled_by_a_newer_push() -> tuple[CiCheck, ...]:
    """The GH-548 shape: three jobs cancelled, the jobs that need them (and `ci-ok`) failed."""
    return (
        job("docs", "CANCELLED", OLD),
        job("pytest", "CANCELLED", OLD),
        job("e2e", "FAILURE", OLD),
        job("ci-ok", "FAILURE", OLD),
    )


def newer_run(conclusion: str | None, status: str = "COMPLETED") -> tuple[CiCheck, ...]:
    return tuple(job(name, conclusion, NEW, status) for name in DECLARED)


def test_gh_548_old_run_with_a_newer_green_run_of_the_head_is_green() -> None:
    checks = old_run_cancelled_by_a_newer_push() + newer_run("SUCCESS")
    assert ci.ci_state(report(*checks), DECLARED) == CiVerdict("green", (), ())


def test_gh_548_old_run_with_a_newer_run_still_going_is_pending_not_red() -> None:
    checks = old_run_cancelled_by_a_newer_push() + newer_run(None, "IN_PROGRESS")
    verdict = ci.ci_state(report(*checks), DECLARED)
    assert (verdict.state, verdict.failing, verdict.cancelled) == ("pending", (), ())


def test_gh_548_old_run_with_a_newer_really_red_run_is_red_with_the_new_run_only() -> None:
    new_red = job("e2e", "FAILURE", NEW)
    checks = old_run_cancelled_by_a_newer_push() + (new_red,)
    verdict = ci.ci_state(report(*checks), ("e2e",))
    assert verdict.state == "red"
    assert verdict.failing == (new_red,)


def test_an_old_run_alone_with_its_suite_cancelled_is_pending_and_names_its_checks() -> None:
    checks = old_run_cancelled_by_a_newer_push()
    read = CiReport(FULL, None, None, None, checks, True, frozenset({str(OLD)}))
    verdict = ci.ci_state(read, ("e2e", "ci-ok"))
    assert verdict.state == "pending"
    assert verdict.failing == ()
    assert [check.name for check in verdict.cancelled] == ["e2e", "ci-ok"]


def test_an_old_run_alone_with_its_suite_failed_stays_red() -> None:
    """The suite conclusion is what says the cancellation: without a newer run or it, red."""
    verdict = ci.ci_state(report(*old_run_cancelled_by_a_newer_push()), ("e2e", "ci-ok"))
    assert verdict.state == "red"
    assert verdict.cancelled == ()


def test_a_real_red_with_fail_fast_cancelled_siblings_and_a_newer_green_run_is_green() -> None:
    old = (job("docs", "CANCELLED", OLD), job("pytest", "FAILURE", OLD))
    checks = old + (job("docs", "SUCCESS", NEW), job("pytest", "SUCCESS", NEW))
    assert ci.ci_state(report(*checks), ("docs", "pytest")).state == "green"


def test_a_real_red_with_cancelled_siblings_alone_and_the_suite_failed_is_red() -> None:
    old = (job("docs", "CANCELLED", OLD), job("pytest", "FAILURE", OLD))
    assert ci.ci_state(report(*old), ("docs", "pytest")).state == "red"


def test_a_red_run_without_any_cancelled_check_stays_red_beside_a_newer_green_one() -> None:
    old = (job("pytest", "FAILURE", OLD), job("docs", "SUCCESS", OLD))
    checks = old + (job("pytest", "SUCCESS", NEW), job("docs", "SUCCESS", NEW))
    verdict = ci.ci_state(report(*checks), ("pytest", "docs"))
    assert verdict.state == "red"
    assert [check.name for check in verdict.failing] == ["pytest"]


def test_the_newer_run_is_compared_as_an_integer_not_as_text() -> None:
    old, newer = 99, 100  # "99" > "100" as text
    checks = (job("ci-ok", "CANCELLED", old), job("ci-ok", "SUCCESS", newer))
    assert ci.ci_state(report(*checks), ("ci-ok",)).state == "green"
    reversed_ids = (job("ci-ok", "CANCELLED", newer), job("ci-ok", "SUCCESS", old))
    assert ci.ci_state(report(*reversed_ids), ("ci-ok",)).state == "red"


def test_a_check_with_no_readable_run_id_keeps_todays_verdict() -> None:
    cancelled = CiCheck("ci-ok", "COMPLETED", "CANCELLED", "https://github.com/o/r/runs/ci-ok")
    green = job("ci-ok", "SUCCESS", NEW)
    read = CiReport(FULL, None, None, None, (cancelled, green), True, frozenset({str(NEW)}))
    assert ci.ci_state(read, ("ci-ok",)).state == "red"


def test_a_green_check_of_a_cancelled_suite_is_still_green() -> None:
    read = CiReport(
        FULL, None, None, None, (job("unit", "SUCCESS", OLD),), True, frozenset({str(OLD)})
    )
    assert ci.ci_state(read, ("unit",)) == CiVerdict("green", (), ())


def test_red_on_base_and_base_green_read_a_cancelled_base_as_not_red_and_not_green() -> None:
    base = CiReport(
        FULL, None, None, None, old_run_cancelled_by_a_newer_push(), True, frozenset({str(OLD)})
    )
    head = report(job("ci-ok", "FAILURE", NEW), job("e2e", "FAILURE", NEW))
    assert ci.red_on_base(head, base, ("ci-ok", "e2e")) == ()
    assert ci.base_green(base, ("ci-ok", "e2e")) is False
    real_base = report(job("ci-ok", "FAILURE", OLD))
    assert ci.red_on_base(head, real_base, ("ci-ok",)) == ("ci-ok",)


def test_red_checks_and_run_link_leave_a_cancelled_run_out() -> None:
    read = CiReport(
        FULL, None, None, None, old_run_cancelled_by_a_newer_push(), True, frozenset({str(OLD)})
    )
    assert ci.red_checks(read) == ()
    assert ci.run_link(read, "ci-ok") is None
    assert ci.red_checks(report(*old_run_cancelled_by_a_newer_push())) != ()


def test_a_rerun_is_restarted_only_when_a_read_shows_its_run_outside_completed() -> None:
    running = CiReport(
        FULL, "master", OTHER, "2026-10-07T10:00:00Z", (), running_runs=frozenset({"77"})
    )
    assert ci.rerun_restarted(running, "77") is True
    # Rejected: another run, a report with none running, and a read that is not a report.
    assert ci.rerun_restarted(running, "78") is False
    assert ci.rerun_restarted(report(), "77") is False
    assert ci.rerun_restarted(ci.CiUnreadable("gh_unavailable"), "77") is False
