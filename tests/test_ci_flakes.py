"""`scripts/ci_flakes.py` (GH-464, ADR-0085), run as a process over junit fixtures: it prints the
`agentic-flakes:` block for the tests that failed first and passed on the retry, and nothing
else, with exit 0 whatever the input. One test runs real pytest to see the ids the script prints
agree with the ids the gate reads from pytest's `FAILED` lines."""

import json
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

import yaml

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain.ci import (
    FLAKE_BLOCK_PREFIX,
    FLAKE_ID_MAX_CHARS,
    parse_flake_blocks,
)
from agentic.core.process.domain.presubmit import FAILED_IDS_MAX, failed_ids

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ci_flakes.py"
STACK_YAML = REPO / ".agentic" / "stack.yaml"

MOD = "tests/test_mod.py"


def case(name, *children, classname="tests.test_mod", file=MOD):
    inner = "".join(f"<{child}/>" if child != "pass" else "" for child in children)
    attrs = f"classname={quoteattr(classname)} name={quoteattr(name)} file={quoteattr(file)}"
    return f"<testcase {attrs}>{inner}</testcase>"


def junit(*cases, nested=False):
    body = "".join(cases)
    suite = f'<testsuite name="pytest">{body}</testsuite>'
    return f"<testsuites>{suite}</testsuites>" if nested else suite


def run(tmp_path, first, second, check="tests"):
    paths = []
    for name, text in (("first.xml", first), ("second.xml", second)):
        path = tmp_path / name
        if text is not None:
            path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--check", check, *paths],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=30,
    )


def block_tests(result):
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith(FLAKE_BLOCK_PREFIX)
    return json.loads(lines[0][len(FLAKE_BLOCK_PREFIX) :])["tests"]


def test_red_then_green_prints_the_block(tmp_path):
    first = junit(case("test_a", "failure"), case("test_b"))
    second = junit(case("test_a"))
    result = run(tmp_path, first, second)
    assert result.stdout == (
        FLAKE_BLOCK_PREFIX + json.dumps({"v": 1, "check": "tests", "tests": [f"{MOD}::test_a"]})
    ) + "\n"
    assert result.returncode == 0


def test_the_check_name_is_the_one_given(tmp_path):
    result = run(tmp_path, junit(case("t", "failure")), junit(case("t")), check="lint")
    assert json.loads(result.stdout[len(FLAKE_BLOCK_PREFIX) :])["check"] == "lint"


def test_both_red_is_no_flake_for_the_still_red_test(tmp_path):
    first = junit(case("test_a", "failure"), case("test_b", "failure"))
    second = junit(case("test_a", "failure"), case("test_b"))
    assert block_tests(run(tmp_path, first, second)) == [f"{MOD}::test_b"]


def test_both_red_prints_nothing(tmp_path):
    result = run(tmp_path, junit(case("t", "failure")), junit(case("t", "failure")))
    assert (result.stdout, result.returncode) == ("", 0)


def test_first_green_prints_nothing(tmp_path):
    result = run(tmp_path, junit(case("t")), junit(case("t")))
    assert (result.stdout, result.returncode) == ("", 0)


def test_malformed_xml_prints_no_block_and_exits_zero(tmp_path):
    good = junit(case("t", "failure"))
    for first, second in (("<not-xml", good), (good, "<testsuite><testcase"), ("", good)):
        result = run(tmp_path, first, second)
        assert (result.stdout, result.returncode) == ("", 0)


def test_a_second_file_with_an_unclosed_root_prints_no_block(tmp_path):
    first = junit(case("t", "failure"))
    second = '<testsuite name="pytest">' + case("t")
    assert run(tmp_path, first, second).stdout == ""


def test_a_missing_file_prints_no_block_and_exits_zero(tmp_path):
    result = run(tmp_path, junit(case("t", "failure")), None)
    assert (result.stdout, result.returncode) == ("", 0)


def test_bad_arguments_still_exit_zero(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--nope"], capture_output=True, text=True, cwd=REPO
    )
    assert (result.stdout, result.returncode) == ("", 0)


def test_a_test_absent_on_retry_is_no_flake(tmp_path):
    first = junit(case("test_a", "failure"), case("test_b", "failure"))
    second = junit(case("test_b"))
    assert block_tests(run(tmp_path, first, second)) == [f"{MOD}::test_b"]


def test_a_test_skipped_on_retry_is_no_flake(tmp_path):
    result = run(tmp_path, junit(case("t", "failure")), junit(case("t", "skipped")))
    assert result.stdout == ""


def test_a_test_in_error_on_retry_is_no_flake(tmp_path):
    result = run(tmp_path, junit(case("t", "failure")), junit(case("t", "error")))
    assert result.stdout == ""


def test_an_error_on_the_first_run_is_never_a_candidate(tmp_path):
    result = run(tmp_path, junit(case("t", "error")), junit(case("t")))
    assert (result.stdout, result.returncode) == ("", 0)


def test_a_well_formed_second_file_with_a_subset_gives_only_that_subset(tmp_path):
    first = junit(*(case(f"t{n}", "failure") for n in range(4)))
    second = junit(case("t1"), case("t2", "failure"))
    assert block_tests(run(tmp_path, first, second)) == [f"{MOD}::t1"]


def test_ids_are_rebuilt_with_class_parts_nesting_and_parameters_with_spaces(tmp_path):
    cls = "pkg.sub.test_x.TestA.TestB"
    file = "pkg/sub/test_x.py"
    first = junit(
        case("test_p[a b]", "failure", classname=cls, file=file),
        case("test_q", "failure", classname="pkg.sub.test_x", file=file),
        nested=True,
    )
    second = junit(
        case("test_p[a b]", classname=cls, file=file),
        case("test_q", classname="pkg.sub.test_x", file=file),
        nested=True,
    )
    assert block_tests(run(tmp_path, first, second)) == [
        f"{file}::TestA::TestB::test_p[a b]",
        f"{file}::test_q",
    ]


def test_a_testcase_whose_id_cannot_be_rebuilt_is_no_evidence(tmp_path):
    odd = case("t", "failure", classname="elsewhere.mod")
    result = run(tmp_path, junit(odd), junit(case("t", classname="elsewhere.mod")))
    assert result.stdout == ""
    no_file = '<testcase classname="a" name="t"><failure/></testcase>'
    assert run(tmp_path, junit(no_file), junit(no_file.replace("<failure/>", ""))).stdout == ""


def test_a_hostile_id_never_appears_in_the_output(tmp_path):
    names = ["a`b", "a{{b", "a<!--b", "x" * (FLAKE_ID_MAX_CHARS + 1), "fine"]
    first = junit(*(case(name, "failure") for name in names))
    second = junit(*(case(name) for name in names))
    assert block_tests(run(tmp_path, first, second)) == [f"{MOD}::fine"]


def test_sixty_flaky_ids_give_fifty(tmp_path):
    names = [f"t{n}" for n in range(60)]
    first = junit(*(case(name, "failure") for name in names))
    second = junit(*(case(name) for name in names))
    assert block_tests(run(tmp_path, first, second)) == [f"{MOD}::{n}" for n in names[:50]]
    assert FAILED_IDS_MAX == 50


def test_what_the_script_prints_the_parser_returns_whole(tmp_path):
    first = junit(case("test_p[a b]", "failure"), case("t2", "failure"))
    second = junit(case("test_p[a b]"), case("t2"))
    line = run(tmp_path, first, second).stdout.strip()
    records = parse_flake_blocks({"check": [line]}, frozenset({"tests"}), "head", "full_checks")
    assert [r["tests"] for r in records] == [[f"{MOD}::test_p[a b]", f"{MOD}::t2"]]


TINY_TESTS = '''
import pathlib

import pytest

HERE = pathlib.Path(__file__).parent


def red_once(name):
    marker = HERE / name
    if not marker.exists():
        marker.write_text("seen")
        raise AssertionError(name)


def test_red_once():
    red_once("m1")


class TestGroup:
    def test_red_once_in_a_class(self):
        red_once("m2")

    @pytest.mark.parametrize("value", ["a b", "c"])
    def test_param(self, value):
        if value == "a b":
            red_once("m3")


def test_always_red():
    assert False


def test_green():
    pass
'''


def pytest_in(project, *args):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "junit_family=xunit1", *args],
        capture_output=True,
        text=True,
        cwd=project,
        timeout=60,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )


def tiny_project(tmp_path):
    project = tmp_path / "project"
    nested = project / "pkg" / "sub"
    nested.mkdir(parents=True)
    (project / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (nested / "test_mod.py").write_text(TINY_TESTS, encoding="utf-8")
    return project


def real_test_id():
    data = yaml.safe_load(STACK_YAML.read_text(encoding="utf-8"))
    return stack.rerun_checks(data, STACK_YAML)["tests"].test_id


def test_the_ids_printed_agree_with_the_ids_the_gate_reads_from_real_pytest(tmp_path):
    project = tiny_project(tmp_path)
    first = pytest_in(project, "--junitxml=first.xml")
    assert first.returncode != 0
    retry = pytest_in(
        project,
        "--last-failed",
        "--last-failed-no-failures",
        "none",
        "--junitxml=second.xml",
    )
    assert retry.returncode != 0  # test_always_red stays red
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "tests", "first.xml", "second.xml"],
        capture_output=True,
        text=True,
        cwd=project,
        timeout=30,
    )
    printed = set(block_tests(result))
    gate = set(failed_ids(first.stdout, real_test_id()))
    always_red = "pkg/sub/test_mod.py::test_always_red"
    assert always_red in gate
    assert printed == gate - {always_red}
    assert "pkg/sub/test_mod.py::TestGroup::test_param[a b]" in printed
    assert "pkg/sub/test_mod.py::TestGroup::test_red_once_in_a_class" in printed
    assert len(printed) == 3


def test_a_retry_with_an_empty_cache_runs_nothing_and_exits_non_zero(tmp_path):
    project = tiny_project(tmp_path)
    retry = pytest_in(project, "--last-failed", "--last-failed-no-failures", "none")
    assert retry.returncode != 0
    assert "passed" not in retry.stdout
