"""Which names a check run can carry, read from a product's workflows (GH-485). Pure: the
texts come in, a verdict on `ci_required` names comes out. A matrix job's id names all its
expansions (GH-535, `ci.matches`); only the id of a job with another literal `name:` is refused."""

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


def test_a_matrix_job_id_is_carried_and_no_wrong_id():
    names = _names()
    assert "e2e" in names.carriable
    assert "e2e" not in names.wrong_ids


def test_a_matrix_job_with_a_literal_name_is_carried_under_the_name_and_its_id_is_refused():
    text = "jobs:\n  e2e:\n    name: End to end\n    strategy:\n      matrix:\n        n: [1]\n"
    names = _names(text)
    assert "End to end" in names.carriable and "e2e" not in names.carriable
    assert (names.wrong_ids["e2e"].reason, names.wrong_ids["e2e"].literal_name) == (
        "named",
        "End to end",
    )


def test_refusals_accept_a_matrix_id_and_refuse_a_named_id_and_an_unknown_name():
    names = _names()
    reasons = ci_workflow.refusals(["e2e", "lint", "pytest"], names, (), frozenset())
    assert len(reasons) == 2
    assert "`lint`" in reasons[0] and "named `Lint`" in reasons[0]
    assert "`pytest`" in reasons[1] and "`e2e`" in reasons[1]  # the known names list it


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
    assert found.wrong_ids["lint"].file == ".github/workflows/ci.yml"
