"""`returns.is_return` and `returns.finding_origin`: the two places that decide whether a
`change_history` entry is a real return and where an `important` row came from."""

import re
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.domain import returns

# --- is_return -------------------------------------------------------------------------


def test_a_landing_reopen_is_not_a_return():
    entry = {"pair": "Tech Lead→Developer:a", "category": "spec-scope", "by": "landing"}
    assert returns.is_return(entry) is False


def test_a_review_return_is_a_return():
    entry = {"pair": "Tech Lead→Developer:a", "category": "spec-scope", "reason": "r"}
    assert returns.is_return(entry) is True


def test_an_entry_with_no_pair_is_not_a_return():
    assert returns.is_return({"category": "spec-scope"}) is False
    assert returns.is_return({"pair": "", "category": "spec-scope"}) is False


# --- finding_origin --------------------------------------------------------------------


def _row(subtask: str, version: int) -> Any:
    return {
        "subtask": subtask,
        "version": version,
        "location": "a.py:1",
        "claim": "c",
        "severity": "important",
        "scope": "subtask",
        "category": "spec-behaviour",
        "status": "open",
    }


def _review(subtask: str, version: int, started_at: str) -> dict[str, Any]:
    return {
        "role": "Tech Lead",
        "section": f"Tech Lead: review {subtask}",
        "version": version,
        "started_at": started_at,
    }


def _state(*runs: dict[str, Any], landing: list[dict[str, Any]] | None = None) -> Any:
    return {"subtasks": {"a": {}, "integration": {}}, "runs": list(runs), "landing": landing or []}


def test_a_row_of_a_subtask_review_is_the_owners():
    state = _state(_review("a", 1, "2026-10-20T10:00:00.000Z"))
    assert returns.finding_origin(_row("a", 1), state) == "owner_review"


def test_a_row_of_the_integration_review_is_the_integrations():
    state = _state(_review("integration", 1, "2026-10-20T11:00:00.000Z"))
    assert returns.finding_origin(_row("integration", 1), state) == "integration_review"


def test_a_row_of_a_review_that_ran_after_the_landing_pass_is_the_landing_re_review():
    state = _state(
        _review("a", 1, "2026-10-20T10:00:00.000Z"),
        _review("a", 2, "2026-10-21T10:00:00.000Z"),
        landing=[{"head": "h", "step": "fix_important", "at": "2026-10-21T09:00:00+00:00"}],
    )
    assert returns.finding_origin(_row("a", 1), state) == "owner_review"
    assert returns.finding_origin(_row("a", 2), state) == "landing_review"


def test_a_pass_before_the_pull_request_does_not_make_the_next_review_a_landing_one():
    state = _state(
        _review("integration", 1, "2026-10-20T10:00:00.000Z"),
        _review("integration", 2, "2026-10-20T12:00:00.000Z"),
        landing=[
            {
                "head": "h",
                "step": "fix_important",
                "at": "2026-10-20T11:00:00+00:00",
                "before_pr": True,
            }
        ],
    )
    assert returns.finding_origin(_row("integration", 2), state) == "integration_review"


def test_a_row_with_no_matching_run_falls_back_to_its_subtask():
    assert returns.finding_origin(_row("a", 9), _state()) == "owner_review"
    assert returns.finding_origin(_row("integration", 9), _state()) == "integration_review"


# --- one place decides it (invariant 1) -------------------------------------------------

_COMPARISON = re.compile(r"""(\[\s*["']by["']\s*\]|\.get\(\s*["']by["'][^)]*\))\s*[!=]=""")


def test_no_module_outside_returns_compares_the_by_key():
    root = Path(returns.__file__).parents[3]  # the `agentic/` package
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in sorted(root.rglob("*.py"))
        if path.name != "returns.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _COMPARISON.search(line)
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "line", ['e.get("by") != "landing"', "x['by'] == 'landing'", 'entry.get("by", "") == "l"']
)
def test_the_scan_pattern_catches_each_spelling(line):
    assert _COMPARISON.search(line)
