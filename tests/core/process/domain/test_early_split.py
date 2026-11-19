"""The early size check (GH-999, part 2): the verdict on a declared size, the predicate that says
whether the check is still due, the only reader of its record, and the record's reducer."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import pytest

from agentic.core.process.domain import document, early_split, rules, size_budget, task
from agentic.core.process.domain.early_split import Verdict
from agentic.core.process.domain.roles import (
    PM_CONSOLIDATOR,
    TECH_LEAD,
    TECH_WRITER,
    UI_DESIGNER,
    UI_LEAD,
    UX_DESIGNER,
    UX_LEAD,
)
from agentic.core.process.domain.size_budget import PlanSize
from agentic.core.process.domain.task import EarlySplitRow

FILES, LINES, TESTS = size_budget.HAND_FILES, size_budget.HAND_LINES, size_budget.TEST_LINES
FACTOR = size_budget.EARLY_SPLIT_FACTOR

FITS = PlanSize(FILES, LINES, TESTS)
CLOSE = PlanSize(FILES + 1, LINES, TESTS)
CLEARLY_OVER = PlanSize(FILES * FACTOR + 1, LINES, TESTS)

FIXTURES = Path(__file__).parent / "fixtures" / "early_split"


def row(
    cause: str, verdict: Literal["split", "go"] = "go", at: str = "2026-11-19T10:00:00Z"
) -> EarlySplitRow:
    return {"verdict": verdict, "cause": cause, "at": at}


@pytest.mark.parametrize(
    ("size", "parts_ok", "expected"),
    [
        (None, True, Verdict("go", "sizer_failed")),
        (None, False, Verdict("go", "sizer_failed")),
        (PlanSize(-1, 1, 1), True, Verdict("go", "sizer_failed")),
        (PlanSize(1, -1, 1), True, Verdict("go", "sizer_failed")),
        (PlanSize(1, 1, -1), True, Verdict("go", "sizer_failed")),
        (FITS, True, Verdict("go", "fits")),
        (FITS, False, Verdict("go", "fits")),
        (CLOSE, True, Verdict("go", "close")),
        (CLOSE, False, Verdict("go", "close")),
        (PlanSize(1, LINES * FACTOR, 1), True, Verdict("go", "close")),
        (PlanSize(1, 1, TESTS * FACTOR), False, Verdict("go", "close")),
        (CLEARLY_OVER, True, Verdict("split", "over")),
        (PlanSize(1, LINES * FACTOR + 1, 1), True, Verdict("split", "over")),
        (PlanSize(1, 1, TESTS * FACTOR + 1), True, Verdict("split", "over")),
        (CLEARLY_OVER, False, Verdict("go", "parts_refused")),
    ],
)
def test_verdict_table(size, parts_ok, expected):
    assert early_split.verdict(size, parts_ok) == expected


def test_measured_is_false_with_no_row_no_key_or_one_sizer_failure():
    assert early_split.measured({}) is False
    assert early_split.measured({"early_split": []}) is False
    assert early_split.measured({"early_split": [row("sizer_failed")]}) is False


def test_measured_is_true_after_two_sizer_failures():
    rows = [row("sizer_failed"), row("sizer_failed", at="b")]
    assert early_split.measured({"early_split": rows}) is True


@pytest.mark.parametrize("cause", ["over", "fits", "close", "parts_refused"])
def test_measured_is_true_for_each_closing_cause_alone(cause):
    verdict = "split" if cause == "over" else "go"
    assert early_split.measured({"early_split": [row(cause, verdict)]}) is True


def test_a_closing_row_after_one_failure_measures():
    assert early_split.measured({"early_split": [row("sizer_failed"), row("fits")]}) is True


def test_measured_reads_only_the_cause():
    plain: Any = {"early_split": [row("close")]}
    figures: Any = {"early_split": [{**row("close"), "hand_files": 99, "estimated_cost_usd": 9.0}]}
    assert early_split.measured(plain) is early_split.measured(figures) is True


def test_add_early_split_appends_to_nothing_and_ignores_an_empty_new():
    first = row("sizer_failed")
    assert task.add_early_split([], [first]) == [first]
    assert task.add_early_split([first], []) == [first]
    assert task.add_early_split([], []) == []


def test_add_early_split_skips_an_exact_duplicate_but_not_a_different_row():
    first = row("sizer_failed")
    assert task.add_early_split([first], [first.copy()]) == [first]
    later = row("sizer_failed", at="2026-11-19T11:00:00Z")
    assert task.add_early_split([first], [later]) == [first, later]


def test_add_early_split_keeps_the_oldest_three_and_measured_does_not_change():
    rows = [row("sizer_failed", at="a"), row("sizer_failed", at="b"), row("sizer_failed", at="c")]
    capped = task.add_early_split(rows, [row("fits", at="d")])
    assert capped == rows
    assert len(capped) == task.EARLY_SPLIT_ROWS_MAX
    before: Any = {"early_split": rows}
    after: Any = {"early_split": capped}
    assert early_split.measured(before) is early_split.measured(after) is True


def test_the_channel_folds_through_apply_and_delta_gives_back_the_new_row():
    first, second = row("sizer_failed", at="a"), row("fits", at="b")
    before: Any = {"early_split": [first]}
    after = task.apply(before, {"early_split": [second]})
    assert after["early_split"] == [first, second]
    assert task.delta(before, after) == {"early_split": [second]}


def feature(**extra: Any) -> Any:
    """A full-path feature whose definition is done and nothing else: the one state the check
    applies to."""
    return {
        "type": "feature",
        "path": "full",
        "description": "Export the report.",
        "runs": [{"role": "PM", "section": "PM", "version": 1}],
        **extra,
    }


def test_applies_to_a_full_path_feature_with_only_the_definition_done():
    assert early_split.applies(feature()) is True


def test_applies_when_the_path_is_absent_which_reads_as_full():
    state = feature()
    del state["path"]
    assert early_split.applies(state) is True


def test_applies_when_a_run_has_no_section_and_is_not_a_plan_role():
    assert early_split.applies(feature(runs=[{"role": "PM", "version": 1}])) is True


@pytest.mark.parametrize(
    "extra",
    [
        {"type": "bug"},
        {"type": "chore"},
        {"path": "light"},
        {"spec": {"contract": "x"}},
        {"description": rules.split_marker("GH-999", 2, 1) + "\nBody."},
        {"promotion": {"from_role": "PM", "kind": "x", "text": None, "at": "t"}},
        {"evaluation": {"issue": 7, "base": "a" * 40, "ci": "local"}},
        {"plan_route": {"route": "single"}},
        {"estimate": {"cost_usd": 3.0, "files": 4}},
        {"planned_size": PlanSize(1, 1, 1)},
        {"subtasks": {"a": {"status": "pending"}}},
        {"parts": [{"title": "t", "body": "b", "depends_on": []}]},
        {"plan_due": True},
        {"fit_question": True},
        {"early_split": [row("fits")]},
        {"early_split": [row("sizer_failed"), row("sizer_failed", at="b")]},
        {"runs": [{"role": "Tech Lead", "section": TECH_LEAD.name, "version": 1}]},
        {"runs": [{"role": "Tech Lead efímero", "section": rules.SOLUTION_INTERVIEW}]},
        {"runs": [{"role": rules.PLAN_SIZER.name, "version": 1}]},
        {"runs": [{"role": "Tech Lead", "section": rules.TECH_LEAD_CRITIC.name}]},
    ],
    ids=lambda extra: "-".join(sorted(extra)) + "-" + str(list(extra.values())[0])[:24],
)
def test_each_exclusion_alone_stops_the_check(extra):
    assert early_split.applies(feature(**extra)) is False


@pytest.mark.parametrize(
    "extra",
    [
        {"promotion": {}},
        {"plan_due": False, "fit_question": False},
        {"parts": [], "subtasks": {}},
        {"early_split": [row("sizer_failed")]},
        {"early_split": []},
    ],
    ids=["empty_promotion", "plan_flags_false", "empty_plan_lists", "one_failure", "no_rows"],
)
def test_an_empty_or_unfinished_record_does_not_stop_the_check(extra):
    assert early_split.applies(feature(**extra)) is True


@pytest.mark.parametrize(
    "role",
    [UX_DESIGNER, UX_LEAD, UI_DESIGNER, UI_LEAD, PM_CONSOLIDATOR, TECH_WRITER],
    ids=lambda role: role.slug,
)
def test_a_started_design_ladder_stops_the_check_by_section_or_by_role(role):
    by_section = feature(runs=[{"role": "x", "section": role.name, "version": 1}])
    by_role = feature(runs=[{"role": role.name, "version": 1}])
    assert early_split.applies(by_section) is False
    assert early_split.applies(by_role) is False


def drop_keys(*keys: str) -> Callable[[dict[str, Any]], None]:
    def drop(state: dict[str, Any]) -> None:
        for key in keys:
            state.pop(key, None)

    return drop


def drop_interview_run(state: dict[str, Any]) -> None:
    state["runs"] = [r for r in state["runs"] if r["section"] != rules.SOLUTION_INTERVIEW]


# Each pre-change document, and what removing its marker takes out of it: the point of the plan
# it stands for. A paused solution interview, an estimate escalation, a plan that drew
# departures, a fit question, parts half opened.
DOCUMENTS = {
    "paused_interview": drop_interview_run,
    "estimate_escalation": drop_keys("estimate"),
    "plan_departures": drop_keys("plan_due"),
    "fit_question": drop_keys("fit_question"),
    "half_opened_parts": drop_keys("parts"),
}


def load(name: str) -> dict[str, Any]:
    """The fixture through the real document parser, as the graph would load it."""
    return document.parse(f"---\n{(FIXTURES / f'{name}.yaml').read_text()}---\n\n# {name}\n")


@pytest.mark.parametrize("name", DOCUMENTS)
def test_a_document_written_before_the_check_gives_go_at_each_point_of_the_plan(name):
    state: Any = load(name)
    assert "early_split" not in state
    assert early_split.applies(state) is False


@pytest.mark.parametrize("name", DOCUMENTS)
def test_the_same_document_without_its_marker_is_still_due(name):
    state: Any = load(name)
    DOCUMENTS[name](state)
    assert early_split.applies(state) is True
