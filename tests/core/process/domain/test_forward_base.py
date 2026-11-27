"""The domain half of the forward affected base (GH-1125): the kept-verdict row, the green
source, the one base decision, `ran`, and how the rows read in durations, forms and Markdown."""

from __future__ import annotations

from typing import get_args

import pytest

from agentic.core.process.domain.forward_base import (
    advance_greens,
    base_for,
    green_source,
    kept_row,
)
from agentic.core.process.domain.presubmit import (
    AFFECTED_EMPTY_EXIT,
    AFFECTED_REFUSED_EXIT,
    STACK_FILE,
    ModeCause,
    PlannedForm,
    budget_breaches,
    check_durations,
    checks_markdown,
    decided_durations,
    plan_form,
    ran,
    settle_form,
)

G = "a" * 40
G8 = "a" * 8
HEAD = "b" * 40
TASK_BASE = "c" * 40


def row(name="tests", passed=True, **extra):
    return {
        "name": name,
        "command": f"run {name}",
        "passed": passed,
        "output": "",
        "duration_s": 2.0,
    } | extra


def exit8(name="tests", **extra):
    return row(
        name,
        passed=False,
        mode="affected-only",
        affected_base=G,
        exit_code=AFFECTED_EMPTY_EXIT,
        duration_s=5.0,
        **extra,
    )


def kept(name="tests"):
    built = kept_row(exit8(name), G, ["agentic/x.py"])
    assert built is not None
    return built


# --- green_source ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "passed", "stuck", "expected"),
    [
        pytest.param({}, True, frozenset(), True, id="plain"),
        pytest.param({"reused": True}, True, frozenset(), True, id="reused-content-hit"),
        pytest.param({"rerun": True}, True, frozenset(), False, id="rerun"),
        pytest.param({"timed_out": True}, True, frozenset(), False, id="timed-out"),
        pytest.param({"truncated": True}, True, frozenset(), False, id="truncated"),
        pytest.param({"not_ready": True}, True, frozenset(), False, id="not-ready"),
        pytest.param({"reused_from": G}, True, frozenset(), False, id="kept"),
        pytest.param({}, True, frozenset({"tests"}), False, id="stuck"),
        pytest.param({}, True, frozenset({"lint"}), True, id="another-check-stuck"),
        pytest.param({}, False, frozenset(), False, id="failed"),
    ],
)
def test_green_source_table(extra, passed, stuck, expected):
    assert green_source(row(passed=passed, **extra), stuck) is expected


# --- base_for -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("green", "ancestor", "paths", "expected"),
    [
        pytest.param(G, True, ["a.py"], G, id="ancestor-with-paths"),
        pytest.param(G, False, ["a.py"], TASK_BASE, id="amended-or-rebased-away"),
        pytest.param(None, True, ["a.py"], TASK_BASE, id="no-green"),
        pytest.param(G, None, ["a.py"], TASK_BASE, id="ancestry-unreadable"),
        pytest.param(G, True, None, TASK_BASE, id="paths-unreadable"),
        pytest.param(G, True, [], TASK_BASE, id="green-is-head-empty-diff"),
        pytest.param(None, None, None, TASK_BASE, id="nothing-known"),
    ],
)
def test_base_for_table(green, ancestor, paths, expected):
    assert base_for(green, ancestor, paths, TASK_BASE) == expected


# --- kept_row -------------------------------------------------------------------------------


def test_exit_8_against_green_with_a_new_diff_gives_the_kept_row():
    built = kept_row(exit8(), G, ["agentic/x.py"])
    assert built == {
        "name": "tests",
        "command": "run tests",
        "passed": True,
        "output": "",
        "duration_s": 0.0,
        "mode": "affected-only",
        "reused_from": G,
    }


@pytest.mark.parametrize(
    ("changes", "paths"),
    [
        pytest.param({"exit_code": AFFECTED_REFUSED_EXIT}, ["a.py"], id="exit-9"),
        pytest.param({"exit_code": 1}, ["a.py"], id="another-exit"),
        pytest.param({"timed_out": True}, ["a.py"], id="timed-out"),
        pytest.param({}, None, id="paths-unreadable"),
        pytest.param({}, [], id="paths-empty"),
        pytest.param({"affected_base": TASK_BASE}, ["a.py"], id="ran-against-another-base"),
        pytest.param({"mode": "full"}, ["a.py"], id="full-form"),
    ],
)
def test_kept_row_is_refused_outside_the_exit_8_case(changes, paths):
    assert kept_row(exit8() | changes, G, paths) is None


def test_kept_row_needs_the_row_to_have_run_against_green():
    unmarked = exit8()
    del unmarked["affected_base"]
    assert kept_row(unmarked, G, ["a.py"]) is None


# --- advance_greens -------------------------------------------------------------------------


def test_advance_greens_moves_a_green_check_to_head_and_keeps_the_rest():
    rows = [
        row("tests"),
        row("lint", passed=False),
        row("types", passed=False, timed_out=True),
        row("docs", reused_from=G),
    ]
    previous = {"lint": "1" * 40, "types": "2" * 40, "docs": "3" * 40, "format": "4" * 40}
    assert advance_greens(previous, rows, HEAD, frozenset()) == {
        "tests": HEAD,
        "lint": "1" * 40,
        "types": "2" * 40,
        "docs": "3" * 40,
        "format": "4" * 40,
    }


def test_advance_greens_never_moves_a_stuck_check_even_with_a_green_final_row():
    rows = [row("tests"), row("lint")]
    assert advance_greens({"tests": G}, rows, HEAD, frozenset({"tests"})) == {
        "tests": G,
        "lint": HEAD,
    }


def test_advance_greens_judges_the_final_row_of_a_check():
    assert advance_greens({"tests": G}, [exit8(), kept()], HEAD, frozenset()) == {"tests": G}
    full_green = [exit8(), row(mode="full")]
    assert advance_greens({"tests": G}, full_green, HEAD, frozenset()) == {"tests": HEAD}


def test_advance_greens_with_no_previous_and_no_green_has_no_entry():
    assert advance_greens({}, [row(passed=False)], HEAD, frozenset()) == {}


# --- ran ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("built", "expected"),
    [
        pytest.param(row(), True, id="plain"),
        pytest.param(row(reused=True), False, id="reused"),
        pytest.param(row(rerun=True), False, id="rerun"),
        pytest.param(row(affected_base=G, mode="affected-only"), True, id="against-green"),
    ],
)
def test_ran_table(built, expected):
    assert ran(built) is expected


def test_a_kept_row_passed_but_did_not_run():
    built = kept()
    assert built["passed"] is True
    assert ran(built) is False


# --- durations ------------------------------------------------------------------------------


def test_duration_table_over_flake_reused_and_kept_rows():
    rows = [
        row("tests", passed=False, duration_s=3.0),
        row("tests", rerun=True, duration_s=4.0),
        row("lint", reused=True, duration_s=7.0),
        exit8("types"),
        kept("types"),
    ]
    # the exit-8 row's seconds still count; the rerun, the reused row and the kept row add none
    assert check_durations(rows) == {"tests": 3.0, "types": 5.0}
    # a flake is its green rerun, a reused pass its stored seconds, a kept check no entry
    assert decided_durations(rows) == {"tests": 4.0, "lint": 7.0}


def test_a_lone_kept_row_has_no_entry_in_either_duration_reader():
    assert check_durations([kept()]) == {}
    assert decided_durations([kept()]) == {}


def test_budget_breaches_never_count_a_kept_check_as_zero_and_still_judges_a_reused_pass():
    rows = [row("tests", reused=True, duration_s=30.0), kept("lint")]
    breaches = budget_breaches(rows, {"tests": 10.0, "lint": 0.0})
    assert [b["name"] for b in breaches] == ["tests"]
    assert breaches[0]["duration_s"] == 30.0


# --- settle_form and the ModeCause set ------------------------------------------------------

AFFECTED = PlannedForm("affected-only", "none", None, None)


def test_settle_form_gives_kept_green_for_the_exit_8_row_then_the_kept_row():
    form = settle_form(AFFECTED, [exit8(), kept()], "gate")
    assert (form["mode"], form["cause"]) == ("affected-only", "kept_green")
    assert form["check"] == "tests"


def test_settle_form_gives_kept_green_for_a_lone_kept_row():
    form = settle_form(AFFECTED, [kept()], "gate")
    assert (form["mode"], form["cause"]) == ("affected-only", "kept_green")


def test_settle_form_without_a_kept_row_reads_the_exit_8_row_as_before():
    form = settle_form(AFFECTED, [exit8(), row(mode="full")], "gate")
    assert (form["mode"], form["cause"]) == ("full", "script_empty")


def test_every_mode_cause_is_produced_by_plan_form_or_settle_form():
    commands = {"tests": "t {base}"}
    plans = [
        plan_form("tests", ["a.py"], [], commands, frozenset()),
        plan_form("tests", ["a.py"], ["a.*"], commands, frozenset()),
        plan_form("tests", [STACK_FILE], [], commands, frozenset()),
        plan_form("tests", None, [], commands, frozenset()),
        plan_form("lint", ["a.py"], [], commands, frozenset()),
        plan_form("tests", ["a.py"], [], commands, frozenset({"tests"})),
    ]
    produced = {p.cause for p in plans}
    for exit_code in (AFFECTED_EMPTY_EXIT, AFFECTED_REFUSED_EXIT, 1):
        failed = row(passed=False, mode="affected-only", exit_code=exit_code)
        produced.add(settle_form(AFFECTED, [failed, row(mode="full")], "gate")["cause"])
    produced.add(settle_form(AFFECTED, [exit8(), kept()], "gate")["cause"])
    assert produced == set(get_args(ModeCause))


# --- checks_markdown ------------------------------------------------------------------------


def test_markdown_shows_the_exit_8_row_with_its_seconds_and_the_kept_row_with_a_dash():
    text = checks_markdown("h" * 8, [exit8(), kept()], base=TASK_BASE)
    lines = [ln for ln in text.splitlines() if ln.startswith("| `tests`")]
    assert lines == [
        f"| `tests` | `run tests` | affected-only | **failed** against {G8} | 5.0 |",
        f"| `tests` | `run tests` | affected-only | passed (kept from {G8}) | - |",
    ]


def test_markdown_closing_sentence_names_only_the_unmarked_rows_when_some_are_marked():
    marked = checks_markdown("h", [exit8(), kept()], base=TASK_BASE)
    plain = checks_markdown("h", [row(mode="affected-only")], base=TASK_BASE)
    assert "without `against` or `kept from`" in marked
    assert "the whole branch" in marked
    assert "without" not in plain
    assert (
        f"`affected-only` rows ran the tests selected against `{TASK_BASE}` (the whole "
        "branch, `task_base..head`, not the increment under review); the full suite "
        "runs at merge (ADR-0045)."
    ) in plain


def test_markdown_of_rows_without_the_new_fields_is_as_before():
    text = checks_markdown("h", [row(reused=True)])
    assert "| `tests` | `run tests` | passed (reused) | - |" in text
    assert "kept from" not in text
    assert "against" not in text
