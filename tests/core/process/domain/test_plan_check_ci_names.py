"""The plan check refuses a `ci_required` name the base's workflows prove no check run
carries (GH-485): the apostrophe GH-543 plan asked for the matrix jobs `pytest` and `e2e`
and the full tier waited out its timeout on a green CI. The refusal names the job and the
names to use instead; it never drops the name, and it refuses only what it can prove."""

import pytest

from agentic.core.process.domain import ci_workflow, cost_history, schemas

WORKFLOW = ".github/workflows/ci.yml"
CI = """
jobs:
  checks:
    steps: []
  lint:
    name: Lint
    steps: []
  e2e:
    strategy:
      matrix:
        shard: [1, 2]
    steps: []
  ci-ok:
    needs: [checks, e2e]
    steps: []
"""
NO_AGGREGATOR = """
jobs:
  checks:
    steps: []
  e2e:
    strategy:
      matrix:
        shard: [1, 2]
    steps: []
"""


def _names(text: str):
    return ci_workflow.check_run_names({WORKFLOW: text})


def _plan(required, *, paths=(), brief="b"):
    return {
        "subtasks": [{"id": "a", "title": "a", "brief": brief, "seams": ["x"], "depends_on": []}],
        "markdown": "### Solution",
        "ci_required": list(required),
        "ci_none_reason": "" if required else "The issue requires no external check.",
        "required_paths": list(paths),
        "estimated_cost_usd": 20.0,
        "estimated_files": 3,
    }


def _check(text: str | None, stack_ci=()):
    history = cost_history.build([])
    workflow = None if text is None else _names(text)
    return schemas.plan_check_for(history, 0.0, workflow=workflow, stack_ci=tuple(stack_ci))


def test_a_matrix_job_id_is_refused_naming_the_job_and_the_names_to_use():
    with pytest.raises(ValueError) as refusal:
        _check(CI, stack_ci=("pytest-ok",))(_plan(["e2e"]))
    text = str(refusal.value)
    assert "`e2e`" in text and "matrix" in text and "e2e (<matrix values>)" in text
    assert "`ci-ok`" in text and "`pytest-ok`" in text
    assert "does not know whether any of them covers the criterion" in text


def test_a_matrix_job_nobody_needs_says_there_is_no_candidate_and_to_add_an_aggregator():
    with pytest.raises(ValueError) as refusal:
        _check(NO_AGGREGATOR)(_plan(["e2e"]))
    text = str(refusal.value)
    assert "No job `needs: e2e`" in text and "aggregator" in text and "required_paths" in text


def test_a_job_required_by_its_id_when_a_name_differs_points_to_the_name():
    with pytest.raises(ValueError, match="named `Lint`.*require `Lint`"):
        _check(CI)(_plan(["lint"]))


def test_every_wrong_name_is_listed_in_one_refusal():
    with pytest.raises(ValueError) as refusal:
        _check(CI)(_plan(["checks", "e2e", "lint"]))
    text = str(refusal.value)
    assert "`e2e`" in text and "`lint`" in text and "ci_required `checks`" not in text


def test_a_name_no_job_of_the_workflows_can_carry_is_refused_with_the_names_that_can():
    with pytest.raises(ValueError) as refusal:
        _check(CI)(_plan(["pytest"]))
    text = str(refusal.value)
    assert "`pytest`" in text and "`ci-ok`" in text and "`Lint`" in text


def test_names_a_check_run_carries_pass():
    _check(CI)(_plan(["checks", "ci-ok", "Lint"]))


def test_a_stack_ci_name_passes_even_when_no_workflow_job_shows_it():
    _check(CI, stack_ci=("merge-gate",))(_plan(["merge-gate"]))


@pytest.mark.parametrize("required", [["e2e"], ["ci-ok-new"], ["e2e", "ci-new"]])
def test_a_plan_that_declares_the_workflow_may_require_the_job_it_changes(required):
    _check(CI)(_plan(required, paths=[WORKFLOW]))


def test_a_subtask_that_names_the_workflow_file_counts_as_touching_it():
    _check(CI)(_plan(["e2e"], brief=f"Add the aggregator job to {WORKFLOW}."))


def test_a_workflow_other_than_the_one_with_the_job_does_not_excuse_a_matrix_id():
    with pytest.raises(ValueError, match="matrix"):
        _check(CI)(_plan(["e2e"], paths=[".github/workflows/release.yml"]))


def test_a_reusable_workflow_or_a_name_expression_lets_an_unknown_name_pass():
    reusable = CI + "  call:\n    uses: ./.github/workflows/other.yml\n"
    expression = CI + "  built:\n    name: ${{ inputs.x }}\n    steps: []\n"
    _check(reusable)(_plan(["pytest"]))
    _check(expression)(_plan(["pytest"]))


def test_unreadable_yaml_or_no_workflow_read_passes():
    _check("jobs: [unclosed")(_plan(["e2e"]))
    _check(None)(_plan(["e2e"]))


def test_a_plan_with_no_ci_required_is_untouched_by_the_names():
    _check(CI)(_plan([]))
