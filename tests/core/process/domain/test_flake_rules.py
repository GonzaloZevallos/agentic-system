"""The pure rules of the rerun (ADR-0066): the verdict, the ids a failure names, and which
command the rerun uses."""

from agentic.core.process.domain.presubmit import (
    FAILED_IDS_MAX,
    FAILED_IDS_TAIL_BYTES,
    RerunForm,
    failed_ids,
    flake_verdict,
    rerun_command,
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


def test_the_delivery_gate_reruns_the_form_when_ids_were_parsed():
    first = row(False, "FAILED a.py::t\n")
    assert rerun_command("pytest", first, FORM, "delivery") == "pytest --lf"


def test_the_delivery_gate_reruns_the_full_command_when_no_id_was_parsed():
    assert rerun_command("pytest", row(False, "boom\n"), FORM, "delivery") == "pytest"


def test_the_delivery_gate_reruns_the_full_command_with_no_form():
    assert rerun_command("pytest", row(False, "FAILED a.py::t\n"), None, "delivery") == "pytest"


def test_the_merge_gate_and_full_checks_rerun_the_full_command_never_the_form():
    first = row(False, "FAILED a.py::t\n")
    assert rerun_command("pytest", first, FORM, "merge") == "pytest"
    assert rerun_command("pytest", first, FORM, "full_checks") == "pytest"
