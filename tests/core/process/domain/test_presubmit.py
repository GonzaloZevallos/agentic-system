from pathlib import Path

from agentic.core.process.domain import presubmit

CHECKOUT = Path("/work/checkouts/GH-41.api")


def result(name="tests", command="pytest", passed=True, output="1 passed"):
    return {"name": name, "command": command, "passed": passed, "output": output}


def test_a_committed_clean_green_delivery_passes():
    assert presubmit.refusal(moved=True, clean=True, results=[result(), result("lint")]) is None


def test_a_delivery_without_a_commit_or_with_changes_left_is_refused_first():
    assert "no commit on the branch" in presubmit.refusal(moved=False, clean=True, results=[])
    assert "uncommitted changes" in presubmit.refusal(moved=True, clean=False, results=[])


def test_a_red_check_is_refused_with_its_command_and_last_lines():
    output = "\n".join(f"line {n}" for n in range(100)) + "\nFAILED tests/test_x.py::t"
    red = result("lint", "ruff check .", False, output)
    reason = presubmit.refusal(True, True, [result(), red])
    assert "`lint` failed: `ruff check .`" in reason
    assert "FAILED tests/test_x.py::t" in reason and "line 0\n" not in reason
    assert "`tests`" not in reason


def test_tail_keeps_the_last_lines():
    assert presubmit.tail("a\nb\nc\n", lines=2) == "b\nc"


def test_the_checks_as_the_document_keeps_them():
    text = presubmit.checks_markdown("abc123", [result(), result("lint", "ruff .", False, "E501")])
    assert text.startswith("### Checks\n\nRun by the system on `abc123`")
    assert "| `tests` | `pytest` | passed |" in text
    assert "| `lint` | `ruff .` | **failed** |" in text
    assert "`lint`, last lines:\n\n```text\nE501\n```" in text


def test_the_checks_as_a_prompt_lists_them():
    assert presubmit.describe_checks({"tests": "pytest", "lint": "ruff check ."}) == (
        "- `tests`: `pytest`\n- `lint`: `ruff check .`"
    )


# `witness_refusal`: the pure verdict (glossary: Testigo del checkout). Decided strictly
# in order -- timeout, then exit status, then the last non-empty line of the RAW
# output -- so a verdict never tries to resolve `presubmit.said`'s own note as a path.


def test_a_witness_that_prints_the_checkout_agrees():
    assert presubmit.witness_refusal("w", f"{CHECKOUT}\n", 0, False, CHECKOUT) is None


def test_a_witness_that_prints_a_sibling_worktree_disagrees():
    reason = presubmit.witness_refusal("w", "/work/checkouts/GH-9.other\n", 0, False, CHECKOUT)
    assert reason is not None and "/work/checkouts/GH-9.other" in reason and "`w`" in reason


def test_a_witness_that_prints_the_parent_checkout_disagrees():
    reason = presubmit.witness_refusal("w", "/work/checkouts\n", 0, False, CHECKOUT)
    assert reason is not None and "/work/checkouts" in reason


def test_a_witness_that_escapes_via_dot_dot_disagrees():
    """A path that only textually starts with the checkout but escapes it once its
    `..` segments collapse must not read as one that is still inside it: pure,
    no-filesystem normalisation, never a lexical prefix match."""
    reason = presubmit.witness_refusal("w", f"{CHECKOUT}/../sibling\n", 0, False, CHECKOUT)
    assert reason is not None and "sibling" in reason


def test_a_witness_that_prints_a_relative_path_disagrees():
    reason = presubmit.witness_refusal("w", "checkouts/GH-41.api\n", 0, False, CHECKOUT)
    assert reason is not None


def test_a_witness_with_empty_output_disagrees():
    reason = presubmit.witness_refusal("w", "   \n\n", 0, False, CHECKOUT)
    assert reason is not None


def test_a_witness_whose_last_line_is_noise_disagrees():
    output = f"{CHECKOUT}\nDeprecationWarning: something unrelated\n"
    reason = presubmit.witness_refusal("w", output, 0, False, CHECKOUT)
    assert reason is not None and "DeprecationWarning" in reason


def test_a_witness_with_a_non_zero_exit_disagrees_without_reading_the_text():
    reason = presubmit.witness_refusal("w", f"{CHECKOUT}\n", 1, False, CHECKOUT)
    assert reason is not None and str(CHECKOUT) not in reason


def test_a_timed_out_witness_disagrees_without_reading_its_annotated_note():
    """`presubmit.said` appends a note a killed witness never wrote; the verdict must
    never try to read it as the printed path, whatever `output` holds."""
    annotated = presubmit.said(f"{CHECKOUT}\n", True, 30.0)
    reason = presubmit.witness_refusal("w", annotated, 0, True, CHECKOUT)
    assert reason is not None and "timed out after 30" not in reason


def test_the_checks_markdown_gains_the_witness_line_whenever_it_ran():
    witness = {"command": "make witness", "output": str(CHECKOUT), "passed": True}
    text = presubmit.checks_markdown("abc123", [result()], witness)
    assert "make witness" in text and str(CHECKOUT) in text and "agreed" in text.lower()


def test_the_witness_line_shows_a_disagreement_too_beside_the_checks_commands():
    witness = {"command": "make witness", "output": "/elsewhere", "passed": False}
    text = presubmit.checks_markdown("abc123", [result()], witness)
    assert "### Checks" in text
    assert "make witness" in text and "/elsewhere" in text and "disagreed" in text.lower()


def test_no_witness_leaves_the_markdown_exactly_as_before():
    text = presubmit.checks_markdown("abc123", [result()])
    assert "witness" not in text.lower()


PLAN = presubmit.AffectedPlan(
    base="abc123",
    commands={"tests": "pytest --picked={base}", "lint": "ruff check --since {base}"},
    fallback=("pyproject.toml", "tests/conftest.py", "*.lock"),
)
NAMES = ["tests", "lint", "types"]


def test_a_change_that_touches_no_fallback_runs_affected_only_where_a_form_exists():
    modes = presubmit.check_modes(NAMES, PLAN, ["src/a.py"])
    assert modes == {"tests": "affected-only", "lint": "affected-only", "types": "full"}


def test_a_name_without_an_affected_form_stays_full_even_on_a_small_change():
    assert presubmit.check_modes(["types"], PLAN, ["src/a.py"]) == {"types": "full"}


def test_a_failed_changed_paths_lookup_runs_everything_full():
    assert set(presubmit.check_modes(NAMES, PLAN, None).values()) == {"full"}


def test_an_empty_change_set_runs_everything_full():
    assert set(presubmit.check_modes(NAMES, PLAN, []).values()) == {"full"}


def test_a_fallback_match_anywhere_in_the_change_forces_every_check_full():
    modes = presubmit.check_modes(NAMES, PLAN, ["src/a.py", "pyproject.toml", "src/b.py"])
    assert set(modes.values()) == {"full"}


def test_the_stack_file_is_an_implicit_fallback_even_when_not_declared():
    plan = presubmit.AffectedPlan(base="b", commands={"tests": "t {base}"}, fallback=())
    assert presubmit.check_modes(["tests"], plan, [".agentic/stack.yaml"]) == {"tests": "full"}
    assert presubmit.check_modes(["tests"], plan, ["src/a.py"]) == {"tests": "affected-only"}


def test_a_star_crosses_slashes_so_a_broad_glob_falls_back_to_full():
    modes = presubmit.check_modes(NAMES, PLAN, ["deep/nested/path/poetry.lock"])
    assert set(modes.values()) == {"full"}


def test_a_glob_is_case_sensitive_and_matches_the_whole_path():
    modes = presubmit.check_modes(["tests"], PLAN, ["src/Pyproject.toml", "src/pyproject.toml.bak"])
    assert modes == {"tests": "affected-only"}


def test_the_base_is_substituted_by_replace_leaving_other_braces_intact():
    command = "jest --changedSince={base} --json='{\"a\": {}}' {x}"
    assert presubmit.affected_command(command, "abc") == (
        "jest --changedSince=abc --json='{\"a\": {}}' {x}"
    )


def test_the_prefix_is_the_literal_text_before_the_base_token_right_stripped():
    assert presubmit.affected_prefix("vitest run --changed {base} --reporter=dot") == (
        "vitest run --changed"
    )
    assert presubmit.affected_prefix("pytest --base={base}") == "pytest --base="


def test_a_red_affected_row_then_a_green_full_row_leaves_a_passing_final_row():
    rows = [
        result("tests", "pytest -k x", False, "boom"),
        result("lint"),
        result("tests", "pytest", True),
    ]
    final = presubmit.final_results(rows)
    assert [r["name"] for r in final] == ["tests", "lint"]
    assert final[0]["passed"] is True and final[0]["command"] == "pytest"
    assert presubmit.refusal(True, True, final) is None


def test_a_green_affected_row_then_a_red_full_row_leaves_a_failing_final_row():
    rows = [result("tests", "a", True), result("tests", "b", False, "boom")]
    final = presubmit.final_results(rows)
    assert presubmit.refusal(True, True, final) is not None


def test_final_results_keeps_first_seen_order_whatever_the_rerun_order():
    rows = [result("a"), result("b"), result("c"), result("b", "again"), result("a", "again")]
    final = presubmit.final_results(rows)
    assert [(r["name"], r["command"]) for r in final] == [
        ("a", "again"),
        ("b", "again"),
        ("c", "pytest"),
    ]


def test_durations_are_summed_per_name_and_a_row_without_one_counts_zero():
    rows = [
        {**result("tests"), "duration_s": 1.5},
        {**result("lint"), "duration_s": 2.0},
        {**result("tests"), "duration_s": 4.0},
        result("types"),
    ]
    assert presubmit.check_durations(rows) == {"tests": 5.5, "lint": 2.0, "types": 0.0}


def test_the_markdown_shows_every_row_with_a_mode_column_and_the_system_line():
    rows = [
        {**result("tests", "pytest -k x", False, "boom"), "mode": "affected-only"},
        {**result("tests", "pytest"), "mode": "full"},
    ]
    text = presubmit.checks_markdown("abc123", rows, base="b4s3")
    assert "| Check | Command | Mode | Result |" in text
    assert "| `tests` | `pytest -k x` | affected-only | **failed** |" in text
    assert "| `tests` | `pytest` | full | passed |" in text
    assert "`b4s3`" in text and "task_base..head" in text and "ADR-0045" in text
    assert "boom" in text


def test_the_markdown_without_any_mode_has_neither_column_nor_line():
    text = presubmit.checks_markdown("abc123", [result()], base="b4s3")
    assert "Mode" not in text and "task_base" not in text and "b4s3" not in text
    assert text == presubmit.checks_markdown("abc123", [result()])


def test_describe_checks_with_a_plan_lists_the_substituted_form_beside_the_full_one():
    checks = {"tests": "pytest", "lint": "ruff check .", "types": "mypy"}
    text = presubmit.describe_checks(checks, affected=PLAN)
    assert "- `tests`: `pytest`" in text and "pytest --picked=abc123" in text
    assert "ruff check --since abc123" in text and "{base}" not in text
    assert "- `types`: `mypy`" in text
    assert "not evidence for the whole suite" in text
    plain = presubmit.describe_checks(checks)
    assert plain == presubmit.describe_checks(checks, affected=None)
    assert "affected" not in plain


def _entry(name, passed):
    return {"name": name, "passed": passed, "output": "" if passed else "boom"}


def test_a_rerun_that_turns_the_failed_check_green_makes_the_whole_record_pass():
    previous = {
        "passed": False,
        "failed_step": "checks",
        "checks": [_entry("lint", True), _entry("tests", False)],
    }
    rerun = {"passed": True, "checks": [_entry("tests", True)]}
    record = presubmit.rerun_record(previous, rerun)
    assert record["passed"] is True
    assert "failed_step" not in record
    assert [(e["name"], e["passed"]) for e in record["checks"]] == [("lint", True), ("tests", True)]


def test_a_rerun_that_stays_red_keeps_the_record_failed():
    previous = {
        "passed": False,
        "failed_step": "checks",
        "checks": [_entry("lint", True), _entry("tests", False)],
    }
    rerun = {"passed": False, "failed_step": "checks", "checks": [_entry("tests", False)]}
    record = presubmit.rerun_record(previous, rerun)
    assert record["passed"] is False
    assert record["failed_step"] == "checks"
    assert presubmit.failed_names(record) == ["tests"]


def test_a_rerun_that_stopped_before_the_checks_ran_stands_as_it_is():
    previous = {"passed": False, "failed_step": "checks", "checks": [_entry("tests", False)]}
    rerun = {"passed": False, "failed_step": "conflict", "checks": []}
    assert presubmit.rerun_record(previous, rerun) == rerun


# -- criteria documents the delivery makes longer than the cap (GH-334) --------------

REGRESSION = "`DESIGN_STANDARDS.md`: 611 lines (cap 300), 290 at the base; cut the file"


def test_a_delivery_with_a_criteria_regression_is_refused_with_the_message():
    reason = presubmit.refusal(True, True, [result()], [REGRESSION])
    assert reason is not None
    assert "DESIGN_STANDARDS.md" in reason and "611" in reason and "cap 300" in reason


def test_a_delivery_without_regressions_is_untouched_by_the_new_input():
    assert presubmit.refusal(True, True, [result()], []) is None
    assert presubmit.refusal(True, True, [result()]) is None


def test_a_regression_and_a_red_check_are_both_in_the_one_reason():
    red = result("lint", "ruff check .", False, "E501")
    reason = presubmit.refusal(True, True, [red], [REGRESSION])
    assert "`lint` failed" in reason and "DESIGN_STANDARDS.md" in reason


def test_a_missing_commit_leads_over_a_regression():
    assert "no commit on the branch" in presubmit.refusal(False, True, [], [REGRESSION])


def test_the_regression_is_written_in_the_developers_section_when_the_gate_lets_it_through():
    text = presubmit.checks_markdown("abc123", [result()], regressions=[REGRESSION])
    assert "DESIGN_STANDARDS.md" in text and "611" in text
    assert "DESIGN_STANDARDS" not in presubmit.checks_markdown("abc123", [result()])


# -- rows the system took from a stored pass (ADR-0065) -------------------------------


def _reused(name="tests"):
    return {**result(name), "duration_s": 99.0, "reused": True}


def test_a_reused_row_says_so_next_to_its_check_and_a_row_that_ran_does_not():
    text = presubmit.checks_markdown("abc123", [_reused(), result("lint", "ruff .")])
    assert "| `tests` | `pytest` | passed (reused) |" in text
    assert "| `lint` | `ruff .` | passed |" in text


def test_a_reused_row_keeps_its_mark_in_the_mode_table_too():
    rows = [{**_reused(), "mode": "full"}, {**result("lint"), "mode": "full"}]
    text = presubmit.checks_markdown("abc123", rows, base="b4s3")
    assert "| `tests` | `pytest` | full | passed (reused) |" in text
    assert "| `lint` | `ruff .` | full | passed |" not in text


def test_durations_leave_out_a_reused_row_and_a_check_with_only_reused_rows_has_none():
    rows = [
        {**result("tests"), "duration_s": 1.5},
        _reused("tests"),
        _reused("lint"),
    ]
    assert presubmit.check_durations(rows) == {"tests": 1.5}


def test_the_merge_record_marks_a_reused_entry_and_leaves_one_that_ran_unmarked():
    record = presubmit.merge_checks_record(
        head="a" * 40,
        master="b" * 40,
        duration_s=1.0,
        declared={"tests": "pytest", "lint": "ruff ."},
        results=[_reused("tests"), result("lint", "ruff .")],
        witness=None,
    )
    assert record["checks"] == [
        {"name": "tests", "passed": True, "reused": True},
        {"name": "lint", "passed": True},
    ]


def test_a_rerun_record_keeps_the_mark_of_the_entries_it_carries():
    previous = {
        "passed": False,
        "failed_step": "checks",
        "checks": [{"name": "lint", "passed": True, "reused": True}, _entry("tests", False)],
    }
    rerun = {"passed": True, "checks": [{"name": "tests", "passed": True, "reused": True}]}
    record = presubmit.rerun_record(previous, rerun)
    assert record["checks"] == [
        {"name": "lint", "passed": True, "reused": True},
        {"name": "tests", "passed": True, "reused": True},
    ]
