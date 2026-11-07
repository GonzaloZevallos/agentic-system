"""GH-485: the CI section of a review never says `pending` for a name no check run carries.
GH-535: a matrix id is carried by its expansions `<id> (<values>)`; a shard job of another
name (`e2e-shard (1)`) carries nothing for `e2e`."""

from agentic.core.process.application import ci_review
from agentic.core.process.domain import ci

HEAD = "a" * 40


def _report(completed: bool | None) -> ci.CiReport:
    checks = tuple(
        ci.CiCheck(f"e2e-shard ({n})", "COMPLETED", "SUCCESS", f"https://ci/{n}") for n in (1, 2)
    )
    return ci.CiReport(HEAD, "main", "b" * 40, None, checks, runs_completed=completed)


def test_a_matrix_job_id_is_not_a_check_run_name_once_the_runs_completed():
    line = ci_review._line(_report(True), "e2e")
    assert "not a check-run name" in line and "pending" not in line


def test_an_unknown_run_status_is_still_pending():
    assert ci_review._line(_report(None), "e2e") == "pending"


def test_a_declared_job_left_only_by_a_cancelled_run_reads_pending_never_a_failure():
    # GH-539: the suite of run 7 concluded CANCELLED, so the red `web` says nothing of the code.
    link = "https://github.com/o/r/actions/runs/7/job/1"
    check = ci.CiCheck("web", "COMPLETED", "CANCELLED", link)
    report = ci.CiReport(HEAD, "main", "b" * 40, None, (check,), True, frozenset({"7"}))
    line = ci_review._line(report, "web")
    assert line.startswith("pending") and "CANCELLED" not in line


def _matrix(*conclusions: str) -> ci.CiReport:
    checks = tuple(
        ci.CiCheck(f"pytest (areas-{n})", "COMPLETED", conclusion, f"https://ci/{n}")
        for n, conclusion in enumerate(conclusions, 1)
    )
    return ci.CiReport(HEAD, "main", "b" * 40, None, checks, runs_completed=True)


def test_a_matrix_id_reads_green_when_every_expansion_is_green():
    line = ci_review._line(_matrix("SUCCESS", "SUCCESS"), "pytest")
    assert line == "green https://ci/1"


def test_a_matrix_id_reads_red_with_the_link_of_the_red_expansion():
    line = ci_review._line(_matrix("SUCCESS", "FAILURE"), "pytest")
    assert line == "red https://ci/2"
