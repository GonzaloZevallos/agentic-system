"""GH-407 of apostrophe, the plan that grew to 263 files, run through the size budget as a
fixture. It proves the classification and the check on real paths, not that the model slices
well: the partition in the fixture was written for the test."""

from pathlib import Path

import pytest
import yaml

from agentic.core.process.domain import cost_history, size_budget
from agentic.core.process.domain.schemas import parts_check, plan_check_for

FIXTURE = yaml.safe_load(
    (Path(__file__).parents[3] / "fixtures" / "split" / "gh407_plan.yaml").read_text()
)
GENERATED = [*FIXTURE["stack"]["docs_paths"], *FIXTURE["stack"]["generated"]]
PATHS = [path for paths in FIXTURE["subtasks"].values() for path in paths]


def kinds(paths):
    counts = {"hand": 0, "test": 0, "generated": 0}
    for path in paths:
        counts[size_budget.classify(path, GENERATED)] += 1
    return counts


def test_the_listed_paths_classify_into_pinned_counts():
    assert kinds(PATHS) == {"hand": 34, "test": 6, "generated": 4}
    assert kinds(FIXTURE["subtasks"]["integration"]) == {"hand": 1, "test": 1, "generated": 3}


def test_a_generated_path_only_counts_when_the_stack_declares_it():
    assert size_budget.classify("docs/api/openapi.json", []) == "hand"
    assert size_budget.classify("docs/api/openapi.json", GENERATED) == "generated"
    assert size_budget.classify("web/package-lock.json", []) == "generated"


def test_the_gh407_figures_as_a_depth_one_part_are_refused_and_told_to_write_parts():
    declared = FIXTURE["declared"]
    counts = kinds(PATHS)
    data = {
        "estimated_cost_usd": declared["estimated_cost_usd"],
        "estimated_files": declared["estimated_files"],
        "hand_files": counts["hand"],
        "hand_lines": declared["hand_lines"],
        "test_lines": 0,
        "subtasks": [{"id": "all", "title": "All", "brief": "b", "seams": ["x"], "depends_on": []}],
        "markdown": "### Solution",
    }
    check = plan_check_for(cost_history.build([]), 0.0, part_of=("GH-383", 24, 1))
    with pytest.raises(ValueError, match=r"does not fit one task.*write parts"):
        check(data)


def _with_reasons(parts):
    """The fixture is byte-identical to what GH-407 planned, before parts carried a reason per
    edge: these reasons were written after the fact for GH-837."""
    return [
        {
            **part,
            "depends_on_reasons": [f"needs what part {dep} creates" for dep in part["depends_on"]],
        }
        for part in parts
    ]


def test_the_fixture_partition_without_reasons_is_refused_for_missing_reasons():
    with pytest.raises(ValueError, match=r"part \d+ has \d+ depends_on and 0 depends_on_reasons"):
        parts_check(FIXTURE["parts"])


def test_the_fixture_partition_passes_the_parts_check():
    parts = _with_reasons(FIXTURE["parts"])
    parts_check(parts)

    for part in parts:
        size = size_budget.PlanSize(part["hand_files"], part["hand_lines"], part["test_lines"])
        assert size_budget.fits(size_budget.judged(size))
    migrations = [n for n, part in enumerate(parts, 1) if part["kind"] == "migration"]
    assert migrations == [4] and parts[3]["depends_on"] == [3]
    assert size_budget.fits(size_budget.PlanSize(1, 60, 100))


def test_the_partition_without_its_conversion_neighbour_is_refused():
    parts = _with_reasons(FIXTURE["parts"])
    parts[3] = {**parts[3], "depends_on": [], "depends_on_reasons": []}
    parts[4] = {**parts[4], "depends_on": [2], "depends_on_reasons": ["needs part 2"]}
    parts[5] = {**parts[5], "depends_on": [1, 3, 5], "depends_on_reasons": ["a", "b", "c"]}
    with pytest.raises(ValueError, match="part 4 is a migration with no neighbour"):
        parts_check(parts)
