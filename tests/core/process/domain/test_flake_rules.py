"""The pure rules of the rerun (ADR-0066): the verdict, the ids a failure names, and which
command the rerun uses."""

import pytest

from agentic.core.process.domain.presubmit import (
    FAILED_IDS_MAX,
    FAILED_IDS_TAIL_BYTES,
    RerunForm,
    check_runs,
    failed_ids,
    flake_verdict,
    ids_complete,
    rerun_command,
    rerun_resolved,
)

FORM = RerunForm(command="pytest --lf", test_id=r"(?m)^FAILED (\S+)")


def row(passed=True, output="", **extra):
    return {"name": "tests", "command": "pytest", "passed": passed, "output": output, **extra}


# --- flake_verdict -----------------------------------------------------------------------


def test_green_the_first_time_is_a_pass_with_no_rerun():
    assert flake_verdict(row(True), None) == "pass"


def test_red_then_green_is_a_flake():
    assert flake_verdict(row(False), row(True, rerun=True)) == "flake"


def test_red_twice_is_a_fail():
    assert flake_verdict(row(False), row(False, rerun=True)) == "fail"


def test_red_with_no_rerun_is_a_fail():
    assert flake_verdict(row(False), None) == "fail"


def test_a_timed_out_first_row_is_a_fail_even_when_handed_a_green_rerun():
    assert flake_verdict(row(False, timed_out=True), row(True, rerun=True)) == "fail"


# --- failed_ids --------------------------------------------------------------------------


def test_the_ids_are_group_one_of_each_match_in_order_without_repeats():
    output = "FAILED a.py::t1\nok\nFAILED b.py::t2\nFAILED a.py::t1\n"
    assert failed_ids(output, r"(?m)^FAILED (\S+)") == ("a.py::t1", "b.py::t2")


def test_a_multi_megabyte_output_with_the_summary_at_the_end_yields_its_ids():
    noise = "x" * 100 + "\n"
    output = noise * 30_000 + "FAILED a.py::t1\nFAILED b.py::t2\n"
    assert len(output) > 3_000_000
    assert failed_ids(output, r"(?m)^FAILED (\S+)") == ("a.py::t1", "b.py::t2")


def test_ids_that_sit_only_before_the_tail_are_never_seen():
    early = "FAILED early.py::t\n"
    output = early + "x" * (FAILED_IDS_TAIL_BYTES + 10)
    assert failed_ids(output, r"(?m)^FAILED (\S+)") == ()


def test_the_tail_is_counted_in_bytes_not_characters():
    early = "FAILED early.py::t\n"
    output = early + "é" * (FAILED_IDS_TAIL_BYTES // 2 + 10)  # 2 bytes each: past the tail
    assert failed_ids(output, r"(?m)^FAILED (\S+)") == ()


def test_at_most_fifty_ids_are_returned():
    output = "".join(f"FAILED t{n}.py::t\n" for n in range(200))
    ids = failed_ids(output, r"(?m)^FAILED (\S+)")
    assert len(ids) == FAILED_IDS_MAX == 50
    assert ids[0] == "t0.py::t"


def test_an_optional_group_that_did_not_match_is_not_an_id():
    assert failed_ids("ab", r"(x)?b") == ()


def test_a_pattern_that_does_not_compile_names_no_ids():
    assert failed_ids("FAILED a", "(") == ()


# --- rerun_command -----------------------------------------------------------------------


def test_a_timed_out_row_is_never_rerun():
    first = row(False, "FAILED a.py::t", timed_out=True)
    assert rerun_command("pytest", first, FORM, "delivery") is None


def test_a_green_row_is_never_rerun():
    assert rerun_command("pytest", row(True), FORM, "delivery") is None


def test_a_rerun_row_is_never_rerun():
    assert (
        rerun_command("pytest", row(False, "FAILED a.py::t", rerun=True), FORM, "delivery") is None
    )


@pytest.mark.parametrize("gate", ["delivery", "merge", "full_checks"])
def test_every_gate_reruns_the_form_when_ids_were_parsed(gate):
    first = row(False, "FAILED a.py::t\n")
    assert rerun_command("pytest", first, FORM, gate) == "pytest --lf"


@pytest.mark.parametrize("gate", ["delivery", "merge", "full_checks"])
def test_every_gate_reruns_the_full_command_when_no_id_was_parsed(gate):
    assert rerun_command("pytest", row(False, "boom\n"), FORM, gate) == "pytest"


@pytest.mark.parametrize("gate", ["delivery", "merge", "full_checks"])
def test_every_gate_reruns_the_full_command_with_no_form(gate):
    assert rerun_command("pytest", row(False, "FAILED a.py::t\n"), None, gate) == "pytest"


def test_a_not_ready_row_is_never_rerun():
    assert (
        rerun_command("pytest", row(False, "FAILED a.py::t\n", not_ready=True), FORM, "merge")
        is None
    )


def test_the_full_command_reruns_when_the_ids_may_be_incomplete():
    at_cap = "".join(f"FAILED t{n}.py::t\n" for n in range(FAILED_IDS_MAX + 10))
    assert rerun_command("pytest", row(False, at_cap), FORM, "merge") == "pytest"
    assert rerun_command(
        "pytest", row(False, "FAILED a.py::t\n" + "x" * 70_000), FORM, "merge"
    ) == ("pytest")


# --- ids_complete ------------------------------------------------------------------------


def test_ids_are_complete_below_the_cap_and_within_the_tail():
    assert ids_complete("FAILED a\n", ["a"])
    assert ids_complete("x" * FAILED_IDS_TAIL_BYTES, ["a"])
    assert ids_complete("", [f"t{n}" for n in range(FAILED_IDS_MAX - 1)])


def test_ids_are_not_complete_at_the_cap_or_past_the_tail():
    assert not ids_complete("", [f"t{n}" for n in range(FAILED_IDS_MAX)])
    assert not ids_complete("x" * (FAILED_IDS_TAIL_BYTES + 1), ["a"])
    # counted in bytes: 2 bytes per character
    assert not ids_complete("é" * (FAILED_IDS_TAIL_BYTES // 2 + 1), ["a"])


# --- rerun_resolved ----------------------------------------------------------------------


def test_a_green_form_row_is_resolved():
    assert rerun_resolved(row(True, "", rerun=True), ["a.py::t"], FORM)


def test_a_red_form_row_naming_one_of_the_ids_is_resolved():
    form_row = row(False, "FAILED a.py::t\nFAILED c.py::u\n", rerun=True)
    assert rerun_resolved(form_row, ["a.py::t", "b.py::t"], FORM)


@pytest.mark.parametrize(
    "output",
    [
        "no tests ran\n",  # nothing selected
        "FAILED a.py::t[a\n",  # a truncated id
        "error: unrecognized arguments: --lf\n",  # a command error
        "FAILED other.py::t\n",  # red, but not the tests it was asked to rerun
    ],
)
def test_a_red_form_row_that_names_none_of_the_ids_is_unresolved(output):
    assert not rerun_resolved(row(False, output, rerun=True), ["a.py::t"], FORM)


# --- check_runs --------------------------------------------------------------------------


def test_a_check_with_a_form_runs_three_times_at_most_and_without_one_twice():
    assert check_runs(True) == 3
    assert check_runs(False) == 2
