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


def test_assemble_is_empty_without_files():
    assert knowledge.assemble({}, {}, {}) == ""


def test_assemble_fences_glossary_then_capabilities_then_adrs_sorted():
    text = knowledge.assemble(
        {"CONTEXT.md": GLOSSARY},
        {"subscriptions.md": "# Subscriptions\n", "check-ins.md": "# Check-ins\n"},
        {"0002-no-isolation.md": "# No isolation\n", "0001-stack.md": "# Stack\n"},
    )
    assert text.startswith(
        "## Product knowledge\n\n`CONTEXT.md`:\n\n````markdown\n# Gym Management"
    )
    assert text.index("docs/product/check-ins.md") < text.index("docs/product/subscriptions.md")
    assert text.index("docs/product/subscriptions.md") < text.index("docs/adr/0001-stack.md")
    assert text.index("docs/adr/0001-stack.md") < text.index("docs/adr/0002-no-isolation.md")
    # Five files, each inside its own fence, so their `##` never read as task sections.
    assert text.count("````markdown") == 5 and text.count("````") == 10


def test_assemble_adrs_alone_are_knowledge():
    text = knowledge.assemble({}, {}, {"0001-stack.md": "# Stack\n"})
    assert text == (
        "## Product knowledge\n\n`docs/adr/0001-stack.md`:\n\n````markdown\n# Stack\n````"
    )


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


def test_assemble_fences_every_glossary_by_its_path():
    text = knowledge.assemble(
        {"CONTEXT-MAP.md": CONTEXT_MAP, "agentic/core/process/CONTEXT.md": GLOSSARY}, {}, {}
    )
    # The map comes first: its relationships are what say which context may know which.
    assert text.index("CONTEXT-MAP.md") < text.index("agentic/core/process/CONTEXT.md")
    assert text.count("````markdown") == 2


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
