import pytest

from agentic.core.knowledge.adapters.filesystem import FilesystemKnowledge

GLOSSARY = "# Gym\n\n## Language\n\n**Gym**:\nA business.\n"


def test_read_is_empty_without_files(tmp_path):
    assert FilesystemKnowledge(tmp_path).read() == ""


def test_read_gathers_glossary_capabilities_and_adrs(tmp_path):
    (tmp_path / "CONTEXT.md").write_text(GLOSSARY)
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/subscriptions.md").write_text("# Subscriptions\n")
    (tmp_path / "docs/product/check-ins.md").write_text("# Check-ins\n")
    (tmp_path / "docs/product/notes.txt").write_text("ignored")
    (tmp_path / "docs/adr").mkdir()
    (tmp_path / "docs/adr/0002-no-isolation.md").write_text("# No isolation\n")
    (tmp_path / "docs/adr/0001-stack.md").write_text("# Stack\n")
    (tmp_path / "docs/adr/template.txt").write_text("ignored")
    text = FilesystemKnowledge(tmp_path).read()
    assert text.startswith("## Product knowledge\n\n`CONTEXT.md`:\n\n````markdown\n# Gym")
    assert text.index("check-ins.md") < text.index("subscriptions.md")
    assert text.index("subscriptions.md") < text.index("docs/adr/0001-stack.md")
    assert text.index("0001-stack.md") < text.index("0002-no-isolation.md")
    assert "# No isolation" in text
    assert "notes.txt" not in text and "template.txt" not in text


def test_read_takes_the_adrs_from_the_given_root(tmp_path):
    # The task's checkout: an ADR the solution interview just wrote is knowledge already.
    checkout = tmp_path / "checkout"
    (checkout / "docs/adr").mkdir(parents=True)
    (checkout / "docs/adr/0003-new.md").write_text("# New\n")
    text = FilesystemKnowledge(tmp_path).read(checkout)
    assert "docs/adr/0003-new.md" in text and "# New" in text


def test_standards_default_to_the_systems_until_the_product_writes_its_own(tmp_path):
    text = FilesystemKnowledge(tmp_path).standards()
    assert text.startswith("## Engineering standards\n\n`CODING_STANDARDS.md`:\n\n````markdown\n")
    assert "## 1. Hexagonal architecture" in text and "## 6. Strict typing" in text

    (tmp_path / "CODING_STANDARDS.md").write_text("# Ours\n\nTabs.\n")
    text = FilesystemKnowledge(tmp_path).standards()
    assert "# Ours\n\nTabs." in text and "Hexagonal" not in text  # replaced whole


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


def test_distill_writes_capabilities_then_glossary(tmp_path):
    paths = FilesystemKnowledge(tmp_path).distill(
        {
            "definitions": [{"capability": "reports", "content": "# Reports"}],
            "glossary": [{"term": "Report", "definition": "A CSV.", "avoid": ""}],
        }
    )
    assert paths == [tmp_path / "docs/product/reports.md", tmp_path / "CONTEXT.md"]
    assert (tmp_path / "docs/product/reports.md").read_text() == "# Reports\n"
    assert "**Report**:\nA CSV.\n" in (tmp_path / "CONTEXT.md").read_text()


def test_distill_checks_everything_before_writing_anything(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        FilesystemKnowledge(tmp_path).distill(
            {
                "definitions": [
                    {"capability": "reports", "content": "x"},
                    {"capability": "Bad Slug", "content": "y"},
                ],
                "glossary": [],
            }
        )
    assert not (tmp_path / "docs/product").exists()


def test_distill_restores_what_was_there_when_a_write_fails(tmp_path):
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/reports.md").write_text("# Reports\n\nOld.\n")
    (tmp_path / "CONTEXT.md").mkdir()  # the glossary write will fail: it is a directory
    with pytest.raises(OSError):
        FilesystemKnowledge(tmp_path).distill(
            {
                "definitions": [
                    {"capability": "reports", "content": "# Reports\n\nNew."},
                    {"capability": "members", "content": "# Members"},
                ],
                "glossary": [{"term": "Report", "definition": "A CSV.", "avoid": ""}],
            }
        )
    # The replaced file is back, the new one is gone.
    assert (tmp_path / "docs/product/reports.md").read_text() == "# Reports\n\nOld.\n"
    assert not (tmp_path / "docs/product/members.md").exists()
