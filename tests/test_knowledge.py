"""Product knowledge: reading it whole, writing capability files, upserting glossary terms."""

import pytest

from agentic import knowledge

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


def test_read_is_empty_without_files(tmp_path):
    assert knowledge.read(tmp_path) == ""


def test_read_fences_glossary_then_capabilities_sorted(tmp_path):
    (tmp_path / "CONTEXT.md").write_text(GLOSSARY)
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/subscriptions.md").write_text("# Subscriptions\n")
    (tmp_path / "docs/product/check-ins.md").write_text("# Check-ins\n")
    text = knowledge.read(tmp_path)
    assert text.startswith(
        "## Product knowledge\n\n`CONTEXT.md`:\n\n````markdown\n# Gym Management"
    )
    assert text.index("docs/product/check-ins.md") < text.index("docs/product/subscriptions.md")
    # Three files, each inside its own fence, so their `##` never read as task sections.
    assert text.count("````markdown") == 3 and text.count("````") == 6


def test_write_definitions_creates_the_directory_and_rejects_bad_slugs(tmp_path):
    paths = knowledge.write_definitions(tmp_path, {"check-ins": "# Check-ins\n\nRules.  "})
    assert paths == [tmp_path / "docs/product/check-ins.md"]
    assert paths[0].read_text() == "# Check-ins\n\nRules.\n"
    with pytest.raises(ValueError, match="slug"):
        knowledge.write_definitions(tmp_path, {"Check Ins": "x"})


def test_upsert_replaces_in_place_and_appends_new_terms(tmp_path):
    (tmp_path / "CONTEXT.md").write_text(GLOSSARY)
    knowledge.upsert_terms(
        tmp_path,
        {
            "Member": {"definition": "A person in a gym, by email.", "avoid": "client, user"},
            "Check-in": {"definition": "A member showing up.", "avoid": ""},
        },
    )
    assert (
        (tmp_path / "CONTEXT.md").read_text()
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


def test_upsert_creates_a_glossary_when_missing_and_skips_when_empty(tmp_path):
    assert knowledge.upsert_terms(tmp_path, {}) is None
    assert not (tmp_path / "CONTEXT.md").exists()
    knowledge.upsert_terms(tmp_path, {"Plan": {"definition": "Something sold.", "avoid": ""}})
    assert (
        (tmp_path / "CONTEXT.md")
        .read_text()
        .endswith("## Language\n\n**Plan**:\nSomething sold.\n")
    )


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
