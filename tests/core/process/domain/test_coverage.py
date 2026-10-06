"""The coverage map's one format (GH-174): what `missing_invariants` refuses and what
`render` writes into the Developer's section. Pure: every case is a delivery's
`coverage` field and a count, never a tree or an invariant's text."""

import pytest

from agentic.core.process.domain.coverage import (
    NOT_TOUCHED,
    REFUSALS,
    UNSATISFIABLE,
    missing_invariants,
    parse,
    render,
)

FULL = [
    "invariant 1: a.py:3, b.py:10",
    f"invariant 2: {UNSATISFIABLE}",
    f"invariant 3: {NOT_TOUCHED}: only the prompts change",
]


def test_the_constants_are_the_contract():
    assert UNSATISFIABLE == "unsatisfiable, see Decisions"
    assert NOT_TOUCHED == "not touched by this subtask"
    assert REFUSALS == 1


def test_a_map_with_every_invariant_in_one_of_the_three_forms_passes():
    assert missing_invariants({"coverage": FULL}, 3) is None


def test_a_string_of_lines_passes_like_a_list():
    assert missing_invariants({"coverage": "\n".join(FULL)}, 3) is None


def test_whitespace_and_the_case_of_the_word_invariant_are_forgiven():
    lines = ["  Invariant 1: a.py:3  ", "INVARIANT 2:   b.py:4"]
    assert missing_invariants({"coverage": lines}, 2) is None


def test_an_invariant_missing_from_the_map_is_refused_by_number():
    reason = missing_invariants({"coverage": FULL[:2]}, 3)
    assert reason is not None
    assert "invariant(s) 3." in reason


def test_the_refusal_quotes_the_three_forms_from_the_constants():
    reason = missing_invariants({"coverage": []}, 2)
    assert reason is not None
    assert "invariant N: <path>:<line>" in reason
    assert f"invariant N: {UNSATISFIABLE}" in reason
    assert f"invariant N: {NOT_TOUCHED}: <one phrase>" in reason


def test_an_entry_with_no_path_and_line_is_refused_as_malformed():
    reason = missing_invariants({"coverage": ["invariant 1: done in the parser"]}, 1)
    assert reason is not None
    assert "malformed" in reason


def test_a_location_needs_a_line_number():
    assert missing_invariants({"coverage": ["invariant 1: a.py"]}, 1) is not None
    assert missing_invariants({"coverage": ["invariant 1: a.py:x"]}, 1) is not None
    assert missing_invariants({"coverage": ["invariant 1: a.py:3, b.py"]}, 1) is not None


def test_not_touched_needs_its_phrase():
    for bare in (f"invariant 1: {NOT_TOUCHED}", f"invariant 1: {NOT_TOUCHED}:   "):
        assert missing_invariants({"coverage": [bare]}, 1) is not None


@pytest.mark.parametrize("payload", [None, [], "", "   \n", 7, ["x"], {"coverage": None}])
def test_a_malformed_or_non_dict_payload_reads_as_all_missing(payload):
    reason = missing_invariants(payload, 2)
    assert reason is not None
    assert "1, 2" in reason


@pytest.mark.parametrize("coverage_field", [None, "", [], 5, [5, None], {"a": 1}])
def test_an_empty_or_unreadable_field_reads_as_all_missing(coverage_field):
    assert missing_invariants({"coverage": coverage_field}, 1) is not None
    assert missing_invariants({}, 1) is not None


def test_an_out_of_range_number_is_ignored_and_does_not_count_for_another():
    lines = ["invariant 0: a.py:1", "invariant 5: a.py:1", "invariant 1: a.py:2"]
    assert missing_invariants({"coverage": lines}, 2) is not None
    assert missing_invariants({"coverage": lines}, 1) is None


def test_a_duplicate_number_keeps_the_first_accepted_line():
    parsed = parse(["invariant 1: a.py:1", f"invariant 1: {UNSATISFIABLE}"], 1)
    assert [e.text for e in parsed.entries] == ["invariant 1: a.py:1"]


def test_a_malformed_line_does_not_shadow_a_later_good_one():
    lines = ["invariant 1: nothing", "invariant 1: a.py:1"]
    assert missing_invariants({"coverage": lines}, 1) is None


def test_no_invariants_means_nothing_to_refuse():
    assert missing_invariants({"coverage": []}, 0) is None
    assert missing_invariants(None, 0) is None


def test_brief_lines_are_never_checked():
    assert missing_invariants({"coverage": ["brief: x -> nowhere", *FULL]}, 3) is None
    assert missing_invariants({"coverage": ["brief: whatever", "invariant 1: a.py:1"]}, 1) is None


def test_render_groups_the_lines_and_the_numbers():
    lines = ["brief: the field -> s.py:9", *FULL, "invariant 4: c.py:1"]
    assert render(lines, 4) == (
        "### Coverage\n"
        "brief: the field -> s.py:9\n"
        "invariant 1: a.py:3, b.py:10\n"
        f"invariant 2: {UNSATISFIABLE}\n"
        f"invariant 3: {NOT_TOUCHED}: only the prompts change\n"
        "invariant 4: c.py:1\n"
        "\n"
        "Not touched: 3\n"
        "Claimed unsatisfiable: 2"
    )


def test_render_omits_the_groups_with_no_members():
    assert render(["invariant 1: a.py:1"], 1) == "### Coverage\ninvariant 1: a.py:1"


def test_render_says_which_invariants_are_still_not_mapped():
    block = render(["invariant 2: a.py:1", "invariant 3: nope"], 3)
    assert block is not None
    assert "Not mapped: 1, 3" in block
    assert "invariant 3: nope" not in block


def test_render_with_an_empty_map_and_invariants_names_them_all():
    assert render([], 2) == "### Coverage\n\nNot mapped: 1, 2"


def test_render_is_none_when_the_plan_has_no_invariants():
    assert render(["invariant 1: a.py:1"], 0) is None
    assert render([], 0) is None


def test_render_reads_a_string_of_lines_too():
    assert render("invariant 1: a.py:1\nbrief: x -> y:2", 1) == (
        "### Coverage\ninvariant 1: a.py:1\nbrief: x -> y:2"
    )
