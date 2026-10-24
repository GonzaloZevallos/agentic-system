"""Product knowledge as text: how it is shown to a role, how the glossary is edited,
what an approval must carry."""

import pytest

from agentic.core.knowledge.domain import knowledge

GLOSSARY = """# Gym Management

Intro line.

## Language

**Gym**:
A business that owns members.
_Avoid_: tenant

**Member** (person):
Someone who belongs to a gym.

## Other

Untouched.
"""


def test_upsert_replaces_in_place_and_appends_new_terms():
    text = knowledge.upsert_terms(
        GLOSSARY,
        {
            "Member": {"definition": "A person in a gym, by email.", "avoid": "client, user"},
            "Check-in": {"definition": "A member showing up.", "avoid": ""},
        },
        "gym",
    )
    assert (
        text
        == """# Gym Management

Intro line.

## Language

**Gym**:
A business that owns members.
_Avoid_: tenant

**Member**:
A person in a gym, by email.
_Avoid_: client, user

**Check-in**:
A member showing up.

## Other

Untouched.
"""
    )


def test_upsert_starts_a_glossary_when_there_is_none():
    text = knowledge.upsert_terms(
        None, {"Plan": {"definition": "Something sold.", "avoid": ""}}, "gym"
    )
    assert text == "# gym\n\n## Language\n\n**Plan**:\nSomething sold.\n"


def test_upsert_adds_the_language_section_when_missing():
    text = knowledge.upsert_terms("# Gym\n", {"Plan": {"definition": "Sold.", "avoid": ""}}, "gym")
    assert text == "# Gym\n\n## Language\n\n**Plan**:\nSold.\n"


def test_check_slug():
    knowledge.check_slug("check-ins")
    with pytest.raises(ValueError, match="slug"):
        knowledge.check_slug("Check Ins")


def test_approval_check():
    jobs = "# Metrics\n\n## Jobs\n\n- When ..., I want ..., so I can ...\n"
    ok = {"verdict": "approved", "definitions": [{"capability": "metrics", "content": jobs}]}
    knowledge.approval_check("feature")(ok)
    # A definition that does not say which jobs it serves is not knowledge yet (JTBD).
    with pytest.raises(ValueError, match="no `## Jobs` section"):
        knowledge.approval_check("feature")(
            {"verdict": "approved", "definitions": [{"capability": "metrics", "content": "x"}]}
        )
    knowledge.approval_check("bug")({"verdict": "approved", "definitions": []})
    knowledge.approval_check("feature")({"verdict": "returned", "definitions": []})
    with pytest.raises(ValueError, match="without `definitions`"):
        knowledge.approval_check("feature")({"verdict": "approved", "definitions": []})
    with pytest.raises(ValueError, match="slug"):
        knowledge.approval_check("feature")(
            {"verdict": "approved", "definitions": [{"capability": "Check Ins", "content": jobs}]}
        )


def test_terms_reads_back_what_upsert_terms_writes():
    text = knowledge.upsert_terms(None, {"Check-in": {"definition": "x", "avoid": ""}}, "P")
    text = knowledge.upsert_terms(text, {"Turno": {"definition": "y", "avoid": "slot"}}, "P")
    assert knowledge.terms(text) == ["Check-in", "Turno"]
    assert knowledge.terms("**Bold** in prose, not a term.\n") == []


CONTEXT_MAP = """# Context Map

A team of agents.

## Contexts

- [Proceso](./agentic/core/process/CONTEXT.md): the roles and the task.
- [Medición](./agentic/core/measurement/CONTEXT.md): the metrics.

## Relationships

- **Medición → Proceso**: measurement reads what the process records.
"""


def test_contexts_reads_the_map_in_order_keyed_by_the_name_it_gives_them():
    assert knowledge.contexts(CONTEXT_MAP) == {
        "Proceso": "agentic/core/process/CONTEXT.md",
        "Medición": "agentic/core/measurement/CONTEXT.md",
    }


def test_a_map_without_links_has_no_contexts():
    assert knowledge.contexts("# Context Map\n\nNothing yet.\n") == {}


def test_upsert_keeps_the_lines_it_does_not_write():
    """`_Campo_` and anything else under an entry survives PM rewriting it: the writer
    of a definition is not the owner of every line the entry carries (issue #45)."""
    before = "# P\n\n## Language\n\n**Gym**:\nOld.\n_Avoid_: tenant\n_Campo_: gym — nobody\n"
    text = knowledge.upsert_terms(before, {"Gym": {"definition": "New.", "avoid": "club"}}, "p")
    assert "New." in text and "Old." not in text
    assert "_Avoid_: club" in text and "_Avoid_: tenant" not in text
    assert "_Campo_: gym — nobody" in text


NESTED_MAP = """# Map

## Contexts

- [Billing](./services/billing/domain/CONTEXT.md): money.
- [Orders](./services/orders/domain/CONTEXT.md): orders.
"""


def test_contexts_keyed_by_the_name_the_map_gives_them_not_the_directory():
    """`<module>/domain/CONTEXT.md` is a plausible hexagonal layout: keying by the
    parent directory would collapse both contexts into one and lose a glossary."""
    assert knowledge.contexts(NESTED_MAP) == {
        "Billing": "services/billing/domain/CONTEXT.md",
        "Orders": "services/orders/domain/CONTEXT.md",
    }


def test_a_map_naming_two_contexts_the_same_is_an_error():
    twice = "## Contexts\n\n- [A](./a/CONTEXT.md): x.\n- [A](./b/CONTEXT.md): y.\n"
    with pytest.raises(ValueError, match="twice"):
        knowledge.contexts(twice)


def test_a_context_resolves_by_its_name_or_by_its_directory():
    found = knowledge.contexts(CONTEXT_MAP)
    assert knowledge.context_path("Proceso", found) == "agentic/core/process/CONTEXT.md"
    assert knowledge.context_path("process", found) == "agentic/core/process/CONTEXT.md"


def test_a_context_that_is_not_in_the_map_is_an_error_that_lists_the_ones_that_are():
    with pytest.raises(ValueError, match="Proceso"):
        knowledge.context_path("billing", knowledge.contexts(CONTEXT_MAP))


def test_a_map_link_that_escapes_the_product_is_an_error():
    """`CONTEXT-MAP.md` lives in the checkout a role edits, so a relative link is a way
    to write outside its own worktree (ADR-0002, ADR-0017)."""
    with pytest.raises(ValueError, match="outside"):
        knowledge.contexts("## Contexts\n\n- [Shared](../../shared/CONTEXT.md): x.\n")


def test_upsert_only_keeps_key_lines_not_any_italic_prose():
    before = "# P\n\n## Language\n\n**Stage0**:\n_El checkout_ desde el que corre.\n_Campo_: x\n"
    text = knowledge.upsert_terms(before, {"Stage0": {"definition": "Corta.", "avoid": ""}}, "p")
    assert "_Campo_: x" in text
    assert "_El checkout_" not in text


def test_approval_check_demands_a_known_context_for_every_term():
    check = knowledge.approval_check("feature", {"Proceso": "a/process/CONTEXT.md"})
    data = {
        "verdict": "approved",
        "definitions": [{"capability": "roles", "content": "## Jobs\n"}],
        "glossary": [{"term": "Ola", "definition": "d", "avoid": "", "context": "billing"}],
    }
    with pytest.raises(ValueError, match="Proceso"):
        check(data)
    data["glossary"][0]["context"] = "Proceso"
    check(data)  # a known one passes


def test_approval_check_demands_a_context_at_all_when_the_product_is_mapped():
    check = knowledge.approval_check("feature", {"Proceso": "a/process/CONTEXT.md"})
    with pytest.raises(ValueError, match="multi-context"):
        check(
            {
                "verdict": "approved",
                "definitions": [{"capability": "roles", "content": "## Jobs\n"}],
                "glossary": [{"term": "Ola", "definition": "d", "avoid": ""}],
            }
        )


def test_a_product_with_one_glossary_needs_no_context():
    check = knowledge.approval_check("feature", {})
    check(
        {
            "verdict": "approved",
            "definitions": [{"capability": "roles", "content": "## Jobs\n"}],
            "glossary": [{"term": "Ola", "definition": "d", "avoid": ""}],
        }
    )


# The Brief (ADR-0025): a root-level, human-owned artefact, mechanically checked before
# a single role is paid. `BRIEF_HEADINGS` is its own tuple, deliberately not sharing
# `JOBS_HEADING` even though `## Jobs` coincides with it.

VALID_BRIEF = """# Brief

## Problem and who has it

Gym owners cannot tell if a member is about to churn.

## Jobs

- When a member skips two weeks, I want a flag, so I can call them.

## Constraints

- Scale: up to 200k members.
- Regulatory: none.

## Out of scope

Payment processing.
"""


def test_brief_refusal_accepts_a_valid_brief():
    assert knowledge.brief_refusal(VALID_BRIEF) is None


def test_brief_refusal_accepts_the_headings_in_any_order_and_with_extra_sections():
    reordered = """## Out of scope

Payment processing.

## Extra

Ignored.

## Jobs

- When ..., I want ..., so I can ...

## Constraints

- Scale: small.

## Problem and who has it

A problem.
"""
    assert knowledge.brief_refusal(reordered) is None


def test_brief_refusal_rejects_a_missing_heading():
    missing = VALID_BRIEF.replace("## Out of scope\n\nPayment processing.\n", "")
    reason = knowledge.brief_refusal(missing)
    assert reason is not None
    assert "Out of scope" in reason and "BRIEF.md" in reason
    assert knowledge.DEFAULT_BRIEF_TEMPLATE.read_text(encoding="utf-8") in reason


def test_brief_refusal_rejects_a_present_but_empty_heading():
    # Line-based, not substring containment: a heading with only whitespace before the
    # next `##` is the present-and-empty case the request itself rejects.
    empty = VALID_BRIEF.replace(
        "## Out of scope\n\nPayment processing.\n", "## Out of scope\n\n   \n"
    )
    reason = knowledge.brief_refusal(empty)
    assert reason is not None and "Out of scope" in reason


def test_brief_refusal_ignores_a_level_three_heading_with_the_same_title():
    demoted = VALID_BRIEF.replace("## Out of scope", "### Out of scope")
    reason = knowledge.brief_refusal(demoted)
    assert reason is not None and "Out of scope" in reason


def test_brief_refusal_matches_case_insensitively_and_strips_one_trailing_colon():
    shouted = VALID_BRIEF.replace("## Out of scope", "## OUT OF SCOPE:")
    assert knowledge.brief_refusal(shouted) is None


def test_brief_refusal_rejects_over_the_word_cap():
    long_job = "- " + " ".join(f"word{i}" for i in range(600))
    over_cap = VALID_BRIEF.replace(
        "- When a member skips two weeks, I want a flag, so I can call them.", long_job
    )
    reason = knowledge.brief_refusal(over_cap)
    assert reason is not None
    assert "words (cap 500)" in reason and "BRIEF.md" in reason


def test_brief_refusal_strips_fenced_blocks_before_counting_words():
    fenced_code = "```\n" + " ".join(f"word{i}" for i in range(600)) + "\n```\n"
    padded = VALID_BRIEF.replace(
        "Gym owners cannot tell if a member is about to churn.",
        "Gym owners cannot tell if a member is about to churn.\n\n" + fenced_code,
    )
    assert knowledge.brief_refusal(padded) is None


def _lines(count: int) -> str:
    return "\n".join(f"line {n}" for n in range(count)) + "\n"


def test_the_cap_is_300_lines_and_exactly_300_passes():
    assert knowledge.CRITERIA_MAX_LINES == 300
    assert knowledge.criteria_over_cap({knowledge.STANDARDS: _lines(300)}) == []


def test_a_document_over_the_cap_gets_one_message_naming_the_file_and_its_lines():
    messages = knowledge.criteria_over_cap({knowledge.STANDARDS: _lines(301)})
    assert len(messages) == 1
    assert knowledge.STANDARDS in messages[0] and "301" in messages[0]
    assert "cut" in messages[0] and "## " in messages[0]


def test_lines_are_counted_per_document_never_summed():
    texts = {
        knowledge.STANDARDS: _lines(200),
        knowledge.DESIGN_STANDARDS: _lines(200),
    }
    assert knowledge.criteria_over_cap(texts) == []


def test_messages_follow_the_input_order_and_skip_the_documents_within_the_cap():
    texts = {
        knowledge.DESIGN_STANDARDS: _lines(350),
        knowledge.STANDARDS: _lines(10),
        "OTHER.md": _lines(400),
    }
    messages = knowledge.criteria_over_cap(texts)
    assert [m.split()[0].strip("`:") for m in messages] == [knowledge.DESIGN_STANDARDS, "OTHER.md"]


def test_a_new_document_under_the_cap_is_no_regression():
    assert knowledge.criteria_regressions({}, {knowledge.STANDARDS: _lines(300)}) == []


def test_a_new_document_over_the_cap_is_a_regression_from_zero():
    (message,) = knowledge.criteria_regressions({}, {knowledge.DESIGN_STANDARDS: _lines(305)})
    assert knowledge.DESIGN_STANDARDS in message
    assert "305" in message and "0 at the base" in message and "cap 300" in message


def test_a_document_that_grows_past_the_cap_is_a_regression_naming_both_counts():
    base = {knowledge.STANDARDS: _lines(290)}
    (message,) = knowledge.criteria_regressions(base, {knowledge.STANDARDS: _lines(301)})
    assert knowledge.STANDARDS in message and "301" in message and "290" in message


def test_a_document_already_over_the_cap_and_unchanged_is_not_a_regression():
    both = {knowledge.STANDARDS: _lines(611)}
    assert knowledge.criteria_regressions(both, both) == []


def test_a_document_that_shrinks_but_stays_over_the_cap_is_not_a_regression():
    base = {knowledge.STANDARDS: _lines(611)}
    assert knowledge.criteria_regressions(base, {knowledge.STANDARDS: _lines(400)}) == []


def test_a_document_over_the_cap_that_grows_further_is_a_regression():
    base = {knowledge.STANDARDS: _lines(611)}
    assert len(knowledge.criteria_regressions(base, {knowledge.STANDARDS: _lines(612)})) == 1


def test_a_product_without_criteria_files_has_no_regressions():
    assert knowledge.criteria_regressions({}, {}) == []


def test_a_document_deleted_at_the_head_is_not_measured():
    assert knowledge.criteria_regressions({knowledge.STANDARDS: _lines(611)}, {}) == []


def test_splitting_one_document_into_two_over_the_cap_refuses_both():
    base = {"BIG.md": _lines(610)}
    head = {"A.md": _lines(305), "B.md": _lines(305)}
    messages = knowledge.criteria_regressions(base, head)
    assert [m.split()[0].strip("`:") for m in messages] == ["A.md", "B.md"]


def test_the_over_cap_message_is_the_one_the_run_start_prints():
    """One text for the cap (invariant 1): the regression names the same file and count."""
    (over,) = knowledge.criteria_over_cap({"A.md": _lines(305)})
    (regression,) = knowledge.criteria_regressions({}, {"A.md": _lines(305)})
    assert regression.startswith(over.split(";")[0])
