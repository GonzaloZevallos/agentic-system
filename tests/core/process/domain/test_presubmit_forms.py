"""Which form each delivery-gate check ran and why: `plan_form` (the plan), `settle_form` (what
the rows say happened) and `cap_forms` (the bound), all pure; `check_modes` stays a view of
`plan_form` and is pinned against a copy of the expression it replaced."""

from __future__ import annotations

import itertools
from fnmatch import fnmatchcase

import pytest

from agentic.core.process.domain import presubmit
from agentic.core.process.domain.presubmit import (
    STACK_FILE,
    PlannedForm,
    cap_forms,
    plan_form,
    settle_form,
)

COMMANDS = {"tests": "pytest --since {base}"}
FALLBACK = ("pyproject.toml", "*.lock")
NO_STUCK: frozenset[str] = frozenset()


def row(mode=None, passed=True, **extra):
    base = {"name": "tests", "command": "pytest", "passed": passed, "output": ""}
    if mode is not None:
        base["mode"] = mode
    return {**base, **extra}


def planned(paths, check="tests", fallback=FALLBACK, commands=None, stuck=NO_STUCK):
    return plan_form(check, paths, fallback, COMMANDS if commands is None else commands, stuck)


# --- plan_form -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("check", "paths", "stuck", "expected"),
    [
        (
            "tests",
            ["src/a.py", "deep/x.lock", "pyproject.toml"],
            NO_STUCK,
            PlannedForm("full", "fallback_glob", "pyproject.toml", "pyproject.toml"),
        ),
        (
            "tests",
            ["src/a.py", "deep/x.lock"],
            NO_STUCK,
            PlannedForm("full", "fallback_glob", "*.lock", "deep/x.lock"),
        ),
        (
            "tests",
            ["src/a.py", STACK_FILE],
            NO_STUCK,
            PlannedForm("full", "stack_file", STACK_FILE, STACK_FILE),
        ),
        ("tests", None, NO_STUCK, PlannedForm("full", "paths_unknown", None, None)),
        ("tests", (), NO_STUCK, PlannedForm("full", "paths_unknown", None, None)),
        ("types", ["src/a.py"], NO_STUCK, PlannedForm("full", "no_affected_form", None, None)),
        ("tests", ["src/a.py"], frozenset({"tests"}), PlannedForm("full", "stuck_full", None, None)),
        ("tests", ["src/a.py"], NO_STUCK, PlannedForm("affected-only", "none", None, None)),
    ],
    ids=[
        "glob-in-declared-order",
        "glob-first-path-that-matches",
        "stack-file",
        "paths-none",
        "paths-empty",
        "no-affected-form",
        "stuck",
        "affected",
    ],
)
def test_plan_form_gives_the_first_rule_that_applies(check, paths, stuck, expected):
    assert planned(paths, check=check, stuck=stuck) == expected


def test_a_glob_outranks_the_stack_file_and_a_stuck_check():
    both = planned([STACK_FILE, "a.lock"], stuck=frozenset({"tests"}))
    assert (both.cause, both.glob, both.path) == ("fallback_glob", "*.lock", "a.lock")


def test_the_path_of_a_form_is_cut_to_two_hundred_characters():
    long = "d/" * 150 + "x.lock"
    form = settle_form(planned([long]), [row("full")], "gate")
    assert form["path"] == long[:200]


# --- check_modes stays a view ----------------------------------------------------------


def _old_check_modes(names, plan, changed_paths):
    everything_full = not changed_paths or any(
        fnmatchcase(path, glob) for path in changed_paths for glob in (*plan.fallback, STACK_FILE)
    )
    return {
        name: "affected-only" if name in plan.commands and not everything_full else "full"
        for name in names
    }


@pytest.mark.parametrize(
    ("paths", "fallback", "commands"),
    list(
        itertools.product(
            [
                None,
                (),
                ("src/a.py",),
                ("src/a.py", "pyproject.toml"),
                (STACK_FILE,),
                ("x/y.lock", "src/b.py"),
                ("src/Pyproject.toml",),
            ],
            [(), ("pyproject.toml",), ("*.lock", "tests/conftest.py")],
            [{}, {"tests": "t {base}"}, {"tests": "t", "lint": "l"}],
        )
    ),
)
def test_check_modes_equals_the_expression_it_replaced(paths, fallback, commands):
    plan = presubmit.AffectedPlan(base="b", commands=commands, fallback=fallback)
    names = ["tests", "lint", "types"]
    assert presubmit.check_modes(names, plan, paths) == _old_check_modes(names, plan, paths)


# --- settle_form -----------------------------------------------------------------------


def test_with_no_affected_row_the_planned_form_stands_with_its_glob_and_path():
    assert settle_form(planned(["a.lock"]), [row("full")], "gate") == {
        "check": "tests",
        "mode": "full",
        "cause": "fallback_glob",
        "via": "gate",
        "glob": "*.lock",
        "path": "a.lock",
    }


@pytest.mark.parametrize(
    ("plan_paths", "cause"),
    [(None, "paths_unknown"), ((), "paths_unknown"), ([STACK_FILE], "stack_file")],
)
def test_with_no_affected_row_a_full_plan_keeps_its_cause(plan_paths, cause):
    form = settle_form(planned(plan_paths), [row("full")], "gate")
    assert (form["mode"], form["cause"]) == ("full", cause)


def test_a_check_with_no_affected_form_and_a_stuck_check_keep_their_cause():
    no_form = settle_form(planned(["a.py"], check="types"), [row()], "gate")
    assert no_form["cause"] == "no_affected_form"
    stuck = settle_form(planned(["a.py"], stuck=frozenset({"tests"})), [row("full")], "gate")
    assert stuck["cause"] == "stuck_full" and "glob" not in stuck and "path" not in stuck


def test_an_affected_pass_is_affected_only_and_carries_no_glob_or_path():
    form = settle_form(planned(["a.py"]), [row("affected-only", exit_code=0)], "gate")
    assert form == {"check": "tests", "mode": "affected-only", "cause": "none", "via": "gate"}


def test_a_reused_affected_pass_is_an_affected_pass():
    form = settle_form(planned(["a.py"]), [row("affected-only", reused=True)], "gate")
    assert (form["mode"], form["cause"]) == ("affected-only", "none")


@pytest.mark.parametrize(
    ("affected_extra", "cause"),
    [
        ({"exit_code": 8}, "script_empty"),
        ({"exit_code": 9}, "script_refused"),
        ({"exit_code": 1}, "affected_red"),
        ({}, "affected_red"),
        ({"timed_out": True}, "affected_red"),
        ({"timed_out": True, "exit_code": 8}, "affected_red"),
        ({"timed_out": True, "exit_code": 9}, "affected_red"),
    ],
    ids=["exit-8", "exit-9", "exit-1", "no-exit", "timed-out", "timed-out-8", "timed-out-9"],
)
def test_an_affected_row_that_did_not_pass_then_a_full_row_is_full_with_the_exit_cause(
    affected_extra, cause
):
    rows = [row("affected-only", False, **affected_extra), row("full")]
    form = settle_form(planned(["a.py"]), rows, "gate")
    assert (form["mode"], form["cause"]) == ("full", cause)
    assert "path" not in form and "glob" not in form


def test_a_rerun_row_inherits_the_cause_of_the_row_it_reran():
    rows = [
        row("affected-only", False, exit_code=8),
        row("full", False),
        row("full", True, rerun=True),
    ]
    form = settle_form(planned(["a.py"]), rows, "gate")
    assert (form["mode"], form["cause"]) == ("full", "script_empty")


def test_a_rerun_of_a_planned_full_check_does_not_change_its_cause():
    rows = [row("full", False), row("full", True, rerun=True)]
    form = settle_form(planned(["pyproject.toml"]), rows, "gate")
    assert (form["mode"], form["cause"]) == ("full", "fallback_glob")


def test_a_rerun_row_never_stands_for_the_affected_row():
    rows = [row("affected-only", False, exit_code=9), row("affected-only", True, rerun=True)]
    assert settle_form(planned(["a.py"]), rows, "gate")["cause"] == "script_refused"


def test_a_row_without_a_mode_is_a_full_run_of_the_plan():
    assert settle_form(planned(["a.lock"]), [row()], "gate")["cause"] == "fallback_glob"


def test_via_is_what_the_caller_says():
    assert settle_form(planned(["a.py"]), [row("affected-only")], "tool")["via"] == "tool"


def test_the_cause_never_comes_from_a_rows_output():
    rows = [row("affected-only", False, output="exit 8 script_empty"), row("full")]
    assert settle_form(planned(["a.py"]), rows, "gate")["cause"] == "affected_red"


# --- cap_forms -------------------------------------------------------------------------


def _form(n):
    return {"check": f"c{n}", "mode": "full", "cause": "none", "via": "gate"}


def test_the_fifty_first_form_does_not_grow_the_list_and_counts_one_dropped():
    kept, dropped = cap_forms([_form(n) for n in range(50)], [_form(50)])
    assert len(kept) == 50 and dropped == 1
    assert kept[0]["check"] == "c1" and kept[-1]["check"] == "c50"


def test_under_the_cap_nothing_is_dropped_and_arrival_order_is_kept():
    kept, dropped = cap_forms([_form(0)], [_form(1), _form(2)])
    assert [f["check"] for f in kept] == ["c0", "c1", "c2"] and dropped == 0


def test_more_than_the_cap_in_one_go_keeps_the_last_ones():
    kept, dropped = cap_forms([], [_form(n) for n in range(60)])
    assert dropped == 10 and kept[0]["check"] == "c10" and len(kept) == 50
