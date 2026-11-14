"""`stage0_window.ci_state` (GH-694): a commit's CI read, as the `CiState` the rotation takes."""

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable
from agentic.core.process.domain.stage0_window import ci_state

HEAD = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"


def job(
    name: str, conclusion: str | None = "SUCCESS", status: str = "COMPLETED", run_id: int = 1
) -> CiCheck:
    return CiCheck(name, status, conclusion, f"https://github.com/o/r/actions/runs/{run_id}/job/x")


def report(*checks: CiCheck, cancelled: frozenset[str] = frozenset()) -> CiReport:
    return CiReport(HEAD, None, None, None, tuple(checks), True, cancelled_runs=cancelled)


CANCELLED = job("tests", "CANCELLED", run_id=7)
NAMES = frozenset({"tests", "lint"})


def test_all_names_green_is_green() -> None:
    assert ci_state(report(job("tests"), job("lint")), NAMES) == "green"


def test_a_red_name_is_red_even_beside_a_pending_one() -> None:
    checks = report(job("tests", "FAILURE"), job("lint", None, "IN_PROGRESS"))
    assert ci_state(checks, NAMES) == "red"


def test_a_pending_name_is_pending() -> None:
    assert ci_state(report(job("tests"), job("lint", None, "QUEUED")), NAMES) == "pending"


def test_a_missing_name_is_pending() -> None:
    assert ci_state(report(job("tests")), NAMES) == "pending"


def test_a_name_with_only_cancelled_runs_is_cancelled_never_red() -> None:
    found = ci_state(report(CANCELLED, job("lint"), cancelled=frozenset({"7"})), NAMES)
    assert found == "cancelled"


def test_cancelled_beside_pending_is_pending() -> None:
    checks = report(CANCELLED, job("lint", None, "QUEUED"), cancelled=frozenset({"7"}))
    assert ci_state(checks, NAMES) == "pending"


def test_a_cancelled_run_beside_a_green_one_of_the_same_name_is_green() -> None:
    checks = report(CANCELLED, job("tests", run_id=8), job("lint"), cancelled=frozenset({"7"}))
    assert ci_state(checks, NAMES) == "green"


def test_a_red_run_that_is_not_cancelled_is_red_whatever_is_cancelled_beside_it() -> None:
    checks = report(CANCELLED, job("lint", "FAILURE"), cancelled=frozenset({"7"}))
    assert ci_state(checks, NAMES) == "red"


def test_skipped_stays_red_as_at_the_merge_gate() -> None:
    assert ci_state(report(job("tests", "SKIPPED")), frozenset({"tests"})) == "red"


@pytest.mark.parametrize("names", [None, frozenset()])
def test_no_declared_ci_is_unknown_even_with_green_runs(names: frozenset[str] | None) -> None:
    assert ci_state(report(job("tests")), names) == "unknown"


@pytest.mark.parametrize("read", [CiUnreadable("gh_unavailable"), CiUnreadable("gh_unreadable")])
def test_an_unreadable_read_is_unknown(read: CiUnreadable) -> None:
    assert ci_state(read, NAMES) == "unknown"


def test_checks_beside_checks_fast_agrees_with_the_merge_gate() -> None:
    names = frozenset({"checks", "checks (fast)"})
    for checks in (
        report(job("checks"), job("checks (fast)", "FAILURE", run_id=2)),
        report(job("checks (fast)")),
        report(job("checks"), job("checks (fast)", run_id=2)),
    ):
        verdict = ci.ci_state(checks, tuple(sorted(names)))
        assert ci_state(checks, names) == verdict.state
