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
    assert knowledge.assemble(None, {}) == ""


def test_assemble_fences_glossary_then_capabilities_sorted():
    text = knowledge.assemble(
        GLOSSARY, {"subscriptions.md": "# Subscriptions\n", "check-ins.md": "# Check-ins\n"}
    )
    assert text.startswith(
        "## Product knowledge\n\n`CONTEXT.md`:\n\n````markdown\n# Gym Management"
    )
    assert text.index("docs/product/check-ins.md") < text.index("docs/product/subscriptions.md")
    # Three files, each inside its own fence, so their `##` never read as task sections.
    assert text.count("````markdown") == 3 and text.count("````") == 6


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
    ok = {"verdict": "approved", "definitions": [{"capability": "metrics", "content": "x"}]}
    knowledge.approval_check("feature")(ok)
    knowledge.approval_check("bug")({"verdict": "approved", "definitions": []})
    knowledge.approval_check("feature")({"verdict": "returned", "definitions": []})
    with pytest.raises(ValueError, match="without `definitions`"):
        knowledge.approval_check("feature")({"verdict": "approved", "definitions": []})
    with pytest.raises(ValueError, match="slug"):
        knowledge.approval_check("feature")(
            {"verdict": "approved", "definitions": [{"capability": "Check Ins", "content": "x"}]}
        )


def test_terms_reads_back_what_upsert_terms_writes():
    text = knowledge.upsert_terms(None, {"Check-in": {"definition": "x", "avoid": ""}}, "P")
    text = knowledge.upsert_terms(text, {"Turno": {"definition": "y", "avoid": "slot"}}, "P")
    assert knowledge.terms(text) == ["Check-in", "Turno"]
    assert knowledge.terms("**Bold** in prose, not a term.\n") == []
