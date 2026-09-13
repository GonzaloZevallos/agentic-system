import pytest

from agentic.adapters.filesystem import FilesystemKnowledge

GLOSSARY = "# Gym\n\n## Language\n\n**Gym**:\nA business.\n"


def test_read_is_empty_without_files(tmp_path):
    assert FilesystemKnowledge(tmp_path).read() == ""


def test_read_gathers_glossary_and_capabilities(tmp_path):
    (tmp_path / "CONTEXT.md").write_text(GLOSSARY)
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/subscriptions.md").write_text("# Subscriptions\n")
    (tmp_path / "docs/product/check-ins.md").write_text("# Check-ins\n")
    (tmp_path / "docs/product/notes.txt").write_text("ignored")
    text = FilesystemKnowledge(tmp_path).read()
    assert text.startswith("## Product knowledge\n\n`CONTEXT.md`:\n\n````markdown\n# Gym")
    assert text.index("check-ins.md") < text.index("subscriptions.md")
    assert "notes.txt" not in text


def test_write_definitions_creates_the_directory_and_rejects_bad_slugs(tmp_path):
    knowledge = FilesystemKnowledge(tmp_path)
    paths = knowledge.write_definitions({"check-ins": "# Check-ins\n\nRules.  "})
    assert paths == [tmp_path / "docs/product/check-ins.md"]
    assert paths[0].read_text() == "# Check-ins\n\nRules.\n"
    with pytest.raises(ValueError, match="slug"):
        knowledge.write_definitions({"Check Ins": "x"})
    assert not (tmp_path / "docs/product/Check Ins.md").exists()


def test_upsert_terms_edits_or_creates_the_glossary(tmp_path):
    knowledge = FilesystemKnowledge(tmp_path)
    assert knowledge.upsert_terms({}) is None
    assert not (tmp_path / "CONTEXT.md").exists()

    assert knowledge.upsert_terms({"Plan": {"definition": "Sold.", "avoid": ""}}) == (
        tmp_path / "CONTEXT.md"
    )
    assert (tmp_path / "CONTEXT.md").read_text() == (
        f"# {tmp_path.name}\n\n## Language\n\n**Plan**:\nSold.\n"
    )

    knowledge.upsert_terms({"Plan": {"definition": "Sold monthly.", "avoid": "tier"}})
    assert (
        (tmp_path / "CONTEXT.md").read_text().endswith("**Plan**:\nSold monthly.\n_Avoid_: tier\n")
    )
