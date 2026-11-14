"""`ci.log_candidates` and the failing-header reader of a CI log (GH-740). Pure."""

from pathlib import Path

import pytest

from agentic.core.process.domain import ci
from agentic.core.process.domain.ci import CiCheck, CiReport, JobLog, JobLogCause

FIXTURE = Path(__file__).parent / "fixtures" / "playwright_list_failure.txt"
PW = "[chromium] › e2e/a.spec.ts:10:5 › suite › case"
PW_ID = "[chromium] › e2e/a.spec.ts › suite › case"


def run(name: str, run_id: int, conclusion: str = "FAILURE") -> CiCheck:
    link = f"https://github.com/o/r/actions/runs/{run_id}/job/1"
    return CiCheck(name, "COMPLETED", conclusion, link, started_at=f"2026-11-10T10:0{run_id}:00Z")


def report(*checks: CiCheck) -> CiReport:
    return CiReport("sha", None, None, None, checks)


def read(text: str, *, cause: JobLogCause = "read", truncated: int = 0) -> JobLog:
    return JobLog("check", "https://x/runs/1/job/2", text, len(text.encode()), cause, truncated)


def names(checks: tuple[CiCheck, ...]) -> list[str]:
    return [check.name for check in checks]


def test_shards_come_before_the_declared_aggregate() -> None:
    shards = [run(f"shard {n}", 1) for n in range(5)]
    red = report(run("check", 1), *shards)
    assert names(ci.log_candidates(red, ("check",))) == [f"shard {n}" for n in range(5)] + ["check"]


def test_only_the_aggregate_red_gives_the_aggregate() -> None:
    red = report(run("check", 1), run("shard 1", 1, "SUCCESS"))
    assert names(ci.log_candidates(red, ("check",))) == ["check"]


def test_a_superseded_red_shard_is_not_a_candidate() -> None:
    red = report(run("shard 1", 1), run("shard 1", 2, "SUCCESS"), run("check", 2))
    assert names(ci.log_candidates(red, ("check",))) == ["check"]


def summary(*failed: str) -> list[str]:
    return ["=== short test summary info ===", *(f"FAILED {i}" for i in failed), "=== 2 failed ==="]


SESSION = "=== test session starts ==="


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([SESSION, *summary("t.py::a"), SESSION, *summary("t.py::b")], ["t.py::b"]),
        ([SESSION, *summary("t.py::a"), SESSION, "collected 3 items"], []),
        ([*summary("t.py::a")], []),
        ([SESSION, *summary("t.py::a - boom", "t.py::a", "t.py::b")], ["t.py::a", "t.py::b"]),
        ([SESSION, *summary("t.py::a"), "FAILED t.py::late"], ["t.py::a"]),
    ],
)
def test_pytest_failures_come_from_the_last_sessions_summary(
    lines: list[str], expected: list[str]
) -> None:
    assert [h.id for h in ci.failing_headers("\n".join(lines))] == expected


@pytest.mark.parametrize(
    "line",
    [
        f"  1) {PW} ─────",
        f"\x1b[31m  12) {PW} (retry #2) ─\x1b[39m",
        f"job\tstep\t2026-11-10T10:00:00.0000000Z 3) {PW}",
    ],
)
def test_a_playwright_header_gives_one_id_whatever_its_decoration(line: str) -> None:
    assert ci.failing_headers("x\n" + line + "\n" + line) == (ci.FailingHeader(1, PW_ID),)


def test_pytest_headers_know_their_line_through_gh_prefix() -> None:
    lines = [f"j\ts\t2026-11-10T10:00:00.0000000Z {x}" for x in [SESSION, *summary("t.py::a")]]
    assert ci.failing_headers("\n".join(lines)) == (ci.FailingHeader(2, "t.py::a"),)


def test_the_real_playwright_header_keeps_its_id_with_retry_and_any_rule() -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    header = lines[15]
    expected = (
        "[secure-origin] › e2e/ventasTelefono.spec.ts › phone sales sweep"
        " › the measurement rejects what it must › a control under 44 px"
    )
    assert [h.id for h in ci.failing_headers(header)] == [expected]
    base = header.rstrip(" ─")
    for variant in (f"{base} (retry #1) ──", f"{base} {'─' * 70}", base):
        assert [h.id for h in ci.failing_headers(variant)] == [expected]
    assert ci.failing_headers(lines[19] + f"\n  ✓  1 {PW} (3ms)\n  12 passed (1m)") == ()
    assert ci.failed_tests("\n".join(lines)) == {expected}


@pytest.mark.parametrize("bad", ["x" * 201, "a`b", "a{{b", "a\x07b"])
def test_an_id_unfit_to_travel_is_dropped_and_counted_never_cut(bad: str) -> None:
    scan = ci.scan_headers("\n".join([SESSION, *summary("t.py::a", bad)]))
    assert [h.id for h in scan.headers] == ["t.py::a"]
    assert scan.dropped == 1


@pytest.mark.parametrize(
    "log",
    [
        read("", cause="read"),
        read("x", cause="unreadable"),
        read("\n".join([SESSION, *summary("t.py::a")]), truncated=5),
        read("nothing recognisable here"),
        read("\n".join([SESSION, *summary(*(f"t.py::{n}" for n in range(7)), "x" * 201)])),
    ],
)
def test_the_failing_set_is_unknown_when_the_log_cannot_say(log: JobLog) -> None:
    assert ci.failing_set(log) is None


def test_the_failing_set_of_a_readable_log_is_its_ids() -> None:
    assert ci.failing_set(read("\n".join([SESSION, *summary("t.py::a")]))) == {"t.py::a"}
