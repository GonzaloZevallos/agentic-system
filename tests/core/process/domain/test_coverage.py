"""The coverage map's one format (GH-174): what `missing_invariants` refuses and what
`render` writes into the Developer's section. Pure: every case is a delivery's
`coverage` field and a count, never a tree or an invariant's text."""

import re
from pathlib import Path

import pytest

from agentic.core.process import domain
from agentic.core.process.domain.coverage import (
    BRIEF_FORM,
    EXAMPLE,
    MAPPED_FORM,
    NOT_TOUCHED,
    NOT_TOUCHED_FORM,
    REFUSALS,
    UNSATISFIABLE,
    UNSATISFIABLE_FORM,
    Entry,
    Refusal,
    classify,
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


# GH-363: the grammar of the body of an `invariant N:` line, with the lines the Developer
# really wrote and the validator refused (GH-293, GH-228) and the ones it always accepted.

REAL_REFUSED_BEFORE = [
    "invariant 2: docs/product/gate.md:84-89",
    "invariant 3: docs/product/gate.md:78-98, CODING_STANDARDS.md:213-214, "
    "docs/product/design-system/README.md:229-231, docs/product/accounts.md:186",
    "invariant 2: web/src/design/screens.test.ts:250 (new describe inserted before "
    "assertSelectedAccounts; no existing `it` edited or removed)",
    "invariant 3: web/src/design/screens.test.ts:259-272 (placeholders() on raw path/route, "
    "early return when path list is empty, no exemption list)",
    "invariant 4: web/src/design/screens.test.ts:272, web/src/design/screens.test.ts:282-284",
]

ACCEPTED_BEFORE = [
    "invariant 1: tests/test_stack_ci.py:48, tests/test_stack_ci.py:92, "
    ".agentic/stack.yaml:17, .agentic/stack.yaml:21",
    "invariant 3: not touched by this subtask: no assertion, timeout, retry or CI-only "
    "branch is changed",
    "invariant 6: not touched by this subtask: the flip commit is a later commit; "
    "ci: stays commented here",
    "invariant 4: pyproject.toml:52, pyproject.toml:53, scripts/design/mobile_seed.py:19",
    "invariant 1: a-b.py:3",
    "invariant 1: x.test.ts:12",
    "invariant 1: not touched by this subtask: nothing changes here.",
    "invariant 1: not touched by this subtask: a; b: c",
    "invariant 1: app/(group)/page.tsx:12",
    "invariant 1: a.py:12:5",
    "invariant 1: x:1(b:2,c):3",
    f"invariant 1: {UNSATISFIABLE}",
]

REFUSED_STAYS = [
    "invariant 1: a.py:84–89",
    "invariant 1: a.py:L84",
    "invariant 1: a.py:84..89",
    "invariant 1: a.py#L12",
    "invariant 1: a.py:84,86",
    "invariant 1: a.py:3.",
    "invariant 1: a.py",
    "invariant 1: done",
    "invariant 2: same as 1",
    "invariant 1:",
    "invariant 1: a.py:3,",
    "invariant 1: a.py:80-",
    f"invariant 1: {NOT_TOUCHED}",
    "invariant 1: a.py:3 (see b.py:9",
    "invariant 1: a.py:3 see b.py:9)",
    "invariant 1: a.py:3 (note (inner))",
    "invariant 1: (see below)",
    "invariant 1: a.py:3 (note) and more",
    "invariant 1: a.py:90-80",
]

NOT_AN_INVARIANT_LINE = [
    "- invariant 1: a.py:10",
    "**invariant 1: a.py:10**",
    "`invariant 1: a.py:10`",
    "invariants 1-3: a.py:10",
    "invariant 1, 2: a.py:10",
]


def _body(line: str) -> str:
    return line.split(":", 1)[1].strip()


def _entry(line: str):
    entries = parse(line, 6).entries
    return entries[0] if entries else None


@pytest.mark.parametrize("line", REAL_REFUSED_BEFORE)
def test_the_real_lines_the_old_grammar_refused_are_mapped_verbatim(line):
    number = int(line.split()[1].rstrip(":"))
    assert _entry(line) == Entry("mapped", number, line)
    assert classify(_body(line)) == "mapped"


@pytest.mark.parametrize("line", ACCEPTED_BEFORE)
def test_a_line_the_old_grammar_accepted_keeps_its_entry(line):
    number = int(line.split()[1].rstrip(":"))
    body = _body(line)
    expected = (
        "unsatisfiable"
        if body == UNSATISFIABLE
        else "not_touched"
        if body.startswith(f"{NOT_TOUCHED}:")
        else "mapped"
    )
    assert _entry(line) == Entry(expected, number, line)


@pytest.mark.parametrize(
    "body",
    [
        "a.py:3 (see b.py:9, c.py:4)",
        "a.py:3 (one, two)",
        "a.py:3 (one), b.py:4-9 (two, three)",
        "a.py:5-5",
        "a.py:3(tight)",
        "a.py:3 ()",
        "a.py:3 (calls parse() and format())",
    ],
)
def test_a_comment_holds_commas_and_locations_that_do_not_count(body):
    assert classify(body) == "mapped"


@pytest.mark.parametrize("line", REFUSED_STAYS)
def test_a_refused_line_gets_a_reason_and_leaves_its_invariant_missing(line):
    refusal = classify(_body(line))
    assert isinstance(refusal, Refusal) and refusal.reason
    number = int(line.split()[1].rstrip(":"))
    assert parse(line, 2).entries == ()
    assert number in parse(line, 2).missing


@pytest.mark.parametrize("line", NOT_AN_INVARIANT_LINE)
def test_a_line_that_is_not_an_invariant_line_stays_unread(line):
    assert parse(line, 3).entries == ()
    assert parse(line, 3).missing == (1, 2, 3)


def test_every_corpus_line_is_a_kind_or_a_refusal_with_a_reason():
    for line in REAL_REFUSED_BEFORE + ACCEPTED_BEFORE:
        assert classify(_body(line)) in ("mapped", "unsatisfiable", "not_touched")
    for line in REFUSED_STAYS:
        result = classify(_body(line))
        assert isinstance(result, Refusal) and result.reason.strip()


def test_each_refusal_says_what_is_wrong_with_its_own_reason():
    bodies = ("", "a.py:3 (a (b))", "a.py:3 (a", "(see below)", "a.py:3 (a) b", "a.py:9-3", "a.py")
    results = [classify(body) for body in (*bodies, f"{NOT_TOUCHED}:")]
    reasons = {r.reason for r in results if isinstance(r, Refusal)}
    assert len(reasons) == len(results)


def test_a_mechanical_slip_comes_with_the_corrected_shape_built_from_its_own_text():
    cases = {
        "a.py:84–89": "a.py:84-89",
        "a.py:84..89, b.py:3": "a.py:84-89, b.py:3",
        "a.py:L84": "a.py:84",
        "a.py:L84-L89": "a.py:84-89",
        "a.py#L12": "a.py:12",
        "a.py:3.": "a.py:3",
        "a.py:3 (note (inner) more)": "a.py:3 (note [inner] more)",
    }
    for body, corrected in cases.items():
        refusal = classify(body)
        assert isinstance(refusal, Refusal)
        assert refusal.corrected == corrected
        assert classify(corrected) == "mapped"
    for body in ("a.py", "a.py:84,86", "a.py:3 (a", "(see below)", "a.py:9-3"):
        refusal = classify(body)
        assert isinstance(refusal, Refusal) and refusal.corrected is None


def test_the_refusal_quotes_three_refused_lines_and_counts_the_rest():
    lines = [f"invariant {n}: nothing for {n}" for n in range(1, 21)]
    reason = missing_invariants({"coverage": lines}, 20)
    assert reason is not None
    assert reason.count("\n- `invariant ") == 3
    assert "`invariant 1: nothing for 1`" in reason and "`invariant 3: nothing for 3`" in reason
    assert "and 17 more" in reason
    assert "shape only" in reason and "location must be your own" in reason


def test_the_refusal_shows_the_fix_next_to_the_quoted_line():
    reason = missing_invariants({"coverage": ["invariant 1: a.py:84–89"]}, 1)
    assert reason is not None
    assert "`invariant 1: a.py:84–89`" in reason
    assert "invariant 1: a.py:84-89" in reason
    assert "more" not in reason.split("shape only")[0]


def test_a_refused_line_for_a_number_later_covered_is_not_quoted():
    lines = ["invariant 1: nothing", "invariant 1: a.py:1", "invariant 2: also nothing"]
    reason = missing_invariants({"coverage": lines}, 2)
    assert reason is not None
    assert "also nothing" in reason and "`invariant 1: nothing`" not in reason


def test_the_refusal_ends_with_the_forms_and_the_example():
    reason = missing_invariants({"coverage": []}, 3)
    assert reason is not None
    for form in (MAPPED_FORM, UNSATISFIABLE_FORM, NOT_TOUCHED_FORM):
        assert f"`{form}`" in reason
    assert EXAMPLE in reason


def test_the_example_is_a_complete_map():
    assert parse(EXAMPLE, 3).missing == ()
    assert [e.kind for e in parse(EXAMPLE, 3).entries] == [
        "mapped",
        "mapped",
        "not_touched",
        "brief",
    ]
    assert missing_invariants({"coverage": EXAMPLE}, 3) is None


def test_the_forms_name_the_range_and_the_comment():
    assert "-" in MAPPED_FORM and "(" in MAPPED_FORM and "…" in MAPPED_FORM


def test_the_developer_prompt_quotes_the_forms_and_one_example_equal_to_the_constant():
    path = Path(domain.__file__).parent / "prompts" / "developer.md"
    prompt = path.read_text()
    for form in (MAPPED_FORM, UNSATISFIABLE_FORM, NOT_TOUCHED_FORM, BRIEF_FORM):
        assert f"`{form}`" in prompt
    blocks = re.findall(r"```\n(.*?)\n```", prompt, re.DOTALL)
    assert blocks == [EXAMPLE]
    assert "range of lines" in prompt and "one comment in parentheses" in prompt
    assert parse(blocks[blocks.index(EXAMPLE)], 3).missing == ()
