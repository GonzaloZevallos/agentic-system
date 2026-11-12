"""The size budget (GH-592): the closed classification of a path, the measure of a delivery,
the caps, the divergence, the frontmatter record and the paragraph the planner reads."""

import pytest

from agentic.core.process.domain import size_budget as budget
from agentic.core.process.domain.size_budget import ActualSize, NumstatRow, PlanSize

GLOBS = ("gen/arch/**", "gen/reference/**", "web/gen/*.ts")


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("src/pkg/rules.py", "hand"),
        ("handbook/README.md", "hand"),
        ("handbook/adr/0100-size.md", "hand"),
        ("pkg/prompts/planner.md", "hand"),
        ("tests/core/test_rules.py", "test"),
        ("web/__tests__/page.tsx", "test"),
        ("apps/x/e2e/flow.ts", "test"),
        ("src/test/Main.java", "test"),
        ("pkg/test_helpers.py", "test"),
        ("pkg/widget_test.go", "test"),
        ("web/button.test.tsx", "test"),
        ("web/button.spec.ts", "test"),
        ("pkg/contest.py", "hand"),
        ("pkg/latest_notes.py", "hand"),
        ("svc/uv.lock", "generated"),
        ("web/package-lock.json", "generated"),
        ("services/api/go.sum", "generated"),
        ("gen/reference/stack.md", "generated"),
        ("gen/arch/map.md", "generated"),
        ("web/gen/api.ts", "generated"),
        ("web/gen/deep/api.ts", "hand"),
    ],
)
def test_classification_table(path, kind):
    assert budget.classify(path, GLOBS) == kind


def test_a_lockfile_is_generated_with_no_glob_declared():
    assert all(budget.classify(f"x/{name}", ()) == "generated" for name in budget.LOCKFILES)


def test_a_generated_glob_wins_over_a_test_path():
    assert budget.classify("tests/golden/out.txt", ("tests/golden/**",)) == "generated"


def test_measure_splits_hand_test_and_generated_and_counts_added_lines_only():
    rows = (
        NumstatRow("src/a.py", 30, 10),
        NumstatRow("src/b.py", 5, 0),
        NumstatRow("tests/test_a.py", 50, 2),
        NumstatRow("svc/uv.lock", 900, 800),
        NumstatRow("gen/reference/x.md", 40, 40),
    )
    assert budget.measure(rows, GLOBS) == ActualSize(2, 35, 50, 2)


def test_a_row_with_no_line_is_free_but_a_generated_one_is_still_reported():
    rows = (
        NumstatRow("moved/a.py", 0, 0),
        NumstatRow("img.png", 0, 0),
        NumstatRow("svc/uv.lock", 0, 0),
    )
    assert budget.measure(rows, ()) == ActualSize(0, 0, 0, 1)


def test_a_deletion_only_file_counts_as_a_file_with_no_added_lines():
    assert budget.measure((NumstatRow("a.py", 0, 12),), ()) == ActualSize(1, 0, 0, 0)


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (PlanSize(15, 400, 600), True),
        (PlanSize(16, 400, 600), False),
        (PlanSize(15, 401, 600), False),
        (PlanSize(15, 400, 601), False),
        (PlanSize(0, 0, 0), True),
    ],
)
def test_fits_at_and_over_each_cap(size, expected):
    assert budget.fits(size) is expected


def test_divergence_is_actual_over_planned_per_field_and_none_where_planned_is_zero():
    got = budget.divergence(PlanSize(10, 200, 0), ActualSize(15, 100, 40, 3))
    assert (got.hand_files, got.hand_lines, got.test_lines) == (1.5, 0.5, None)


def test_the_planned_record_round_trips_through_the_frontmatter():
    size = PlanSize(3, 120, 200)
    frontmatter = {"title": "x", **size.to_frontmatter()}
    assert list(size.to_frontmatter()) == [budget.PLANNED_KEY] == ["planned_size"]
    assert PlanSize.from_frontmatter(frontmatter) == size


def test_the_actual_record_round_trips_through_the_frontmatter():
    size = ActualSize(3, 120, 200, 7)
    assert list(size.to_frontmatter()) == [budget.ACTUAL_KEY] == ["actual_size"]
    assert ActualSize.from_frontmatter(size.to_frontmatter()) == size


@pytest.mark.parametrize(
    "value",
    [
        None,
        "3",
        [],
        {"hand_files": 1},
        {"hand_files": -1, "hand_lines": 1, "test_lines": 1},
        {"hand_files": True, "hand_lines": 1, "test_lines": 1},
        {"hand_files": "1", "hand_lines": 1, "test_lines": 1},
    ],
)
def test_a_missing_or_malformed_record_reads_as_none(value):
    assert PlanSize.from_frontmatter({"planned_size": value}) is None
    assert PlanSize.from_frontmatter({}) is None
    assert ActualSize.from_frontmatter({"actual_size": value}) is None


def test_budget_text_states_every_constant_and_the_excluded_paths():
    text = budget.budget_text(None)
    for number in (budget.HAND_FILES, budget.HAND_LINES, budget.TEST_LINES):
        assert str(number) in text
    for name in budget.LOCKFILES:
        assert name in text
    assert "docs.paths" in text and "generated:" in text
    assert "judged" not in text


def test_budget_text_with_a_ratio_says_how_the_declared_figures_are_judged():
    assert "judged x 1.5, rounded up" in budget.budget_text(budget.UNPROVEN_RATIO)
    assert "judged x 1.2" in budget.budget_text(1.2)


def test_a_pair_of_ratios_judges_files_and_lines_each_by_its_own():
    judged = budget.judged(PlanSize(10, 100, 7), (1.2, 2.0))
    assert (judged.hand_files, judged.hand_lines, judged.test_lines) == (12, 200, 7)
    assert budget.judged(PlanSize(10, 100, 7), 1.5) == budget.judged(
        PlanSize(10, 100, 7), (1.5, 1.5)
    )


def test_budget_text_with_a_pair_of_ratios_names_each():
    assert "judged x 1.2 files and x 2 lines, rounded up" in budget.budget_text((1.2, 2.0))
