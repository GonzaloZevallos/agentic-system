import re

import pytest
import yaml

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain.design import Design
from agentic.core.process.domain.docs import Docs
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


# -- `parallel_checks`: the opt-in to run the declared checks concurrently ------------


@pytest.mark.parametrize("value", [True, False])
def test_parallel_checks_reads_a_declared_bool_as_it_is(value):
    declared = {"checks": {"tests": "pytest"}, "parallel_checks": value}
    assert stack.parallel_checks(declared, "x") is value


def test_a_product_that_does_not_declare_parallel_checks_keeps_them_serial():
    assert stack.parallel_checks({"checks": {"tests": "pytest"}}, "x") is False


@pytest.mark.parametrize(
    "value",
    ["true", "false", "yes", 1, 0, None, [], [True], {"on": True}, 1.0],
    ids=[
        "str-true",
        "str-false",
        "str-yes",
        "one",
        "zero",
        "null",
        "empty-list",
        "list",
        "map",
        "float",
    ],
)
def test_a_parallel_checks_that_is_not_a_bool_is_an_error_never_ignored(value):
    declared = {"checks": {"tests": "pytest"}, "parallel_checks": value}
    with pytest.raises(ValueError, match=r"stack.yaml: expected `parallel_checks`.*true or false"):
        stack.parallel_checks(declared, "stack.yaml")


def test_the_template_keeps_parallel_checks_commented_out():
    data = yaml.safe_load(stack.TEMPLATE.read_text(encoding="utf-8"))
    assert "parallel_checks" not in data
    assert "parallel_checks" in stack.TEMPLATE.read_text(encoding="utf-8")


# -- `ci`: the check-run names that gate a merge when GitHub's CI is trusted (ADR-0045) --


def test_a_product_that_does_not_declare_ci_gates_nothing_on_it():
    assert stack.ci({"checks": {"tests": "pytest"}}, "x") == ()


def test_ci_reads_the_declared_names_in_the_files_order():
    declared = {"checks": {"tests": "pytest"}, "ci": ["web", "e2e (chromium)"]}
    assert stack.ci(declared, "x") == ("web", "e2e (chromium)")


# -- `affected_checks` and `affected_fallback`: the opt-in affected-only form (ADR-0046) --

CHECKS = {"tests": "pytest", "lint": "ruff check ."}


def test_a_product_that_declares_no_affected_checks_reads_an_empty_mapping():
    assert stack.affected_checks({"checks": CHECKS}, "x") == {}


def test_affected_checks_reads_the_commands_stripped_in_file_order():
    declared = {
        "checks": CHECKS,
        "affected_checks": {
            "lint": "  ruff check --diff {base}  ",
            "tests": "pytest --since {base}",
        },
    }
    found = stack.affected_checks(declared, "x")
    assert found == {"lint": "ruff check --diff {base}", "tests": "pytest --since {base}"}
    assert list(found) == ["lint", "tests"]


def test_a_product_that_declares_no_fallback_reads_an_empty_tuple():
    assert stack.affected_fallback({"checks": CHECKS}, "x") == ()


def test_affected_fallback_reads_the_globs_stripped_in_file_order():
    declared = {"checks": CHECKS, "affected_fallback": ["  uv.lock", "tests/conftest.py "]}
    assert stack.affected_fallback(declared, "x") == ("uv.lock", "tests/conftest.py")


@pytest.mark.parametrize(
    ("value", "why"),
    [
        (["pytest {base}"], "list"),
        ("pytest {base}", "string"),
        (3, "int"),
    ],
)
def test_affected_checks_that_is_not_a_mapping_is_an_error(value, why):
    declared = {"checks": CHECKS, "affected_checks": value}
    with pytest.raises(ValueError, match=r"stack.yaml: expected `affected_checks`.*mapping"):
        stack.affected_checks(declared, "stack.yaml")


def test_an_affected_name_that_is_not_a_declared_check_is_an_error():
    declared = {"checks": CHECKS, "affected_checks": {"e2e": "playwright --only-changed={base}"}}
    with pytest.raises(ValueError, match=r"stack.yaml: .*'e2e'.*`affected_checks`.*`checks`"):
        stack.affected_checks(declared, "stack.yaml")


@pytest.mark.parametrize("command", ["", "   ", None, 3, ["pytest {base}"], {"a": "{base}"}])
def test_an_affected_command_that_is_not_a_non_blank_string_is_an_error(command):
    declared = {"checks": CHECKS, "affected_checks": {"tests": command}}
    with pytest.raises(ValueError, match=r"stack.yaml: expected a command for 'tests'.*affected"):
        stack.affected_checks(declared, "stack.yaml")


def test_an_affected_command_without_the_base_token_is_an_error():
    declared = {"checks": CHECKS, "affected_checks": {"tests": "pytest --lf"}}
    with pytest.raises(ValueError, match=r"stack.yaml: .*'tests'.*\{base\}"):
        stack.affected_checks(declared, "stack.yaml")


def test_the_base_token_is_literal_not_a_format_field():
    declared = {"checks": CHECKS, "affected_checks": {"tests": "pytest --since {sha}"}}
    with pytest.raises(ValueError, match=r"\{base\}"):
        stack.affected_checks(declared, "stack.yaml")


@pytest.mark.parametrize("command", ["{base}", "  {base} pytest", "{base}/run.sh"])
def test_an_affected_command_that_starts_with_the_base_is_an_error(command):
    """The reviewer's permission rule is the literal prefix before `{base}`; a command that
    starts with it has no prefix to allow."""
    declared = {"checks": CHECKS, "affected_checks": {"tests": command}}
    with pytest.raises(ValueError, match=r"stack.yaml: .*'tests'.*start"):
        stack.affected_checks(declared, "stack.yaml")


@pytest.mark.parametrize(
    "value",
    [[], None, "check", {"check": 1}, [1], ["check", ""], ["  "], ["check", None], ["a", "a"]],
    ids=["empty", "null", "str", "mapping", "int", "blank", "spaces", "null-item", "duplicate"],
)
def test_a_ci_that_is_not_a_list_of_distinct_names_is_an_error_naming_key_and_source(value):
    declared = {"checks": {"tests": "pytest"}, "ci": value}
    with pytest.raises(ValueError, match=r"stack.yaml: .*`ci`"):
        stack.ci(declared, "stack.yaml")


def test_the_template_keeps_ci_commented_out_and_says_what_a_name_is():
    text = stack.TEMPLATE.read_text(encoding="utf-8")
    assert "ci" not in yaml.safe_load(text)
    assert "# ci:" in text and "conditional" in text and "matrix" in text


@pytest.mark.parametrize(
    "value",
    ["uv.lock", {"a": "uv.lock"}, ["uv.lock", ""], ["  "], ["uv.lock", 3], [None], 3],
    ids=["string", "map", "blank-entry", "blank-only", "int-entry", "null-entry", "int"],
)
def test_an_affected_fallback_that_is_not_a_list_of_non_blank_strings_is_an_error(value):
    declared = {"checks": CHECKS, "affected_fallback": value}
    with pytest.raises(ValueError, match=r"stack.yaml: expected `affected_fallback`.*list"):
        stack.affected_fallback(declared, "stack.yaml")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("affected_checks", {}),
        ("affected_checks", None),
        ("affected_fallback", None),
        ("affected_fallback", []),
        ("affected_checks", []),
    ],
    ids=[
        "checks-empty-map-same-outcome-new-message",
        "checks-null-same-outcome-new-message",
        "fallback-null-same-outcome-new-message",
        "fallback-empty-list-outcome-changes-now-refused",
        "checks-empty-list",
    ],
)
def test_an_explicitly_empty_affected_declaration_is_refused_naming_key_and_source(key, value):
    declared = {"checks": CHECKS, key: value}
    read = stack.affected_checks if key == "affected_checks" else stack.affected_fallback
    with pytest.raises(ValueError) as refused:
        read(declared, "stack.yaml")
    message = str(refused.value)
    assert "stack.yaml" in message and f"`{key}`" in message
    assert stack.EMPTY_DECLARATION in message


def _normalised(text: str) -> str:
    stripped = (line.lstrip().removeprefix("#") for line in text.splitlines())
    return " ".join(" ".join(stripped).split())


def _clause_in(template: str) -> bool:
    return _normalised(stack.EMPTY_DECLARATION) in _normalised(template)


def test_the_template_states_the_empty_declaration_rule_in_the_codes_words():
    assert _clause_in(stack.TEMPLATE.read_text(encoding="utf-8"))


def test_a_template_without_the_empty_declaration_clause_fails_the_comparison():
    stale = (
        "# `affected_checks` and `affected_fallback`: optional (ADR-0046).\n# affected_fallback:\n"
    )
    assert not _clause_in(stale)
    assert _clause_in(f"# a key that {stack.EMPTY_DECLARATION.replace('; ', ';\n#   ')} \n")


def test_the_two_keys_are_declarable_and_not_taken_for_a_typo():
    declared = {
        "checks": CHECKS,
        "affected_checks": {"tests": "pytest --since {base}"},
        "affected_fallback": ["uv.lock"],
    }
    assert stack.checks(declared, "x") == CHECKS


def test_the_template_keeps_both_affected_keys_commented_out_and_explained():
    text = stack.TEMPLATE.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert "affected_checks" not in data and "affected_fallback" not in data
    assert "# affected_checks:" in text and "# affected_fallback:" in text
    assert stack.affected_checks(data, stack.TEMPLATE) == {}
    assert stack.affected_fallback(data, stack.TEMPLATE) == ()


# -- `delivery_checks`: the fast tier of the two-tier gate -------------------------------


def test_a_product_that_declares_no_delivery_checks_has_no_tiers():
    assert stack.delivery_checks({"checks": CHECKS}, "x") == ()


def test_delivery_checks_reads_the_names_stripped_in_the_files_order():
    declared = {"checks": CHECKS, "delivery_checks": ["  lint", "tests "]}
    assert stack.delivery_checks(declared, "x") == ("lint", "tests")


def test_delivery_checks_equal_to_all_of_checks_is_valid():
    declared = {"checks": CHECKS, "delivery_checks": ["tests", "lint"]}
    assert stack.delivery_checks(declared, "x") == ("tests", "lint")


@pytest.mark.parametrize(
    "value",
    ["tests", {"tests": "pytest"}, 3, True],
    ids=["string", "mapping", "int", "bool"],
)
def test_delivery_checks_that_is_not_a_list_is_an_error(value):
    declared = {"checks": CHECKS, "delivery_checks": value}
    with pytest.raises(ValueError, match=r"stack.yaml: expected `delivery_checks`.*list"):
        stack.delivery_checks(declared, "stack.yaml")


@pytest.mark.parametrize("value", [[], None], ids=["empty-list", "null"])
def test_an_empty_delivery_checks_is_declared_empty_naming_key_and_source(value):
    declared = {"checks": CHECKS, "delivery_checks": value}
    with pytest.raises(ValueError) as refused:
        stack.delivery_checks(declared, "stack.yaml")
    message = str(refused.value)
    assert "stack.yaml" in message and "`delivery_checks`" in message
    assert stack.EMPTY_DECLARATION in message


@pytest.mark.parametrize(
    "value",
    [[""], ["  "], ["tests", None], [3], [["tests"]], [{"tests": 1}]],
    ids=["empty", "spaces", "null-item", "int-item", "list-item", "map-item"],
)
def test_a_delivery_check_that_is_not_a_non_blank_string_is_an_error(value):
    declared = {"checks": CHECKS, "delivery_checks": value}
    with pytest.raises(ValueError, match=r"stack.yaml: expected non-blank.*`delivery_checks`"):
        stack.delivery_checks(declared, "stack.yaml")


def test_a_delivery_check_declared_twice_is_an_error():
    declared = {"checks": CHECKS, "delivery_checks": ["tests", " tests"]}
    with pytest.raises(ValueError, match=r"stack.yaml: .*'tests'.*twice.*`delivery_checks`"):
        stack.delivery_checks(declared, "stack.yaml")


def test_a_delivery_check_that_is_not_a_declared_check_is_an_error_listing_the_known_ones():
    declared = {"checks": CHECKS, "delivery_checks": ["tests", "e2e"]}
    with pytest.raises(
        ValueError, match=r"stack.yaml: .*'e2e'.*`delivery_checks`.*`checks`.*tests, lint"
    ):
        stack.delivery_checks(declared, "stack.yaml")


def test_delivery_checks_is_declarable_and_not_taken_for_a_typo():
    declared = {"checks": CHECKS, "delivery_checks": ["lint"]}
    assert stack.checks(declared, "x") == CHECKS


def test_the_template_keeps_delivery_checks_commented_out_and_explained():
    text = stack.TEMPLATE.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert "delivery_checks" not in data
    assert "# delivery_checks:" in text
    assert stack.delivery_checks(data, stack.TEMPLATE) == ()


# --- rerun_checks (ADR-0066) ---------------------------------------------------------------

FORM_PATTERN = r"(?m)^FAILED (\S+)"


def rerun_data(**entry):
    form = {"command": "pytest --lf", "test_id": FORM_PATTERN, **entry}
    return {
        "checks": {"tests": "pytest", "lint": "ruff check ."},
        "delivery_checks": ["tests"],
        "rerun_checks": {"tests": form},
    }


def test_an_absent_rerun_checks_is_the_gate_of_today():
    assert stack.rerun_checks({"checks": {"tests": "pytest"}}, "x") == {}


def test_a_declared_form_is_kept_by_check_name():
    found = stack.rerun_checks(rerun_data(), "x")
    assert found == {"tests": stack.RerunForm(command="pytest --lf", test_id=FORM_PATTERN)}


def test_rerun_checks_is_a_key_the_file_may_declare():
    assert "rerun_checks" in stack.KEYS
    stack.checks(rerun_data(), "x")  # an unknown key would raise here


def reject(data, match):
    with pytest.raises(ValueError, match=match):
        stack.rerun_checks(data, "stack.yaml")


def test_a_check_that_is_not_declared_is_refused():
    data = rerun_data()
    data["rerun_checks"] = {"e2e": data["rerun_checks"]["tests"]}
    reject(data, r"'e2e' under `rerun_checks` is not a check declared under `checks`")


def test_a_command_without_test_id_is_refused():
    data = rerun_data()
    del data["rerun_checks"]["tests"]["test_id"]
    reject(data, r"'tests' under `rerun_checks`.*`test_id`")


def test_a_test_id_without_command_is_refused():
    data = rerun_data()
    del data["rerun_checks"]["tests"]["command"]
    reject(data, r"'tests' under `rerun_checks`.*`command`")


def test_an_empty_or_misshapen_rerun_checks_is_refused():
    for bad in (None, {}, [], "tests", {"tests": "pytest --lf"}, {"tests": {}}):
        data = rerun_data()
        data["rerun_checks"] = bad
        reject(data, r"`rerun_checks`")


def test_an_unknown_sub_key_is_refused():
    reject(rerun_data(extra="x"), r"unknown key\(s\) extra under `rerun_checks`")


@pytest.mark.parametrize("pattern", ["(", "(?P<a>x", "[a-"])
def test_a_test_id_that_does_not_compile_is_refused(pattern):
    reject(rerun_data(test_id=pattern), r"`test_id`.*does not compile")


@pytest.mark.parametrize("pattern", [r"FAILED \S+", r"(a)(b)", r"(?:a)b"])
def test_a_test_id_without_exactly_one_group_is_refused(pattern):
    reject(rerun_data(test_id=pattern), r"`test_id`.*exactly one capturing group")


def test_a_test_id_longer_than_200_characters_is_refused():
    reject(rerun_data(test_id="(" + "a" * 199 + ")"), r"`test_id`.*at most 200 characters")
    stack.rerun_checks(rerun_data(test_id="(" + "a" * 198 + ")"), "x")


@pytest.mark.parametrize("pattern", [r"(a+)+", r"(\w+\s)*", r"(?:\d+,)+(x)", r"(a*b)+"])
def test_a_quantified_group_that_contains_a_quantifier_is_refused(pattern):
    reject(rerun_data(test_id=pattern), r"`test_id`.*quantified group that contains a quantifier")


@pytest.mark.parametrize("pattern", [r"(a|bc)+", r"((?:foo|bar)x)*", r"(?:a|ab)+(z)"])
def test_an_alternation_inside_a_quantified_group_is_refused(pattern):
    reject(rerun_data(test_id=pattern), r"`test_id`.*alternation inside a quantified group")


@pytest.mark.parametrize(
    "pattern",
    [FORM_PATTERN, r"^\s+(\S+\.spec\.ts:\d+)", r"(a|b)c", r"(?m)^(?:FAILED|ERROR) (\S+)"],
)
def test_ordinary_patterns_are_accepted(pattern):
    stack.rerun_checks(rerun_data(test_id=pattern), "x")


def test_a_form_without_delivery_checks_is_refused():
    data = rerun_data()
    del data["delivery_checks"]
    reject(data, r"`rerun_checks`.*requires `delivery_checks`")


# --- check_budgets (GH-365) ----------------------------------------------------------------


def budget_data(budgets=None, **more):
    return {
        "checks": {"tests": "pytest", "lint": "ruff check ."},
        "delivery_checks": ["lint"],
        "check_budgets": {"tests": 600, "lint": 30.5} if budgets is None else budgets,
        **more,
    }


def test_an_absent_check_budgets_declares_none():
    assert stack.check_budgets({"checks": {"tests": "pytest"}}, "x") == {}


def test_check_budgets_keep_each_figure_by_check_name_in_order():
    found = stack.check_budgets(budget_data(), "x")
    assert list(found.items()) == [("tests", 600), ("lint", 30.5)]


def test_check_budgets_is_a_key_the_file_may_declare():
    assert "check_budgets" in stack.KEYS
    stack.checks(budget_data(), "x")  # an unknown key would raise here


@pytest.mark.parametrize(
    ("budgets", "match"),
    [
        ([("tests", 5)], "mapping of checks to seconds"),
        ({}, "declared empty"),
        ({"e2e": 5}, "'e2e' under `check_budgets` is not a check declared under `checks`"),
        ({"tests": 0}, "positive number of seconds"),
        ({"tests": -3}, "positive number of seconds"),
        ({"tests": True}, "positive number of seconds"),
        ({"tests": "600"}, "positive number of seconds"),
        ({"tests": None}, "positive number of seconds"),
    ],
)
def test_a_check_budgets_that_is_not_check_to_positive_seconds_fails_at_load(budgets, match):
    with pytest.raises(ValueError, match=match):
        stack.check_budgets(budget_data(budgets), "stack.yaml")


def test_check_budgets_without_delivery_checks_is_refused_naming_the_full_run():
    data = budget_data()
    del data["delivery_checks"]
    with pytest.raises(ValueError, match="stack.yaml: `check_budgets` requires `delivery_checks`"):
        stack.check_budgets(data, "stack.yaml")


# --- docs (the stack contract for generated docs) ------------------------------------------

GENERATED = "docs/architecture/generated/**"


def docs_data(block=None, **more):
    return {
        "checks": {"tests": "pytest", "lint": "ruff check ."},
        "docs": {"generate": "python scripts/gen_docs.py", "paths": [GENERATED]}
        if block is None
        else block,
        **more,
    }


def test_an_absent_docs_block_is_none_and_changes_no_answer():
    plain = {"checks": {"tests": "pytest"}, "delivery_checks": ["tests"]}
    assert stack.docs(plain, "x") is None
    assert stack.checks(plain, "x") == {"tests": "pytest"}
    assert stack.delivery_checks(plain, "x") == ("tests",)


def test_a_declared_docs_block_is_read_and_its_check_joins_checks_last():
    data = docs_data()
    assert stack.docs(data, "x") == Docs(generate="python scripts/gen_docs.py", paths=(GENERATED,))
    found = stack.checks(data, "x")
    assert list(found) == ["tests", "lint", "docs"]
    assert found["docs"] == stack.docs_command(stack.docs(data, "x"))


def test_the_docs_check_joins_delivery_checks_only_when_tiers_are_declared():
    assert stack.delivery_checks(docs_data(), "x") == ()
    tiers = docs_data(delivery_checks=["lint"])
    assert stack.delivery_checks(tiers, "x") == ("lint", "docs")
    listed = docs_data(delivery_checks=["docs", "lint"])
    assert stack.delivery_checks(listed, "x") == ("docs", "lint")


def test_a_product_check_named_docs_is_refused_as_reserved():
    data = docs_data(checks={"docs": "make docs"})
    with pytest.raises(ValueError, match="`docs` is reserved"):
        stack.checks(data, "stack.yaml")


def test_a_declared_check_named_docs_is_untouched_without_a_docs_block():
    assert stack.checks({"checks": {"docs": "make docs"}}, "x") == {"docs": "make docs"}


def test_a_product_check_named_docs_without_a_docs_block_is_not_added_to_delivery_checks():
    data = {"checks": {"docs": "make docs", "lint": "ruff"}, "delivery_checks": ["lint"]}
    assert stack.delivery_checks(data, "x") == ("lint",)


def test_a_budget_an_affected_form_and_a_rerun_form_may_name_the_docs_check():
    data = docs_data(delivery_checks=["lint"], check_budgets={"docs": 90})
    assert stack.check_budgets(data, "x") == {"docs": 90}
    data = docs_data(affected_checks={"docs": "make docs --since {base}"})
    assert stack.affected_checks(data, "x") == {"docs": "make docs --since {base}"}


@pytest.mark.parametrize(
    "block",
    [
        "generate",
        ["generate"],
        {"generate": "x"},
        {"paths": [GENERATED]},
        {"generate": "x", "paths": [GENERATED], "extra": 1},
        {"generate": "", "paths": [GENERATED]},
        {"generate": "  ", "paths": [GENERATED]},
        {"generate": 5, "paths": [GENERATED]},
        {"generate": "a\nb", "paths": [GENERATED]},
        {"generate": "x", "paths": GENERATED},
        {"generate": "x", "paths": []},
        {"generate": "x", "paths": [" "]},
        {"generate": "x", "paths": [3]},
    ],
)
def test_a_docs_block_of_the_wrong_shape_fails_at_load(block):
    with pytest.raises(ValueError, match="stack.yaml: .*`docs`|`generate`|`paths`"):
        stack.docs(docs_data(block), "stack.yaml")


@pytest.mark.parametrize("empty", [None, {}])
def test_a_docs_block_declared_empty_says_to_leave_the_key_out(empty):
    data = {"checks": {"tests": "pytest"}, "docs": empty}
    with pytest.raises(ValueError, match="`docs` is declared empty; leave the key out"):
        stack.docs(data, "stack.yaml")


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/etc/**", "absolute"),
        ("docs/../.git/**", "`..`"),
        ("../x/**", "`..`"),
        (":(exclude)x", "starts with `:`"),
        ("**/*.md", "empty literal prefix"),
        ("*.md", "empty literal prefix"),
        ("docs/**", "overlaps `docs/adr`"),
        ("docs/", "overlaps `docs/adr`"),
        ("docs/adr/**", "overlaps `docs/adr`"),
        ("docs/adr/0001.md", "overlaps `docs/adr`"),
        (".agentic/**", "overlaps `.agentic`"),
        (".agentic/index/x.md", "overlaps `.agentic`"),
        (".git/hooks/*", "overlaps `.git`"),
        (".g*", "overlaps `.git`"),
        ("./docs/x/**", "`.` segment"),
    ],
)
def test_an_owned_path_that_is_unsafe_is_refused_naming_the_entry_and_why(path, reason):
    data = docs_data({"generate": "x", "paths": [GENERATED, path]})
    with pytest.raises(ValueError, match=re.escape(f"{path!r} under `docs`") + ".*" + reason):
        stack.docs(data, "stack.yaml")


@pytest.mark.parametrize(
    "path",
    [
        "docs/architecture/generated/**",
        "docs/architecture/generated/",
        "docs/reference/api.md",
        "diagrams/*.mmd",
    ],
)
def test_an_owned_path_with_a_safe_literal_prefix_is_accepted(path):
    assert stack.docs(docs_data({"generate": "x", "paths": [path]}), "x") == Docs("x", (path,))


@pytest.mark.parametrize(
    ("where", "key"),
    [
        ("checks", "tests"),
        ("affected_checks", "tests"),
        ("rerun_checks", "tests"),
    ],
)
def test_parallel_checks_refuses_a_check_that_reads_an_owned_path(where, key):
    command = "grep -r todo docs/architecture/generated/ {base}"
    data = docs_data(
        parallel_checks=True,
        delivery_checks=["tests"],
        affected_checks={"tests": command if where == "affected_checks" else "pytest {base}"},
        rerun_checks={
            "tests": {
                "command": command if where == "rerun_checks" else "pytest --lf",
                "test_id": r"(\S+)",
            }
        },
    )
    data["checks"]["tests"] = command if where == "checks" else "pytest"
    with pytest.raises(ValueError, match=rf"'{key}' under `{where}`.*docs/architecture/generated/"):
        stack.docs(data, "stack.yaml")
    del data["parallel_checks"]
    assert stack.docs(data, "stack.yaml") is not None


def test_parallel_checks_accepts_checks_that_do_not_read_the_owned_paths():
    assert stack.docs(docs_data(parallel_checks=True), "x") is not None


def test_docs_is_a_key_the_file_may_declare():
    assert "docs" in stack.KEYS
