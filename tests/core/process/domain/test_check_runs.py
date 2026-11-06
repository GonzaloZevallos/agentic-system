"""The Bash-call matcher of a Developer's own check runs (GH-467): one table at the seam
`check_runs.match`, the accepted calls and the ones it must refuse."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import check_runs
from agentic.core.process.domain.ci_push import scrub

DECLARED = {
    "tests": 'uv run pytest -m "not llm"',
    "tests#affected": "uv run python scripts/affected_tests.py {base}",
    "lint": "uv run ruff check .",
    "format": "uv run ruff format --check .",
    "types": "uv run mypy",
}

# command -> (segment, check, status_decided, detached) or None for no match.
CASES = {
    "exact": ('uv run pytest -m "not llm"', ('uv run pytest -m "not llm"', "tests", True, False)),
    "prefix, other flags": ("pytest -q tests/x.py", ("pytest -q tests/x.py", None, True, False)),
    "uv run prefix": ("uv run pytest tests/x.py", ("uv run pytest tests/x.py", None, True, False)),
    "uv run flags skipped": (
        "uv run --frozen mypy",
        ("uv run --frozen mypy", "types", True, False),
    ),
    "exact without uv run": ("ruff check .", ("ruff check .", "lint", True, False)),
    "other subcommand": ("ruff format --check .", ("ruff format --check .", "format", True, False)),
    "ruff alone is not check": ("ruff version", None),
    "affected placeholder": (
        "uv run python scripts/affected_tests.py origin/main",
        ("uv run python scripts/affected_tests.py origin/main", "tests", True, False),
    ),
    "assignment dropped": ("KEY=secret pytest -q", ("pytest -q", None, True, False)),
    "assignments before uv": (
        "A=1 B='x y' uv run mypy",
        ("uv run mypy", "types", True, False),
    ),
    "git alone": ("git status", None),
    "python alone": ("python", None),
    "python script": ("python other.py", None),
    "uv alone": ("uv sync", None),
    "bash alone": ("bash run.sh", None),
    "pipe": ("pytest -q | tail -5", ("pytest -q", None, False, False)),
    "chain": ("pytest -q && echo done", ("pytest -q", None, False, False)),
    "semicolon": ("mypy; echo $?", ("mypy", "types", False, False)),
    "or": ("mypy || true", ("mypy", "types", False, False)),
    "chain last decides": ("cd sub && uv run mypy", ("uv run mypy", "types", True, False)),
    "background": ("pytest -q &", ("pytest -q", None, True, True)),
    "background with redirect": (
        "pytest -q > out.txt 2>&1 &",
        ("pytest -q > out.txt 2>&1", None, True, True),
    ),
    "nohup": ("nohup pytest -q", ("nohup pytest -q", None, True, True)),
    "setsid": ("setsid uv run mypy", ("setsid uv run mypy", "types", True, True)),
    "redirect is no separator": ("mypy 2>&1", ("mypy 2>&1", None, True, False)),
    "operator inside quotes": ('pytest -k "a && b"', ('pytest -k "a && b"', None, True, False)),
    "no command": ("", None),
}


@pytest.mark.parametrize("command, want", CASES.values(), ids=CASES.keys())
def test_match_table(command: str, want: tuple[str, str | None, bool, bool] | None) -> None:
    got = check_runs.match(command, DECLARED)
    if want is None:
        assert got is None
    else:
        assert got == check_runs.Match(*want)


def test_token_in_an_argument_is_masked_by_the_scrub_of_the_segment() -> None:
    token = "ghp_" + "a1" * 18
    got = check_runs.match(f"pytest -q --token {token}", DECLARED)
    assert got is not None
    assert token not in scrub(got.segment, limit=200)


def test_secret_assignment_never_reaches_the_segment() -> None:
    got = check_runs.match("API_KEY=hunter2 pytest -q", DECLARED)
    assert got is not None
    assert "hunter2" not in got.segment


def test_declared_check_made_only_of_a_generic_program_matches_nothing() -> None:
    assert check_runs.match("python x.py", {"odd": "python"}) is None
    assert check_runs.match("git log", {"odd": "git"}) is None


def test_declared_forms_names_the_affected_and_rerun_forms_of_a_declared_check_only():
    from agentic.core.process.domain.presubmit import RerunForm

    forms = check_runs.declared_forms(
        {"tests": "uv run pytest", "lint": "uv run ruff check ."},
        {"tests": "uv run python scripts/affected_tests.py {base}", "gone": "x y"},
        {"tests": RerunForm("uv run pytest --lf", "id"), "gone": RerunForm("x z", "id")},
    )
    assert forms == {
        "tests": "uv run pytest",
        "lint": "uv run ruff check .",
        "tests#affected": "uv run python scripts/affected_tests.py {base}",
        "tests#rerun": "uv run pytest --lf",
    }
