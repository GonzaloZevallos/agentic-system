"""The readers of a check that timed out (GH-1075): the verdict, the facts, what the store may
keep and the fixed line. Pure; every row is built here, nothing runs a command."""

from __future__ import annotations

from agentic.core.process.domain.presubmit import (
    CheckResult,
    check_timeout,
    storable,
    timeout_facts,
    timeout_line,
    workers_of,
)


def _row(
    name: str = "tests",
    *,
    passed: bool = False,
    timed_out: bool = False,
    workers: int | None = None,
    output: str = "",
    seconds: float = 1500.0,
) -> CheckResult:
    row: CheckResult = {
        "name": name,
        "command": f"run {name}",
        "passed": passed,
        "output": output,
        "duration_s": seconds,
    }
    if timed_out:
        row["timed_out"] = True
    if workers is not None:
        row["workers"] = workers
    return row


def test_a_timeout_alone_is_a_check_timeout():
    assert check_timeout([_row("lint", passed=True), _row("tests", timed_out=True)])


def test_a_timeout_beside_a_red_check_is_not_one():
    assert not check_timeout([_row("lint"), _row("tests", timed_out=True)])


def test_a_green_run_is_not_one():
    assert not check_timeout([_row(passed=True)])


def test_the_verdict_never_reads_the_output_text():
    # The output says "timed out", the field does not: a red, not a timeout.
    assert not check_timeout([_row(output="(timed out after 1500 s)")])


def test_workers_of_is_none_when_absent_and_never_one():
    assert workers_of(_row()) is None
    assert workers_of(_row(workers=4)) == 4


def test_timeout_facts_are_sorted_by_check_and_omit_absent_workers():
    rows = [
        _row("tests", timed_out=True, workers=4, seconds=1500.0),
        _row("lint", passed=True),
        _row("docs", timed_out=True, seconds=45.0),
    ]
    assert timeout_facts(rows, "abc") == [
        {"head": "abc", "check": "docs", "seconds": 45.0},
        {"head": "abc", "check": "tests", "seconds": 1500.0, "workers": 4},
    ]


def test_a_check_that_timed_out_and_then_passed_has_no_fact():
    rows = [_row("tests", timed_out=True), _row("tests", passed=True)]
    assert timeout_facts(rows, "abc") == []


def test_only_a_pass_that_did_not_time_out_is_storable():
    assert storable(_row(passed=True))
    assert not storable(_row())
    assert not storable(_row(timed_out=True))
    assert not storable({**_row(passed=True), "timed_out": True})


def test_the_line_is_fixed_for_known_and_unknown_workers():
    assert (
        timeout_line({"head": "h", "check": "tests", "seconds": 1500.0, "workers": 4})
        == "tests timed out after 1500s with 4 workers, unverified"
    )
    assert (
        timeout_line({"head": "h", "check": "lint", "seconds": 45.5})
        == "lint timed out after 45.5s with unknown workers, unverified"
    )
