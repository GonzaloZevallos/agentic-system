"""GH-535: a declared CI name that is a matrix job id names all its expansions
`<id> (<values>)`. The one matcher (`matches`, `runs_of`) and every verdict built on it, pure.
Same seams as `test_ci.py`; a separate file only because that one is about plain names."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable

HEAD = "a" * 40
BASE = "b" * 40


def run(name: str, conclusion: str | None = "SUCCESS", status: str = "COMPLETED") -> CiCheck:
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/job/{name}")


def pending(name: str) -> CiCheck:
    return run(name, None, "IN_PROGRESS")


def report(*checks: CiCheck, completed: bool | None = True, sha: str = HEAD) -> CiReport:
    return CiReport(sha, None, None, None, tuple(checks), completed)


MATRIX = ("pytest (areas-1, tests/x)", "pytest (always, y)", "pytest (areas-2, z)")


# -- matches / runs_of ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "check_name", "expected"),
    [
        ("pytest", "pytest", True),
        ("pytest", "pytest (areas-1, tests/x)", True),
        ("pytest", "pytest (a (b))", True),
        # Never a bare prefix, never an empty expansion, never another name.
        ("e2e", "e2e-shard (1)", False),
        ("pytest", "pytest-extra", False),
        ("pytest", "pytest ()", False),
        ("pytest", "pytest (", False),
        ("pytest", "pytest (a) tail", False),
        ("pytest", "Pytest (a)", False),
        ("pytest", "my pytest (a)", False),
        # The declared name is literal text, not a pattern.
        ("a.c", "abc (1)", False),
        ("a.c", "a.c (1)", True),
    ],
)
def test_matches(name: str, check_name: str, expected: bool) -> None:
    assert ci.matches(name, check_name) is expected


def test_exact_claims_come_before_expansions() -> None:
    fast = run("check (fast)")
    got = ci.runs_of([fast], ("check", "check (fast)"))
    assert got == {"check": (), "check (fast)": (fast,)}


def test_a_name_with_an_exact_run_counts_only_exact_runs() -> None:
    exact, expanded = run("check"), run("check (3.12)", "FAILURE")
    assert ci.runs_of([expanded, exact], ("check",)) == {"check": (exact,)}


def test_a_run_is_claimed_by_at_most_one_name() -> None:
    both = run("a (b) (c)")
    got = ci.runs_of([both], ("a", "a (b)"))
    assert got == {"a": (both,), "a (b)": ()}


def test_a_matrix_id_claims_its_expansions_in_report_order() -> None:
    first, other, second = run(MATRIX[0]), run("docs"), run(MATRIX[1])
    assert ci.runs_of([first, other, second], ("pytest", "docs")) == {
        "pytest": (first, second),
        "docs": (other,),
    }


# -- ci_state ------------------------------------------------------------------------------


def test_a_matrix_id_is_green_when_every_expansion_is_green_on_a_complete_report() -> None:
    verdict = ci.ci_state(report(*map(run, MATRIX)), ("pytest",))
    assert (verdict.state, verdict.failing, verdict.missing, verdict.red) == ("green", (), (), ())


def test_one_red_expansion_makes_the_id_red_and_names_the_declared_id() -> None:
    red = run(MATRIX[1], "FAILURE")
    verdict = ci.ci_state(report(run(MATRIX[0]), red, run(MATRIX[2])), ("pytest",))
    assert verdict.state == "red"
    assert verdict.failing == (red,)
    assert verdict.red == (("pytest", (red,)),)
    assert verdict.red_names == ("pytest",)


def test_red_wins_over_a_pending_expansion() -> None:
    red = run(MATRIX[0], "FAILURE")
    verdict = ci.ci_state(report(red, pending(MATRIX[1])), ("pytest",))
    assert (verdict.state, verdict.failing) == ("red", (red,))


def test_a_pending_expansion_makes_the_id_pending() -> None:
    verdict = ci.ci_state(report(run(MATRIX[0]), pending(MATRIX[1])), ("pytest",))
    assert (verdict.state, verdict.missing) == ("pending", ())


@pytest.mark.parametrize("completed", [None, False])
def test_an_expansion_only_name_is_never_green_while_runs_are_not_completed(
    completed: bool | None,
) -> None:
    verdict = ci.ci_state(report(*map(run, MATRIX), completed=completed), ("pytest",))
    assert (verdict.state, verdict.missing, verdict.red) == ("pending", (), ())


def test_a_red_expansion_is_red_even_when_runs_are_not_completed() -> None:
    verdict = ci.ci_state(
        report(run(MATRIX[0], "FAILURE"), run(MATRIX[1]), completed=None), ("pytest",)
    )
    assert verdict.state == "red"


def test_an_exact_name_stays_green_on_a_report_that_is_not_completed() -> None:
    assert ci.ci_state(report(run("check"), completed=None), ("check",)).state == "green"


def test_exact_check_beside_a_red_expansion_is_green() -> None:
    verdict = ci.ci_state(report(run("check"), run("check (3.12)", "FAILURE")), ("check",))
    assert (verdict.state, verdict.red) == ("green", ())


def test_a_name_with_no_expansion_is_missing_and_a_prefix_is_not_one() -> None:
    verdict = ci.ci_state(report(run("e2e-shard (1)")), ("e2e",))
    assert (verdict.state, verdict.missing) == ("pending", ("e2e",))
    assert ci.ci_state(report(), ("pytest",)).missing == ("pytest",)


def test_a_plain_name_beside_its_fast_variant_leaves_the_plain_one_missing() -> None:
    verdict = ci.ci_state(report(run("check (fast)")), ("check", "check (fast)"))
    assert verdict.missing == ("check",)


def test_red_lists_declared_names_in_declared_order() -> None:
    web, py = run("web", "FAILURE"), run(MATRIX[0], "FAILURE")
    verdict = ci.ci_state(report(web, py), ("pytest", "web"))
    assert verdict.red == (("pytest", (py,)), ("web", (web,)))
    assert verdict.failing == (py, web)


def test_a_name_declared_twice_counts_once_on_a_matrix() -> None:
    red = run(MATRIX[0], "FAILURE")
    assert ci.ci_state(report(red), ("pytest", "pytest")).red == (("pytest", (red,)),)


def test_a_status_context_keeps_its_exact_reading() -> None:
    status = CiCheck("ci/legacy", "PENDING", "PENDING", "")
    assert ci.ci_state(report(status, completed=None), ("ci/legacy",)).state == "pending"


# -- pending_names / unreported ------------------------------------------------------------


def test_pending_names_includes_a_matrix_id_with_a_pending_expansion() -> None:
    rep = report(run(MATRIX[0]), pending(MATRIX[1]), run("web"))
    assert ci.pending_names(rep, ("pytest", "web")) == ("pytest",)


def test_pending_names_includes_an_expansion_only_name_on_an_incomplete_report() -> None:
    rep = report(*map(run, MATRIX), completed=None)
    assert ci.pending_names(rep, ("pytest",)) == ("pytest",)
    assert ci.pending_names(report(*map(run, MATRIX)), ("pytest",)) == ()


def test_unreported_carries_a_matrix_id_with_expansions_and_reports_the_rest() -> None:
    rep = report(*map(run, MATRIX), run("e2e-shard (1)"))
    assert ci.unreported(rep, ("pytest", "e2e", "gone"), True) == ("e2e", "gone")
    # Not confirmed or not completed: nothing is unknown yet.
    assert ci.unreported(rep, ("e2e",), False) == ()
    assert ci.unreported(report(*map(run, MATRIX), completed=None), ("e2e",), True) == ()


# -- covering_state ------------------------------------------------------------------------


def test_covering_state_over_a_matrix_job() -> None:
    assert ci.covering_state(report(*map(run, MATRIX)), "pytest", HEAD) == "covered"
    assert ci.covering_state(report(*map(run, MATRIX), completed=None), "pytest", HEAD) == (
        "pending"
    )
    assert ci.covering_state(report(run(MATRIX[0]), pending(MATRIX[1])), "pytest", HEAD) == (
        "pending"
    )
    assert ci.covering_state(report(run(MATRIX[0], "FAILURE")), "pytest", HEAD) == "red"
    assert ci.covering_state(report(run(MATRIX[0]), run(MATRIX[1], "SKIPPED")), "pytest", HEAD) == (
        "uncovered"
    )
    assert ci.covering_state(report(run("docs")), "pytest", HEAD) == "uncovered"
    assert ci.covering_state(CiUnreadable("gh_unavailable"), "pytest", HEAD) == "uncovered"


def test_covering_state_of_an_exact_job_ignores_completeness() -> None:
    assert ci.covering_state(report(run("e2e"), completed=None), "e2e", HEAD) == "covered"


# -- red_on_base / base_green --------------------------------------------------------------


def test_red_on_base_compares_declared_names_across_different_expansions() -> None:
    head = report(run("pytest (a)", "FAILURE"), run("web"))
    base = report(run("pytest (b)", "FAILURE"), run("web"))
    assert ci.red_on_base(head, base, ("pytest", "web")) == ("pytest",)
    assert ci.red_names_on_base(base, ("pytest", "web")) == ("pytest",)
    assert ci.red_on_base(head, None, ("pytest",)) == ()


def test_red_on_base_does_not_cross_a_red_head_with_a_green_base() -> None:
    head = report(run("pytest (a)", "FAILURE"))
    base = report(run("pytest (a)"), run("pytest (b)"))
    assert ci.red_on_base(head, base, ("pytest",)) == ()


def test_base_green_over_a_matrix_needs_a_completed_green_report() -> None:
    assert ci.base_green(report(*map(run, MATRIX)), ("pytest",)) is True
    assert ci.base_green(report(*map(run, MATRIX), completed=None), ("pytest",)) is False
    assert ci.base_green(report(run(MATRIX[0], "FAILURE")), ("pytest",)) is False


# -- run_link ------------------------------------------------------------------------------


def test_run_link_of_a_matrix_id_is_its_first_red_expansion() -> None:
    rep = report(run(MATRIX[0]), run(MATRIX[1], "FAILURE"), run(MATRIX[2], "FAILURE"))
    assert ci.run_link(rep, "pytest") == f"https://github.com/o/r/job/{MATRIX[1]}"
    assert ci.run_link(report(*map(run, MATRIX)), "pytest") is None


# -- timing --------------------------------------------------------------------------------


def test_timing_counts_the_expansions_of_a_declared_matrix_id() -> None:
    def job(name: str, start: str, end: str) -> CiCheck:
        return CiCheck(name, "COMPLETED", "SUCCESS", "l", started_at=start, completed_at=end)

    checks = [
        job("pytest (a)", "2026-11-06T10:00:10Z", "2026-11-06T10:03:10Z"),
        job("pytest (b)", "2026-11-06T10:00:20Z", "2026-11-06T10:05:00Z"),
        job("docs", "2026-11-06T10:00:30Z", "2026-11-06T10:09:00Z"),
    ]
    got = ci.timing("2026-11-06T10:00:00Z", "2026-11-06T09:59:00Z", "green", checks, ("pytest",))
    assert got["run_started_at"] == "2026-11-06T10:00:10Z"
    assert got["run_ended_at"] == "2026-11-06T10:05:00Z"
    assert got["jobs"] == [
        {"name": "pytest (a)", "duration_s": 180.0},
        {"name": "pytest (b)", "duration_s": 280.0},
    ]


# -- full_tier_verdict ---------------------------------------------------------------------


def _full(rep: CiReport, names: tuple[str, ...] = ("pytest",), elapsed: float = 0.0):
    return ci.full_tier_verdict(rep, HEAD, names, elapsed, 600.0, 120.0)


@pytest.mark.parametrize("completed", [None, False])
def test_full_tier_waits_on_a_matrix_id_until_the_report_is_complete(
    completed: bool | None,
) -> None:
    got = _full(report(*map(run, MATRIX), completed=completed))
    assert (got.state, got.pending, got.red) == ("waiting", ("pytest",), ())
    assert _full(report(*map(run, MATRIX), completed=completed), elapsed=600.0).state == (
        "timed_out"
    )


def test_full_tier_is_green_on_a_complete_matrix() -> None:
    assert _full(report(*map(run, MATRIX), completed=True)).state == "green"


def test_full_tier_red_carries_the_declared_name() -> None:
    red = run(MATRIX[0], "FAILURE")
    got = _full(report(red, run("web", "FAILURE")), ("pytest", "web"))
    assert (got.state, got.failing, got.red) == (
        "red",
        (red, run("web", "FAILURE")),
        (("pytest", (red,)), ("web", (run("web", "FAILURE"),))),
    )


def test_full_tier_not_started_carries_the_declared_name() -> None:
    cancelled = run(MATRIX[0], "CANCELLED")
    got = _full(report(cancelled))
    assert (got.state, got.cause, got.red) == (
        "unreachable",
        "not_started",
        (("pytest", (cancelled,)),),
    )


def test_every_other_full_tier_return_has_no_red() -> None:
    assert _full(report()).red == ()
    assert _full(CiUnreadable("gh_unavailable")).red == ()
    assert _full(report(*map(run, MATRIX))).red == ()


def test_unknown_names_do_not_flag_a_matrix_id_with_expansions() -> None:
    rep = report(*map(run, MATRIX), run("web", None, "QUEUED"))
    got = ci.full_tier_verdict(rep, HEAD, ("pytest", "web"), 0.0, 600.0, 120.0, True)
    assert (got.state, got.unknown) == ("waiting", ())


# -- ci_findings ---------------------------------------------------------------------------


def test_one_finding_per_declared_name_with_the_count_of_red_expansions() -> None:
    head = report(
        run("pytest (a)", "FAILURE"),
        run("pytest (b)", "FAILURE"),
        run("pytest (c)"),
        run("pytest (d)"),
        run("pytest (e)", "FAILURE"),
    )
    [finding] = ci.ci_findings(head, None, ("pytest",), [])
    assert finding["location"] == "CI: pytest"
    assert finding["severity"] == "blocking"
    assert "3 of 5 expansions" in finding["claim"]
    assert finding["evidence"] == "https://github.com/o/r/job/pytest (a)"


def test_an_exact_name_keeps_todays_claim() -> None:
    [finding] = ci.ci_findings(report(run("web", "FAILURE")), None, ("web",), [])
    assert finding["claim"] == "the declared CI check `web` is red on the head under review"


def test_different_expansions_red_on_head_and_base_are_one_pre_existing_observation() -> None:
    head = report(run("pytest (a)", "FAILURE"))
    master = report(run("pytest (b)", "FAILURE"))
    [finding] = ci.ci_findings(head, master, ("pytest",), [])
    assert finding["location"] == "CI: pytest"
    assert finding["severity"] == "observation"
    assert "https://github.com/o/r/job/pytest (a)" in finding["evidence"]
    assert "https://github.com/o/r/job/pytest (b)" in finding["evidence"]
    assert "(head)" in finding["evidence"]
    assert "(base branch)" in finding["evidence"]


def test_a_matrix_red_on_head_only_is_blocking_when_the_base_is_green() -> None:
    head = report(run("pytest (a)", "FAILURE"))
    master = report(run("pytest (a)"))
    [finding] = ci.ci_findings(head, master, ("pytest",), [])
    assert finding["severity"] == "blocking"
