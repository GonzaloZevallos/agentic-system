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
