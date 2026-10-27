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
    rows = [{**_reused(), "mode": "full"}, {**result("lint", "ruff ."), "mode": "full"}]
    text = presubmit.checks_markdown("abc123", rows, base="b4s3")
    assert "| `tests` | `pytest` | full | passed (reused) |" in text
    # The row that ran is rendered, and rendered unmarked: the row is really in the table.
    assert "| `lint` | `ruff .` | full | passed |" in text
    assert "| `lint` | `ruff .` | full | passed (reused) |" not in text


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
        {"name": "lint", "passed": True, "duration_s": 0.0},
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


def _run(row_master=None, **more):
    return presubmit.merge_checks_record(
        head="h",
        master="m2",
        duration_s=1.0,
        declared={"tests": "pytest", "lint": "ruff ."},
        results=more.pop("results", [_reused("tests"), result("lint", "ruff .")]),
        witness=None,
        row_master=row_master,
        **more,
    )


def test_without_a_row_master_no_entry_names_one():
    record = _run()
    assert all("master" not in entry for entry in record["checks"])
    assert record["master"] == "m2"


def test_with_a_row_master_every_entry_carries_it_the_reused_one_too():
    record = _run("m2")
    assert record["checks"] == [
        {"name": "tests", "passed": True, "reused": True, "master": "m2"},
        {"name": "lint", "passed": True, "duration_s": 0.0, "master": "m2"},
    ]


def test_with_a_row_master_a_failing_and_a_missing_entry_carry_it_too():
    record = _run("m2", results=[result("tests", "pytest", False, "boom")])
    assert record["passed"] is False
    assert [entry["master"] for entry in record["checks"]] == ["m2", "m2"]


def test_a_rerun_keeps_the_master_of_the_kept_rows_and_stamps_only_its_own():
    previous = {
        "master": "m1",
        "passed": False,
        "failed_step": "checks",
        "checks": [
            {"name": "lint", "passed": True, "master": "m1"},
            {"name": "tests", "passed": False, "master": "m1", "tail": ["boom"]},
        ],
    }
    rerun = {
        "master": "m2",
        "passed": True,
        "checks": [{"name": "tests", "passed": True, "master": "m2"}],
    }
    record = presubmit.rerun_record(previous, rerun)
    assert record["master"] == "m2"
    assert record["checks"] == [
        {"name": "lint", "passed": True, "master": "m1"},
        {"name": "tests", "passed": True, "master": "m2"},
    ]
    assert presubmit.row_masters(record) == ("m1", "m2")


def test_a_rerun_over_an_old_record_does_not_move_its_kept_rows_to_the_newer_master():
    """Invariant 3: a row written before rows named a master ran on the record's master;
    the rerun replaces that key, so the kept row takes it before it is lost."""
    previous = {
        "master": "m1",
        "passed": False,
        "failed_step": "checks",
        "checks": [_entry("lint", True), _entry("tests", False)],
    }
    rerun = {"master": "m2", "passed": True, "checks": [{**_entry("tests", True), "master": "m2"}]}
    record = presubmit.rerun_record(previous, rerun)
    assert presubmit.row_masters(record) == ("m1", "m2")


def test_row_masters_of_an_old_record_is_the_record_master():
    old = {"master": "m1", "checks": [_entry("lint", True), _entry("tests", True)]}
    assert presubmit.row_masters(old) == ("m1",)


def test_row_masters_of_a_mixed_record_lists_both_in_first_seen_order():
    mixed = {
        "master": "m2",
        "checks": [
            {"name": "a", "passed": True, "master": "m1"},
            {"name": "b", "passed": True},
            {"name": "c", "passed": True, "master": "m1"},
        ],
    }
    assert presubmit.row_masters(mixed) == ("m1", "m2")


def test_row_masters_reads_nothing_it_cannot_and_returns_no_master():
    assert presubmit.row_masters({}) == ()
    assert presubmit.row_masters({"master": "m", "checks": "bad"}) == ()
    assert presubmit.row_masters({"checks": [{"name": "a"}, {"name": "b", "master": 3}]}) == ()


def test_merge_checks_record_passes_a_flake_and_marks_it_and_marks_a_double_red():
    """The rerun rows of ADR-0066 decide by the last row per check: a flake passes the
    record, flagged, and a check red twice stays failed with the rerun's tail."""
    flake_rows = [
        result("tests", "pytest", False, "boom"),
        {**result("tests", "pytest", True, "ok"), "rerun": True},
    ]
    passed = presubmit.merge_checks_record(
        head="h",
        master="m",
        duration_s=1.0,
        declared={"tests": "pytest"},
        results=flake_rows,
        witness=None,
    )
    assert passed["passed"] is True and "failed_step" not in passed
    assert passed["checks"] == [{"name": "tests", "passed": True, "flake": True, "duration_s": 0.0}]
    twice = [
        result("tests", "pytest", False, "first"),
        {**result("tests", "pytest", False, "second"), "rerun": True},
    ]
    failed = presubmit.merge_checks_record(
        head="h",
        master="m",
        duration_s=1.0,
        declared={"tests": "pytest"},
        results=twice,
        witness=None,
    )
    assert failed["passed"] is False and failed["failed_step"] == "checks"
    assert failed["checks"] == [
        {"name": "tests", "passed": False, "tail": ["second"], "rerun": True, "duration_s": 0.0}
    ]


def test_below_the_budget_a_refusal_is_always_allowed():
    assert presubmit.refusal_allowed(0, None, None)
    assert presubmit.refusal_allowed(presubmit.REFUSALS - 1, ("a",), ("a",))


def test_without_failed_ids_the_budget_is_today_s_exactly():
    assert not presubmit.refusal_allowed(presubmit.REFUSALS, None, None)
    assert not presubmit.refusal_allowed(presubmit.REFUSALS, ("a",), None)
    assert not presubmit.refusal_allowed(presubmit.REFUSALS, None, ("a",))


def test_the_same_failed_ids_stop_at_the_budget():
    assert not presubmit.refusal_allowed(presubmit.REFUSALS, ("a", "b"), ("a", "b"))
    assert not presubmit.refusal_allowed(presubmit.REFUSALS, (), ())


def test_changed_failed_ids_earn_one_more_refusal_and_never_two():
    assert presubmit.refusal_allowed(presubmit.REFUSALS, ("a", "b"), ("a",))
    assert not presubmit.refusal_allowed(presubmit.REFUSALS + 1, ("a", "b"), ("a",))
    assert not presubmit.refusal_allowed(presubmit.REFUSALS + 2, ("a",), ("b",))


def test_the_order_of_failed_ids_is_not_a_change():
    assert not presubmit.refusal_allowed(presubmit.REFUSALS, ("a", "b"), ("b", "a"))


def test_a_timed_out_check_is_refused_saying_it_timed_out_and_not_to_rerun_it():
    row = result("tests", "pytest", False, "partial\n(timed out after 30 s)")
    row["timed_out"] = True
    reason = presubmit.refusal(True, True, [row])
    assert "`tests` timed out: `pytest`" in reason
    assert "do not just rerun it" in reason and "partial" in reason


def test_a_plain_red_check_keeps_its_wording():
    reason = presubmit.refusal(True, True, [result("tests", "pytest", False, "boom")])
    assert "`tests` failed: `pytest`" in reason and "timed out" not in reason


def test_the_third_refusal_is_counted_against_the_extended_budget():
    assert "(1 of 2)" in presubmit.refused("r", 1)
    assert "(2 of 2)" in presubmit.refused("r", 2)
    assert "(3 of 3)" in presubmit.refused("r", 3)


# -- each check's duration in the records (GH-365) --------------------------------------


def _timed(name="tests", seconds=1.0, **more):
    return {**result(name, "cmd"), "duration_s": seconds, **more}


def _timed_record(rows, **more):
    return presubmit.merge_checks_record(
        head="h",
        master="m",
        duration_s=9.0,
        declared={"tests": "pytest", "lint": "ruff ."},
        results=rows,
        witness=None,
        **more,
    )


def test_the_merge_record_carries_each_checks_summed_duration_like_check_durations():
    rows = [
        {**_timed("tests", 2.04), "mode": "affected-only"},
        {**_timed("tests", 3.0), "mode": "full"},
        _timed("lint", 0.26),
        _timed("lint", 50.0, rerun=True),
    ]
    record = _timed_record(rows)
    sums = presubmit.check_durations(rows)
    assert [e["duration_s"] for e in record["checks"]] == [round(sums["tests"], 1), 0.3]
    assert record["checks"][0]["duration_s"] == 5.0


def test_a_reused_only_check_and_a_missing_check_have_no_duration():
    record = _timed_record([_reused("tests")])
    assert record["checks"] == [
        {"name": "tests", "passed": True, "reused": True},
        {"name": "lint", "passed": False},
    ]


def test_a_failing_entry_and_one_with_a_master_carry_the_duration_too():
    red = _timed("tests", 4.0, passed=False, output="boom")
    record = _timed_record([red, _timed("lint", 1.0)], row_master="m")
    assert record["checks"][0]["duration_s"] == 4.0 and record["checks"][0]["tail"] == ["boom"]
    assert record["checks"][1] == {"name": "lint", "passed": True, "duration_s": 1.0, "master": "m"}


def test_the_witness_and_setup_failure_entries_never_carry_a_duration():
    witness = {"command": "w", "output": "/x", "passed": False}
    record = _timed_record(
        [_timed("tests"), _timed("lint")],
        setup_failure=("setup", "no"),
    )
    record_w = presubmit.merge_checks_record(
        head="h",
        master="m",
        duration_s=1.0,
        declared={"tests": "pytest"},
        results=[_timed("tests")],
        witness=witness,
    )
    assert "duration_s" not in record["checks"][-1]
    assert "duration_s" not in record_w["checks"][-1]


def test_the_checks_table_has_a_seconds_column_dash_when_there_is_no_sample():
    rows = [_timed("tests", 12.34), _reused("lint")]
    text = presubmit.checks_markdown("abc123", rows)
    assert "| Check | Command | Result | Seconds |" in text
    assert "| `tests` | `cmd` | passed | 12.3 |" in text
    assert "| `lint` | `pytest` | passed (reused) | - |" in text


def test_the_seconds_column_is_in_the_mode_table_too():
    rows = [{**_timed("tests", 2.0), "mode": "full"}]
    text = presubmit.checks_markdown("abc123", rows, base="b")
    assert "| Check | Command | Mode | Result | Seconds |" in text
    assert "| `tests` | `cmd` | full | passed | 2.0 |" in text


# --- decided_durations and budget_breaches (GH-365) -----------------------------------------


def test_decided_durations_reads_the_row_that_decided_each_passing_check():
    rows = [
        _timed("tests", 7.0, passed=False),
        _timed("tests", 3.0, rerun=True),
        _timed("lint", 1.0),
    ]
    assert presubmit.decided_durations(rows) == {"tests": 3.0, "lint": 1.0}


def test_decided_durations_keeps_a_reused_pass_with_its_stored_duration():
    assert presubmit.decided_durations([_reused("tests")]) == {"tests": 99.0}


def test_decided_durations_drops_a_failed_check_and_a_row_without_duration():
    rows = [_timed("tests", 5.0, passed=False), result("lint"), _timed("types", 2.0)]
    assert presubmit.decided_durations(rows) == {"types": 2.0}


def test_decided_durations_differs_from_check_durations_on_the_same_rows():
    rows = [_timed("tests", 7.0, passed=False), _timed("tests", 3.0, rerun=True), _reused("lint")]
    assert presubmit.check_durations(rows) == {"tests": 7.0}
    assert presubmit.decided_durations(rows) == {"tests": 3.0, "lint": 99.0}


def test_a_first_green_run_over_budget_breaches_and_one_at_the_budget_does_not():
    rows = [_timed("tests", 10.5), _timed("lint", 5.0)]
    assert presubmit.budget_breaches(rows, {"tests": 10.0, "lint": 5.0}) == [
        {"name": "tests", "duration_s": 10.5, "budget_s": 10.0}
    ]


def test_a_flake_is_judged_on_its_green_rerun_not_on_the_red_first_run():
    rows = [_timed("tests", 500.0, passed=False), _timed("tests", 4.0, rerun=True)]
    assert presubmit.budget_breaches(rows, {"tests": 10.0}) == []
    slow = [_timed("tests", 5.0, passed=False), _timed("tests", 40.0, rerun=True)]
    assert presubmit.budget_breaches(slow, {"tests": 10.0}) == [
        {"name": "tests", "duration_s": 40.0, "budget_s": 10.0}
    ]


def test_a_failed_or_timed_out_check_never_breaches():
    rows = [
        _timed("tests", 900.0, passed=False, timed_out=True),
        _timed("lint", 900.0, passed=False),
    ]
    assert presubmit.budget_breaches(rows, {"tests": 10.0, "lint": 10.0}) == []


def test_a_reused_pass_over_budget_breaches_and_one_without_duration_does_not():
    assert presubmit.budget_breaches([_reused("tests")], {"tests": 10.0}) == [
        {"name": "tests", "duration_s": 99.0, "budget_s": 10.0}
    ]
    bare = {**result("tests"), "reused": True}
    assert presubmit.budget_breaches([bare], {"tests": 10.0}) == []


def test_an_unbudgeted_check_never_appears_and_breaches_follow_the_budget_order():
    rows = [_timed("tests", 50.0), _timed("lint", 50.0), _timed("types", 50.0)]
    found = presubmit.budget_breaches(rows, {"types": 1.0, "tests": 1.0})
    assert [b["name"] for b in found] == ["types", "tests"]
    assert presubmit.budget_breaches(rows, {}) == []
