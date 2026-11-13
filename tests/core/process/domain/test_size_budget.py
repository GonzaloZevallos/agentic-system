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


# The refusal and prompt figures (GH-691): `max_declarable` is the one inverse of `judged`.

RATIOS = [1.0, 1.2, 1.5, 1.4285714, 2.0, (1.2, 2.0), (1.4285714, 1.3)]
BUDGET_TEXT_BEFORE = (
    "Size budget: a task may declare at most 15 hand-written non-test files and 400 added "
    "hand-written non-test lines. Tests have their own cap of 600 added lines. A path is a "
    "test when a directory of it is one of `tests`, `test`, `__tests__`, `e2e` or its file "
    "name matches `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`. Generated paths never count: "
    "the lockfiles (`uv.lock`, `poetry.lock`, `Pipfile.lock`, `package-lock.json`, "
    "`npm-shrinkwrap.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`, `Cargo.lock`, "
    "`go.sum`, `Gemfile.lock`, `composer.lock`), the stack's `docs.paths` and its "
    "`generated:` globs. Docs, ADRs, README and prompt files are hand-written."
)


@pytest.mark.parametrize("ratio", RATIOS)
def test_max_declarable_is_the_largest_size_that_still_fits(ratio):
    most = budget.max_declarable(ratio)
    assert budget.fits(budget.judged(most, ratio))
    assert most.test_lines == budget.TEST_LINES
    for field in ("hand_files", "hand_lines"):
        more = PlanSize(
            most.hand_files + (field == "hand_files"),
            most.hand_lines + (field == "hand_lines"),
            0,
        )
        assert budget.crossed(budget.judged(more, ratio)) == (field,)


def test_max_declarable_pins_the_unproven_ratio_and_leaves_tests_unscaled():
    assert budget.max_declarable() == PlanSize(10, 266, 600)
    assert budget.max_declarable(budget.UNPROVEN_RATIO) == PlanSize(10, 266, 600)
    for ratio in RATIOS:
        assert budget.max_declarable(ratio).test_lines == 600


def test_over_text_for_a_part_over_hand_lines_names_every_figure():
    text = budget.over_text(PlanSize(3, 270, 10), 1.5, "part")
    assert text.startswith("Do not lower a declaration to fit")
    assert "hand_lines declared 270, judged 405 at x1.5, cap 400" in text
    assert "an honest estimate fits only at 266 or less" in text
    assert "hand_files" not in text and "test_lines" not in text


def test_over_text_for_a_part_over_test_lines_names_every_figure():
    text = budget.over_text(PlanSize(1, 10, 700), 1.5, "part")
    assert "test_lines declared 700, judged 700 (not scaled), cap 600" in text
    assert "fits only at 600 or less" in text
    assert "hand_lines" not in text


def test_over_text_names_every_crossed_field_in_field_order():
    text = budget.over_text(PlanSize(11, 270, 700), 1.5, "part")
    assert text.index("hand_files") < text.index("hand_lines") < text.index("test_lines")


@pytest.mark.parametrize("ratio", [1.4285714, (1.4285714, 1.3)])
def test_over_text_prints_the_integers_of_judged_and_says_about_for_a_long_ratio(ratio):
    size = PlanSize(40, 320, 10)
    judged, most = budget.judged(size, ratio), budget.max_declarable(ratio)
    text = budget.over_text(size, ratio, "part")
    assert f"hand_files declared 40, judged {judged.hand_files} at about x" in text
    assert f"hand_lines declared 320, judged {judged.hand_lines} at " in text
    assert f"fits only at {most.hand_files} or less" in text
    assert f"fits only at {most.hand_lines} or less" in text
    assert "about x1.43" in text


def test_over_text_shows_a_pair_by_each_halfs_own_ratio():
    text = budget.over_text(PlanSize(20, 300, 0), (1.2, 2.0), "part")
    assert "hand_files declared 20, judged 24 at x1.2, cap 15" in text
    assert "hand_lines declared 300, judged 600 at x2, cap 400" in text


def test_over_text_for_a_plan_gives_the_cap_and_a_ceiling_per_part():
    text = budget.over_text(PlanSize(3, 270, 700), 1.5, "plan")
    assert "hand_lines judged 405 (declared 270 at x1.5), cap 400" in text
    assert "test_lines judged 700 (declared 700, not scaled), cap 600" in text
    assert "write parts" in text and "size each part honestly" in text
    assert "no more than 266 declared hand-written lines" in text
    assert "and 600 test lines" in text
    assert "fits only at" not in text
    assert "lower" not in text


def test_budget_text_without_a_ratio_is_byte_for_byte_what_it_was():
    assert budget.budget_text(None) == BUDGET_TEXT_BEFORE


@pytest.mark.parametrize("ratio", [budget.UNPROVEN_RATIO, 1.2, (1.2, 2.0)])
def test_budget_text_with_a_ratio_appends_one_sentence_with_the_max_declarable(ratio):
    text, most = budget.budget_text(ratio), budget.max_declarable(ratio)
    before = budget.budget_text(None)
    assert text.startswith(before)
    assert text.endswith(
        f"At this factor the most you can declare and still fit is {most.hand_files} "
        f"hand-written files and {most.hand_lines} hand-written lines; tests are not scaled, "
        f"so up to {budget.TEST_LINES} test lines."
    )
    assert "judged x" in text
