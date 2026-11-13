"""`ci.ci_state` characterization and `ci.name_states` (GH-694): the verdict of every report the
merge gate reads stays as it was when the per-name pass is extracted, and the aggregate of the
per-name states is that verdict's state. One row per input and the answer it must give."""

from collections.abc import Callable

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, CiReport, CiVerdict

HEAD = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"


def job(
    name: str,
    conclusion: str | None = "SUCCESS",
    status: str = "COMPLETED",
    run_id: int = 1,
    started_at: str | None = None,
) -> CiCheck:
    link = f"https://github.com/o/r/actions/runs/{run_id}/job/{name.replace(' ', '-')}"
    return CiCheck(name, status, conclusion, link, started_at=started_at)


def report(
    *checks: CiCheck,
    completed: bool | None = True,
    cancelled: frozenset[str] = frozenset(),
) -> CiReport:
    return CiReport(HEAD, None, None, None, tuple(checks), completed, cancelled_runs=cancelled)


RED_ONE = job("checks", "FAILURE")
CANCELLED_ONE = job("checks", "CANCELLED", run_id=7)
FAST_RED = job("checks (fast)", "FAILURE", run_id=2)
STATUS_RED = CiCheck("ci/build", "COMPLETED", "FAILURE", "https://ci.example/b/1")
SHARDS = ("pytest (a, 1)", "pytest (b, 2)")

# (id, declared, report builder, expected verdict)
CASES: list[tuple[str, tuple[str, ...], Callable[[], CiReport], CiVerdict]] = [
    ("green", ("checks",), lambda: report(job("checks")), CiVerdict("green", (), ())),
    (
        "red",
        ("checks",),
        lambda: report(RED_ONE),
        CiVerdict("red", (RED_ONE,), (), (), (("checks", (RED_ONE,)),)),
    ),
    (
        "pending",
        ("checks",),
        lambda: report(job("checks", None, "IN_PROGRESS")),
        CiVerdict("pending", (), ()),
    ),
    (
        "missing",
        ("checks", "lint"),
        lambda: report(job("checks")),
        CiVerdict("pending", (), ("lint",)),
    ),
    (
        "cancelled only",
        ("checks",),
        lambda: report(CANCELLED_ONE, cancelled=frozenset({"7"})),
        CiVerdict("pending", (), (), (CANCELLED_ONE,)),
    ),
    (
        "cancelled plus green on one name",
        ("checks",),
        lambda: report(CANCELLED_ONE, job("checks", run_id=8), cancelled=frozenset({"7"})),
        CiVerdict("green", (), ()),
    ),
    (
        "expansion only, runs_completed None",
        ("pytest",),
        lambda: report(*map(job, SHARDS), completed=None),
        CiVerdict("pending", (), ()),
    ),
    (
        "expansion only, runs_completed False",
        ("pytest",),
        lambda: report(*map(job, SHARDS), completed=False),
        CiVerdict("pending", (), ()),
    ),
    (
        "expansion only, runs_completed True",
        ("pytest",),
        lambda: report(*map(job, SHARDS), completed=True),
        CiVerdict("green", (), ()),
    ),
    (
        "superseded red run",
        ("checks",),
        lambda: report(job("checks", "FAILURE", run_id=3), job("checks", run_id=4)),
        CiVerdict("green", (), ()),
    ),
    (
        "truncated matrix name",
        ("pytest",),
        lambda: report(job("pytest (areas-1, tests/x...")),
        CiVerdict("green", (), ()),
    ),
    (
        "status context",
        ("ci/build",),
        lambda: report(STATUS_RED),
        CiVerdict("red", (STATUS_RED,), (), (), (("ci/build", (STATUS_RED,)),)),
    ),
    (
        "checks and checks (fast) both declared",
        ("checks", "checks (fast)"),
        lambda: report(job("checks"), FAST_RED),
        CiVerdict("red", (FAST_RED,), (), (), (("checks (fast)", (FAST_RED,)),)),
    ),
]

STATES: dict[str, dict[str, str]] = {
    "green": {"checks": "green"},
    "red": {"checks": "red"},
    "pending": {"checks": "pending"},
    "missing": {"checks": "green", "lint": "pending"},
    "cancelled only": {"checks": "cancelled"},
    "cancelled plus green on one name": {"checks": "green"},
    "expansion only, runs_completed None": {"pytest": "pending"},
    "expansion only, runs_completed False": {"pytest": "pending"},
    "expansion only, runs_completed True": {"pytest": "green"},
    "superseded red run": {"checks": "green"},
    "truncated matrix name": {"pytest": "green"},
    "status context": {"ci/build": "red"},
    "checks and checks (fast) both declared": {"checks": "green", "checks (fast)": "red"},
}


@pytest.mark.parametrize(
    ("declared", "build", "expected"),
    [pytest.param(d, b, v, id=i) for i, d, b, v in CASES],
)
def test_ci_state_verdict_is_unchanged(
    declared: tuple[str, ...], build: Callable[[], CiReport], expected: CiVerdict
) -> None:
    verdict = ci.ci_state(build(), declared)
    assert verdict == expected
    assert verdict.red_names == expected.red_names


def test_cases_have_their_states_listed() -> None:
    assert {case[0] for case in CASES} == set(STATES)


@pytest.mark.parametrize(
    ("declared", "build", "expected"),
    [pytest.param(d, b, v, id=i) for i, d, b, v in CASES],
)
def test_the_aggregate_of_name_states_is_the_verdict_state(
    declared: tuple[str, ...], build: Callable[[], CiReport], expected: CiVerdict
) -> None:
    states = set(ci.name_states(build(), declared).values())
    aggregate = "red" if "red" in states else "pending" if states - {"green"} else "green"
    assert aggregate == expected.state


@pytest.mark.parametrize(("case", "states"), list(STATES.items()))
def test_name_states_per_name(case: str, states: dict[str, str]) -> None:
    _, declared, build, _ = next(row for row in CASES if row[0] == case)
    assert ci.name_states(build(), declared) == states


def test_name_states_resolves_the_declared_names_in_one_joint_pass() -> None:
    both = report(job("checks (fast)", "FAILURE", run_id=2))
    assert ci.name_states(both, ("checks", "checks (fast)")) == {
        "checks": "pending",
        "checks (fast)": "red",
    }
    assert ci.name_states(both, ("checks",)) == {"checks": "red"}


def test_a_cancelled_name_beside_a_pending_one_stays_distinguishable() -> None:
    mixed = report(CANCELLED_ONE, cancelled=frozenset({"7"}))
    assert ci.name_states(mixed, ("checks", "lint")) == {"checks": "cancelled", "lint": "pending"}
