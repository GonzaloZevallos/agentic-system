"""GH-485: the CI section of a review never says `pending` for a name no check run carries."""

from agentic.core.process.application import ci_review
from agentic.core.process.domain import ci

HEAD = "a" * 40


def _report(completed: bool | None) -> ci.CiReport:
    checks = tuple(
        ci.CiCheck(f"e2e ({n})", "COMPLETED", "SUCCESS", f"https://ci/{n}") for n in (1, 2)
    )
    return ci.CiReport(HEAD, "main", "b" * 40, None, checks, runs_completed=completed)


def test_a_matrix_job_id_is_not_a_check_run_name_once_the_runs_completed():
    line = ci_review._line(_report(True), "e2e")
    assert "not a check-run name" in line and "pending" not in line


def test_an_unknown_run_status_is_still_pending():
    assert ci_review._line(_report(None), "e2e") == "pending"
