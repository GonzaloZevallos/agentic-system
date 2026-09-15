import pytest
import yaml

from agentic.core.knowledge.domain import checks


def test_declared_keeps_each_check_by_name_in_order():
    data = {"tests": "uv run --no-project pytest", "lint": "ruff check ."}
    assert list(checks.declared(data, "x").items()) == list(data.items())


@pytest.mark.parametrize(
    "data", [None, {}, ["pytest"], "pytest", {"tests": ""}, {"tests": 3}, {"tests": ["pytest"]}]
)
def test_anything_but_names_and_commands_is_an_error(data):
    with pytest.raises(ValueError, match="checks.yaml: expected"):
        checks.declared(data, "checks.yaml")


def test_the_default_is_one_check_that_runs_the_tests():
    default = checks.declared(yaml.safe_load(checks.DEFAULT.read_text()), checks.DEFAULT)
    assert default == {"tests": "pytest"}
