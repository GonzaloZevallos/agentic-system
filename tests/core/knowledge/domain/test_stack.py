import pytest
import yaml

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain.design import Design
from agentic.core.process.domain.suppressions import Forbidden


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


def test_a_product_that_declares_no_forbidden_rules_forbids_nothing():
    # Absent, like `setup`: a product that genuinely forbids nothing is legitimate.
    declared = {"checks": {"tests": "pytest"}}
    assert stack.forbidden(declared, "x") == ()


def test_forbidden_builds_a_record_per_rule_in_file_order():
    declared = {
        "checks": {"tests": "pytest"},
        "forbidden": [
            {"pattern": r"SUPPRESS\b", "extensions": [".src"], "reason": "first"},
            {"pattern": r"SKIP\b", "extensions": [".src", ".alt"], "reason": "second"},
        ],
    }
    rules = stack.forbidden(declared, "x")
    assert [r.reason for r in rules] == ["first", "second"]
    assert isinstance(rules[0], Forbidden)
    assert rules[0].pattern.search("a SUPPRESS b")
    assert rules[1].extensions == frozenset({".src", ".alt"})


@pytest.mark.parametrize(
    "declared",
    [
        {"checks": {"tests": "pytest"}, "forbidden": None},
        {"checks": {"tests": "pytest"}, "forbidden": {}},
        {"checks": {"tests": "pytest"}, "forbidden": []},
        {
            "checks": {"tests": "pytest"},
            "forbidden": [{"pattern": "x", "extensions": [], "reason": "x"}],
        },
        {
            "checks": {"tests": "pytest"},
            "forbidden": [{"pattern": "x", "extensions": [""], "reason": "x"}],
        },
        {
            "checks": {"tests": "pytest"},
            "forbidden": [{"pattern": "x", "extensions": [".src"], "reason": ""}],
        },
        {
            "checks": {"tests": "pytest"},
            "forbidden": [{"pattern": "", "extensions": [".src"], "reason": "x"}],
        },
        {"checks": {"tests": "pytest"}, "forbidden": [{"extensions": [".src"], "reason": "x"}]},
        {"checks": {"tests": "pytest"}, "forbidden": ["not a mapping"]},
    ],
)
def test_a_broken_forbidden_declaration_raises_at_load(declared):
    with pytest.raises(ValueError, match="stack.yaml: expected"):
        stack.forbidden(declared, "stack.yaml")


def test_a_pattern_that_does_not_compile_raises_at_load():
    declared = {
        "checks": {"tests": "pytest"},
        "forbidden": [{"pattern": "(", "extensions": [".src"], "reason": "x"}],
    }
    with pytest.raises(ValueError, match="does not compile"):
        stack.forbidden(declared, "stack.yaml")


def test_the_template_declares_no_forbidden_rule_either():
    data = yaml.safe_load(stack.TEMPLATE.read_text())
    assert stack.forbidden(data, stack.TEMPLATE) == ()


# -- the optional `design` block (ADR-0040) -------------------------------------------


def test_a_product_that_declares_no_design_block_has_none():
    # Absent is legitimate, the same shape `witness`'s absence has: not every product
    # has handed the human a prototype yet.
    declared = {"checks": {"tests": "pytest"}}
    assert stack.design(declared, "x") is None


def test_the_minimal_valid_block_is_prototype_and_entry_alone():
    declared = {
        "checks": {"tests": "pytest"},
        "design": {"prototype": "docs/product/design-system/pantallas", "entry": "Home"},
    }
    found = stack.design(declared, "x")
    assert found == Design(
        prototype="docs/product/design-system/pantallas",
        entry="Home",
        sample_app=None,
        compare=None,
    )


def test_a_full_block_carries_both_commands():
    declared = {
        "checks": {"tests": "pytest"},
        "design": {
            "prototype": "pantallas",
            "entry": "Home",
            "sample_app": "npm run dev",
            "compare": "npm run compare",
        },
    }
    found = stack.design(declared, "x")
    assert found is not None
    assert found.sample_app == "npm run dev"
    assert found.compare == "npm run compare"


@pytest.mark.parametrize(
    "design",
    [
        ["pantallas", "Home"],  # a list, not a mapping
        {},  # present but empty is a half-written file, `setup`'s own shape
        {"prototype": "", "entry": "Home"},  # blank prototype
        {"prototype": "pantallas"},  # missing entry
        {"prototype": "pantallas", "entry": ""},  # blank entry
        {"prototype": "pantallas", "entry": "Home", "sample_app": 3},  # not a string
        {"prototype": "pantallas", "entry": "Home", "compare": "  "},  # blank command
        {"prototype": "pantallas", "entry": "Home", "screens": "x"},  # unknown sub-key
    ],
)
def test_a_broken_design_declaration_raises_at_load(design):
    declared = {"checks": {"tests": "pytest"}, "design": design}
    with pytest.raises(ValueError, match="stack.yaml: expected|unknown"):
        stack.design(declared, "stack.yaml")


def test_the_template_declares_no_design_block(tmp_path):
    data = yaml.safe_load(stack.TEMPLATE.read_text())
    assert stack.design(data, stack.TEMPLATE) is None
