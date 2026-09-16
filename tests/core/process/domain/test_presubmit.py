from agentic.core.process.domain import presubmit


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
