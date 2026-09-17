import pytest
import yaml

from agentic.core.knowledge.domain import stack


def test_checks_keep_each_command_by_name_in_order():
    declared = {"tests": "uv run --no-project pytest", "lint": "ruff check ."}
    assert list(stack.checks({"checks": declared}, "x").items()) == list(declared.items())


def test_setup_keeps_each_command_by_name_in_order():
    declared = {"venv": "uv venv", "deps": "uv pip install -e '.[dev]'"}
    assert list(stack.setup({"setup": declared, "checks": {"t": "pytest"}}, "x").items()) == list(
        declared.items()
    )


def test_a_product_that_declares_no_setup_prepares_nothing():
    assert stack.setup({"checks": {"tests": "pytest"}}, "x") == {}


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        ["pytest"],
        {"checks": None},
        {"checks": {}},
        {"checks": {"tests": ""}},
        {"checks": {"tests": 3}},
        {"checks": {"tests": ["pytest"]}},
    ],
)
def test_anything_but_names_and_commands_is_an_error(data):
    with pytest.raises(ValueError, match="stack.yaml: expected"):
        stack.checks(data, "stack.yaml")


@pytest.mark.parametrize(
    "data",
    [
        {"checks": {"tests": "pytest"}, "setup": None},
        {"checks": {"tests": "pytest"}, "setup": {}},
        {"checks": {"tests": "pytest"}, "setup": "uv venv"},
        {"checks": {"tests": "pytest"}, "setup": {"venv": ""}},
        {"checks": {"tests": "pytest"}, "setup": {"venv": ["uv venv"]}},
    ],
)
def test_a_setup_that_is_there_but_says_nothing_is_an_error(data):
    # Absent is a product that prepares nothing; present and empty is a half-written file.
    with pytest.raises(ValueError, match="stack.yaml: expected"):
        stack.setup(data, "stack.yaml")


def test_a_key_the_file_does_not_know_is_a_typo_not_an_extension():
    # Without this, `check:` or `setpu:` would run with the default and say nothing.
    with pytest.raises(ValueError, match="unknown key"):
        stack.checks({"checks": {"tests": "pytest"}, "setpu": {"venv": "uv venv"}}, "stack.yaml")


def test_the_unknown_key_is_caught_reading_the_setup_too():
    with pytest.raises(ValueError, match="unknown key"):
        stack.setup({"checks": {"tests": "pytest"}, "chekcs": {"t": "x"}}, "stack.yaml")


def test_the_product_says_the_name_every_run_of_it_is_labelled_with():
    assert stack.product({"product": "gym-saas", "checks": {"tests": "pytest"}}, "x") == "gym-saas"


@pytest.mark.parametrize(
    "name", [None, "", "  ", "Gym SaaS", "gym_saas", "-gym", "gym--saas", ["gym"]]
)
def test_a_name_that_is_not_a_slug_is_an_error(name):
    """The name is a Prometheus label value and a dashboard variable: one product with
    two spellings is two products in every panel (issue #47)."""
    declared = {"checks": {"tests": "pytest"}}
    if name is not None:
        declared["product"] = name
    with pytest.raises(ValueError, match="expected `product`"):
        stack.product(declared, "stack.yaml")


def test_the_name_is_trimmed_the_way_a_yaml_line_leaves_it():
    assert stack.product({"product": " gym-saas \n", "checks": {"t": "x"}}, "x") == "gym-saas"


def test_a_product_with_no_name_is_told_what_the_key_is_for():
    with pytest.raises(ValueError, match="never add up"):
        stack.product({"checks": {"tests": "pytest"}}, "stack.yaml")


def test_the_template_is_a_file_a_product_can_copy_as_it_is():
    data = yaml.safe_load(stack.TEMPLATE.read_text())
    assert stack.checks(data, stack.TEMPLATE) == {"tests": "pytest"}
    assert stack.setup(data, stack.TEMPLATE) == {}
    # A placeholder, and a valid one: the human renames it, and a copy that is not
    # renamed still runs rather than dying on a key it was never told about.
    assert stack.product(data, stack.TEMPLATE) == "my-product"


def test_a_product_that_declares_no_stack_is_told_what_to_write():
    error = stack.not_declared("abc123:.agentic/stack.yaml")
    assert isinstance(error, ValueError)
    # The template travels in the message: the human has nothing else to copy from.
    assert "checks:" in str(error) and str(stack.OVERLAY) in str(error)
