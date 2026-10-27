"""GH-212: the evidence a failed merge-checks run leaves behind -- `merge_checks_record`
(the one builder of the record), `failed_checks_summary` and `failed_checks_markdown` (the
only two places the failing names and tails are turned into text), and the document round
trip of a hostile tail. Pure: no port, no filesystem."""

from __future__ import annotations

from typing import get_args

import pytest

from agentic.core.process.domain import document
from agentic.core.process.domain.presubmit import (
    MERGE_COMMENT_MAX,
    MERGE_TAIL_CHARS,
    TAIL_LINES,
    CheckResult,
    FailedStep,
    WitnessResult,
    failed_checks_markdown,
    failed_checks_summary,
    merge_checks_record,
)

HEAD = "a" * 40
MASTER = "b" * 40
ZWSP = "​"


def _result(name: str, passed: bool = True, output: str = "") -> CheckResult:
    return {"name": name, "command": f"run {name}", "passed": passed, "output": output}


def _witness(passed: bool, output: str = "/elsewhere") -> WitnessResult:
    return {"command": "pwd", "output": output, "passed": passed}


def _record(declared=None, results=None, witness=None, **more):
    declared = {"lint": "l", "tests": "t"} if declared is None else declared
    return merge_checks_record(
        head=HEAD,
        master=MASTER,
        duration_s=1.5,
        declared=declared,
        results=results,
        witness=witness,
        **more,
    )


# --- `passed` against today's expression, and the matching `failed_step` -------------------

BOTH_GREEN = [_result("lint"), _result("tests")]
ONE_RED = [_result("lint"), _result("tests", False, "boom")]
TWO = {"lint": "l", "tests": "t"}


@pytest.mark.parametrize(
    ("declared", "results", "witness", "step", "setup", "passed", "failed_step"),
    [
        (TWO, BOTH_GREEN, None, None, None, True, None),
        (TWO, BOTH_GREEN, _witness(True), None, None, True, None),
        (TWO, ONE_RED, None, None, None, False, "checks"),
        (TWO, None, None, None, None, False, "checks"),
        (TWO, BOTH_GREEN, _witness(False), None, None, False, "witness"),
        (TWO, ONE_RED, _witness(False), None, None, False, "checks"),
        ({}, [], None, None, None, False, "no_checks"),
        ({}, None, None, None, None, False, "no_checks"),
        ({}, None, None, "conflict", None, False, "conflict"),
        ({}, None, None, "worktree", None, False, "worktree"),
        ({}, None, None, "setup", ("install", "nope"), False, "setup"),
    ],
    ids=[
        "all green",
        "witness agrees",
        "one red",
        "results none",
        "witness disagrees",
        "red and witness disagrees",
        "no checks declared",
        "no checks, no results",
        "merge conflict",
        "worktree could not be built",
        "setup red",
    ],
)
def test_passed_is_todays_expression_and_failed_step_names_where_it_stopped(
    declared, results, witness, step, setup, passed, failed_step
):
    record = _record(declared, results, witness, failed_step=step, setup_failure=setup)

    assert record["passed"] is passed
    assert record.get("failed_step") == failed_step
    assert ("failed_step" in record) is (not passed)


def test_the_four_old_keys_keep_their_names_and_the_new_one_comes_beside_them():
    record = _record(results=BOTH_GREEN)

    assert record == {
        "head": HEAD,
        "master": MASTER,
        "passed": True,
        "duration_s": 1.5,
        "checks": [
            {"name": "lint", "passed": True, "duration_s": 0.0},
            {"name": "tests", "passed": True, "duration_s": 0.0},
        ],
    }


def test_the_failed_steps_are_the_closed_set():
    assert set(get_args(FailedStep)) == {
        "worktree",
        "conflict",
        "setup",
        "no_checks",
        "witness",
        "checks",
    }


# --- `checks`: declared order, tails only on failures -------------------------------------


def test_results_that_arrive_in_reverse_come_out_in_declared_order():
    record = _record(results=[_result("tests", False, "t out"), _result("lint", False, "l out")])

    assert [c["name"] for c in record["checks"]] == ["lint", "tests"]
    assert failed_checks_summary(record, HEAD) == "Failed: lint, tests"


def test_a_failing_check_carries_its_tail_and_a_passing_one_does_not():
    record = _record(results=[_result("lint", True, "fine"), _result("tests", False, "boom")])

    assert record["checks"] == [
        {"name": "lint", "passed": True, "duration_s": 0.0},
        {"name": "tests", "passed": False, "tail": ["boom"], "duration_s": 0.0},
    ]


def test_a_declared_check_with_no_result_is_failed_without_a_tail():
    record = _record(results=None)

    assert record["checks"] == [
        {"name": "lint", "passed": False},
        {"name": "tests", "passed": False},
    ]
    assert record["failed_step"] == "checks"


def test_a_disagreeing_witness_is_a_trailing_entry_and_an_agreeing_one_is_not_recorded():
    agreed = _record(results=BOTH_GREEN, witness=_witness(True))
    disagreed = _record(results=BOTH_GREEN, witness=_witness(False, "/elsewhere"))

    assert [c["name"] for c in agreed["checks"]] == ["lint", "tests"]
    assert disagreed["checks"][-1] == {"name": "witness", "passed": False, "tail": ["/elsewhere"]}
    assert disagreed["failed_step"] == "witness"
    assert failed_checks_summary(disagreed, HEAD) == "Failed: checkout witness disagreed"


def test_a_setup_failure_is_an_entry_named_by_its_setup_key():
    record = _record({}, None, None, failed_step="setup", setup_failure=("install", "no network"))

    assert record["checks"] == [{"name": "install", "passed": False, "tail": ["no network"]}]
    assert failed_checks_summary(record, HEAD) == "Failed at setup: install"


@pytest.mark.parametrize(
    ("step", "wording"),
    [
        ("conflict", "Failed: the head does not merge with master"),
        ("worktree", "Failed: the worktree could not be built"),
        ("no_checks", "Failed: no checks are declared"),
    ],
)
def test_the_steps_with_no_entry_have_their_own_fixed_wording(step, wording):
    record = _record({}, None, None, failed_step=step)

    assert record["checks"] == []
    assert failed_checks_summary(record, HEAD) == wording


# --- the tail pipeline and its caps -------------------------------------------------------


def _tail(output: str) -> str:
    lines = _record(results=[_result("lint"), _result("tests", False, output)])["checks"][1]["tail"]
    assert isinstance(lines, list)  # a list of lines: a multi-line scalar breaks record_closure
    return "\n".join(lines)


def test_ansi_and_control_characters_go_away_and_tabs_become_spaces():
    dirty = "\x1b[31mred\x1b[0m\x1b]0;title\x07 \x00\x08ok\r\n\tindented\x7f end\x1b"

    assert _tail(dirty) == "red ok\n    indented end"


def test_the_tail_is_the_last_thirty_lines():
    output = "\n".join(f"line {i}" for i in range(TAIL_LINES + 20))

    lines = _tail(output).splitlines()

    assert len(lines) == TAIL_LINES
    assert lines[0] == "line 20" and lines[-1] == f"line {TAIL_LINES + 19}"


def test_the_tail_keeps_the_last_two_thousand_characters_of_a_long_line():
    assert _tail("x" * 100 + "y" * MERGE_TAIL_CHARS) == "y" * MERGE_TAIL_CHARS


def test_the_caps_run_after_sanitizing_so_escapes_never_eat_the_budget():
    assert _tail("\x1b[31m" * 1000 + "z" * MERGE_TAIL_CHARS) == "z" * MERGE_TAIL_CHARS


# --- the summary: what it needs to be shown, and what it never reads ---------------------


def test_a_record_for_another_head_or_without_evidence_has_no_summary():
    record = _record(results=ONE_RED)
    legacy = {"head": HEAD, "master": MASTER, "passed": False, "duration_s": 3.0}

    assert failed_checks_summary(record, "c" * 40) == ""
    assert failed_checks_summary(record, None) == ""
    assert failed_checks_summary(legacy, HEAD) == ""
    assert failed_checks_markdown(legacy, HEAD) == ""
    assert failed_checks_markdown(record, "c" * 40) == ""
    assert failed_checks_summary(_record(results=BOTH_GREEN), HEAD) == ""


@pytest.mark.parametrize(
    "checks", ["nope", [None, 3, {"passed": False}], [{"name": 5, "passed": False}]]
)
def test_a_malformed_record_reads_as_no_summary_not_a_crash(checks):
    record = {"head": HEAD, "passed": False, "failed_step": "checks", "checks": checks}

    assert failed_checks_summary(record, HEAD) == ""
    assert failed_checks_markdown(record, HEAD) == ""


def test_the_summary_never_reads_the_tails():
    one = _record(results=[_result("lint"), _result("tests", False, "one")])
    other = _record(results=[_result("lint"), _result("tests", False, "another thing")])

    assert failed_checks_summary(one, HEAD) == failed_checks_summary(other, HEAD)


# --- the markdown: fences, neutralisation, the cap ----------------------------------------


def test_the_markdown_is_the_summary_then_each_failing_name_and_tail_in_a_fence():
    record = _record(results=ONE_RED)

    assert failed_checks_markdown(record, HEAD) == (
        "Failed: tests\n\n`tests`, last lines:\n\n```text\nboom\n```"
    )


@pytest.mark.parametrize("run", [3, 5])
def test_a_run_of_backticks_in_a_tail_stays_inside_one_fence(run):
    hostile = "before\n" + "`" * run + "\nafter"
    record = _record(results=[_result("lint"), _result("tests", False, hostile)])

    text = failed_checks_markdown(record, HEAD)

    fence = "`" * (run + 1)
    assert f"\n{fence}text\n{hostile}\n{fence}" in text
    assert text.endswith(fence)


def test_mentions_and_issue_references_are_neutralised_in_the_comment_only():
    tail = "ping @someone about #123 and a/b#4, but not a@ b# @ #"
    record = _record(results=[_result("lint"), _result("tests", False, tail)])

    text = failed_checks_markdown(record, HEAD)

    assert f"@{ZWSP}someone" in text and f"#{ZWSP}123" in text and f"b#{ZWSP}4" in text
    assert "a@ b# @ #" in text  # not followed by a word character: untouched
    assert "\n".join(record["checks"][1]["tail"]) == tail  # the document keeps it as it was
    assert failed_checks_markdown(record, HEAD, neutralise=False).count(ZWSP) == 0


def test_a_comment_over_the_cap_drops_tails_from_the_last_failing_entry_backwards():
    count = 40
    record = {
        "head": HEAD,
        "passed": False,
        "failed_step": "checks",
        "checks": [
            {"name": f"c{i}", "passed": False, "tail": ["y" * MERGE_TAIL_CHARS]}
            for i in range(count)
        ],
    }
    assert count * MERGE_TAIL_CHARS > MERGE_COMMENT_MAX

    text = failed_checks_markdown(record, HEAD)

    assert len(text) <= MERGE_COMMENT_MAX
    assert all(f"`c{i}`" in text for i in range(count))  # the names always stay
    kept = text.count("y" * MERGE_TAIL_CHARS)
    assert 0 < kept < count
    assert f"`c{kept - 1}`, last lines:" in text  # the first entries kept their tails
    assert f"`c{kept}`, last lines:" not in text  # dropped from the end backwards
    assert f"`c{kept}`, last lines omitted" in text


# --- the document round trip --------------------------------------------------------------

HOSTILE = (
    "\x1b[31mred\x1b[0m\ttabbed ñ\n---\n- a list item\n   starts with spaces\n"
    + "q" * 5000
    + "\nlast: line"
)


def test_a_hostile_tail_round_trips_through_record_closure_and_nothing_else_moves():
    state = {
        "id": "GH-1",
        "title": "t",
        "description": "d",
        "status": "ready_for_review",
        "flags": {"x": 1},
    }
    old = {"head": "old", "master": "old", "passed": True, "duration_s": 1.0}
    original = document.record_closure(document.render(state), {"merge_checks": old})
    record = _record(
        results=[_result("lint"), _result("tests", False, HOSTILE)], witness=_witness(True)
    )

    changed = document.record_closure(original, {"merge_checks": record})
    parsed = document.parse(changed)

    assert parsed["merge_checks"] == record
    assert "\n".join(parsed["merge_checks"]["checks"][1]["tail"]) == _tail(HOSTILE)
    before = document.parse(original)
    assert {k: v for k, v in parsed.items() if k != "merge_checks"} == {
        k: v for k, v in before.items() if k != "merge_checks"
    }
    old_lines, new_lines = original.splitlines(), changed.splitlines()
    assert len(new_lines) == len(old_lines)
    differing = [i for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=True)) if a != b]
    assert len(differing) == 1 and new_lines[differing[0]].startswith("merge_checks:")
