"""Which names a check run can carry, read from a product's workflows (GH-485). Pure: the
texts come in, a verdict on `ci_required` names comes out. Exact matching is not touched:
a matrix job is still not its id, so the fix is in the names the plan asks for."""

from agentic.core.process.domain import ci_workflow

CI = """
name: CI
on: [push]
jobs:
  checks:
    runs-on: ubuntu-latest
    steps: [{run: make}]
  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps: [{run: make lint}]
  e2e:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        shard: [1, 2, 3, 4]
    steps: [{run: make e2e}]
  ci-ok:
    needs: [checks, e2e]
    if: always()
    runs-on: ubuntu-latest
    steps: [{run: "true"}]
"""


def _names(text: str = CI, path: str = ".github/workflows/ci.yml"):
    found = ci_workflow.check_run_names({path: text})
    assert found is not None
    return found


def test_a_plain_job_is_carried_under_its_id_and_a_named_one_under_its_name():
    names = _names()
    assert {"checks", "ci-ok", "Lint"} <= names.carriable
    assert "lint" not in names.carriable


def test_a_matrix_job_id_is_a_wrong_id_that_lists_the_jobs_needing_it():
    wrong = _names().wrong_ids["e2e"]
    assert wrong.reason == "matrix"
    assert wrong.needed_by == ("ci-ok",)
    assert wrong.file == ".github/workflows/ci.yml"
    assert "e2e" not in _names().carriable


def test_a_job_whose_name_differs_from_its_id_is_a_wrong_id_that_gives_the_name():
    wrong = _names().wrong_ids["lint"]
    assert (wrong.reason, wrong.literal_name) == ("named", "Lint")


def test_a_name_that_equals_the_id_is_carried_and_no_wrong_id():
    names = _names("jobs:\n  web:\n    name: web\n    steps: []\n")
    assert names.carriable == {"web"} and not names.wrong_ids


def test_a_reusable_workflow_or_a_name_expression_makes_the_workflows_undecidable():
    reusable = _names("jobs:\n  call:\n    uses: ./.github/workflows/other.yml\n")
    expression = _names("jobs:\n  a:\n    name: ${{ inputs.label }}\n    steps: []\n")
    assert reusable.undecidable and "call" not in reusable.wrong_ids
    assert expression.undecidable and "a" not in expression.wrong_ids


def test_unreadable_yaml_or_no_jobs_answers_none_never_an_exception():
    assert ci_workflow.check_run_names({"a.yml": "jobs: [unclosed"}) is None
    assert ci_workflow.check_run_names({"a.yml": "name: x\n"}) is None
    assert ci_workflow.check_run_names({}) is None


def test_several_workflows_are_read_together_and_each_job_keeps_its_file():
    other = "jobs:\n  web:\n    steps: []\n"
    found = ci_workflow.check_run_names({".github/workflows/ci.yml": CI, "w.yml": other})
    assert found is not None
    assert "web" in found.carriable and "checks" in found.carriable
    assert found.wrong_ids["e2e"].file == ".github/workflows/ci.yml"
