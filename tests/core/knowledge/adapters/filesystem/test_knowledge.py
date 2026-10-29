import unicodedata

import pytest

from agentic.core.knowledge.adapters.filesystem import FilesystemKnowledge
from agentic.core.knowledge.domain import knowledge as knowledge_domain

FAMILIES = ("product", "engineering")
GLOSSARY = "# Gym\n\n## Language\n\n**Gym**:\nA business.\n"


def test_read_is_empty_without_files(tmp_path):
    assert FilesystemKnowledge(tmp_path).read() == ""


STANDARDS = (
    "# Standards\n\n## 1. Hexagonal\n\nPorts.\n\n"
    "## 9. Isolation\n<!-- agentic:always -->\n\nNever cross a business.\n"
)


def _product(tmp_path):
    (tmp_path / "BRIEF.md").write_text("# Brief\n\nGym owners.\n")
    (tmp_path / "CONTEXT.md").write_text(GLOSSARY)
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/subscriptions.md").write_text("# Subscriptions\n\nPlans.\n")
    (tmp_path / "docs/product/notes.txt").write_text("ignored")
    (tmp_path / "docs/adr").mkdir()
    (tmp_path / "docs/adr/0001-stack.md").write_text(
        "---\nstatus: accepted\n---\n# Stack\n\nBody.\n"
    )
    (tmp_path / "docs/adr/0002-old.md").write_text("---\nstatus: superseded by 0001\n---\n# Old\n")
    (tmp_path / "CODING_STANDARDS.md").write_text(STANDARDS)


def test_read_is_the_always_whole_part_plus_the_area_index(tmp_path):
    _product(tmp_path)
    text = FilesystemKnowledge(tmp_path).read()
    assert text.startswith("## Product knowledge\n\n`BRIEF.md`:\n\n````markdown\n# Brief")
    assert "`CONTEXT.md`:" in text and "A business." in text
    assert "`.agentic/index/product.md`" in text and "`.agentic/index/adr.md`" in text
    # What goes through the index does not also arrive whole (invariant 1).
    assert "Plans." not in text and "Body." not in text and "Never cross" not in text
    assert "standards.md" not in text  # the standards come from `standards`


def test_index_files_list_each_entry_once_and_leave_superseded_adrs_out(tmp_path):
    _product(tmp_path)
    files = FilesystemKnowledge(tmp_path).index_files(None, FAMILIES)
    assert "docs/product/subscriptions.md" in files[".agentic/index/product.md"]
    assert "docs/adr/0001-stack.md" in files[".agentic/index/adr.md"]
    assert "0002-old" not in files[".agentic/index/adr.md"]  # still readable by its path
    assert (tmp_path / "docs/adr/0002-old.md").is_file()
    assert "notes.txt" not in "".join(files.values())
    assert files[".agentic/index/standards/01-hexagonal.md"] == ("## 1. Hexagonal\n\nPorts.\n")
    assert all(path.startswith(".agentic/index/") for path in files)


def test_read_takes_the_adrs_from_the_given_root(tmp_path):
    # The task's checkout: an ADR the solution interview just wrote is knowledge already.
    checkout = tmp_path / "checkout"
    (checkout / "docs/adr").mkdir(parents=True)
    (checkout / "docs/adr/0003-new.md").write_text("# New\n")
    knowledge = FilesystemKnowledge(tmp_path)
    assert "adr.md" in knowledge.read(checkout)
    assert (
        "docs/adr/0003-new.md" in knowledge.index_files(checkout, FAMILIES)[".agentic/index/adr.md"]
    )
    assert knowledge.index_files(None, FAMILIES) != knowledge.index_files(checkout, FAMILIES)


def test_an_unknown_role_tag_in_an_adr_names_the_file(tmp_path):
    (tmp_path / "docs/adr").mkdir(parents=True)
    (tmp_path / "docs/adr/0001-x.md").write_text("---\nroles: [wizard]\n---\n# X\n")
    with pytest.raises(ValueError, match=r"0001-x\.md.*wizard"):
        FilesystemKnowledge(tmp_path).index_files(None, FAMILIES)


def test_standards_are_whole_for_a_verifier_and_through_the_index_otherwise(tmp_path):
    _product(tmp_path)
    knowledge = FilesystemKnowledge(tmp_path)
    whole = knowledge.standards(whole=True)
    assert "Ports." in whole and "Never cross a business." in whole
    by_index = knowledge.standards(whole=False)
    assert by_index.startswith("## Engineering standards\n\n")
    assert "Never cross a business." in by_index  # marked: overrides everything
    assert "Ports." not in by_index and "`.agentic/index/standards.md`" in by_index
    assert (
        "Ports."
        in knowledge.index_files(None, FAMILIES)[".agentic/index/standards/01-hexagonal.md"]
    )
    assert "Never cross" not in "".join(
        knowledge.index_files(None, FAMILIES).values()
    )  # not both ways


BRIEF = (
    "## Problem and who has it\n\nGym owners cannot tell who is about to churn.\n\n"
    "## Jobs\n\n- When a member skips, I want a flag, so I can call them.\n\n"
    "## Constraints\n\n- Scale: up to 200k members.\n\n"
    "## Out of scope\n\nPayment processing.\n"
)


def test_read_is_unchanged_without_a_brief_on_disk(tmp_path):
    (tmp_path / "CONTEXT.md").write_text(GLOSSARY)
    text = FilesystemKnowledge(tmp_path).read()
    assert "BRIEF.md" not in text
    assert text.startswith("## Product knowledge\n\n`CONTEXT.md`:")


def test_read_puts_the_brief_first_when_one_is_on_disk(tmp_path):
    # ADR-0025: `read()` (called in the `preamble` of every role that reads the knowledge)
    # threads `self.brief(root)` into `assemble`. A `BRIEF.md` on disk must arrive as
    # its own fenced block, first, before the glossary -- and adding it must move
    # nothing else.
    (tmp_path / "CONTEXT.md").write_text(GLOSSARY)
    without_brief = FilesystemKnowledge(tmp_path).read()

    (tmp_path / "BRIEF.md").write_text(BRIEF)
    with_brief = FilesystemKnowledge(tmp_path).read()

    brief_block = "`BRIEF.md`:\n\n````markdown\n" + BRIEF.strip() + "\n````\n\n"
    assert with_brief.startswith("## Product knowledge\n\n" + brief_block + "`CONTEXT.md`")
    # Exactly one block added and nothing else moved: cut the Brief's own fenced block
    # back out and what remains is byte for byte what `read()` returned before
    # `BRIEF.md` existed on disk.
    assert with_brief == without_brief.replace(
        "## Product knowledge\n\n", "## Product knowledge\n\n" + brief_block
    )


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


def test_consolidate_writes_capabilities_then_glossary(tmp_path):
    paths = FilesystemKnowledge(tmp_path).consolidate(
        {
            "definitions": [{"capability": "reports", "content": "# Reports"}],
            "glossary": [{"term": "Report", "definition": "A CSV.", "avoid": ""}],
        }
    )
    assert paths == [tmp_path / "docs/product/reports.md", tmp_path / "CONTEXT.md"]
    assert (tmp_path / "docs/product/reports.md").read_text() == "# Reports\n"
    assert "**Report**:\nA CSV.\n" in (tmp_path / "CONTEXT.md").read_text()


def test_consolidate_checks_everything_before_writing_anything(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        FilesystemKnowledge(tmp_path).consolidate(
            {
                "definitions": [
                    {"capability": "reports", "content": "x"},
                    {"capability": "Bad Slug", "content": "y"},
                ],
                "glossary": [],
            }
        )
    assert not (tmp_path / "docs/product").exists()


def test_consolidate_restores_what_was_there_when_a_write_fails(tmp_path):
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/reports.md").write_text("# Reports\n\nOld.\n")
    (tmp_path / "CONTEXT.md").mkdir()  # the glossary write will fail: it is a directory
    with pytest.raises(OSError):
        FilesystemKnowledge(tmp_path).consolidate(
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


MAP = "# Context Map\n\n## Contexts\n\n- [Proceso](./core/process/CONTEXT.md): the roles.\n"
PROCESS = "# Proceso\n\n## Language\n\n**Rol**:\nAn agent.\n_Campo_: role — CrewAI\n"


def mapped(tmp_path):
    (tmp_path / "CONTEXT-MAP.md").write_text(MAP)
    (tmp_path / "core" / "process").mkdir(parents=True)
    (tmp_path / "core" / "process" / "CONTEXT.md").write_text(PROCESS)
    return FilesystemKnowledge(tmp_path)


def test_read_follows_the_map_and_sends_it_first(tmp_path):
    text = mapped(tmp_path).read()
    assert text.index("CONTEXT-MAP.md") < text.index("core/process/CONTEXT.md")
    assert "An agent." in text


def test_a_term_goes_to_the_glossary_of_the_context_it_belongs_to(tmp_path):
    knowledge_ = mapped(tmp_path)
    written = knowledge_.upsert_terms(
        {"Ola": {"definition": "A wave.", "avoid": "", "context": "process"}}
    )
    assert written == tmp_path / "core" / "process" / "CONTEXT.md"
    assert "**Ola**" in (tmp_path / "core" / "process" / "CONTEXT.md").read_text()


def test_a_rewrite_keeps_the_lines_it_does_not_own(tmp_path):
    knowledge_ = mapped(tmp_path)
    knowledge_.upsert_terms(
        {"Rol": {"definition": "An agent with one job.", "avoid": "", "context": "process"}}
    )
    text = (tmp_path / "core" / "process" / "CONTEXT.md").read_text()
    assert "An agent with one job." in text and "_Campo_: role — CrewAI" in text


def test_a_term_for_a_context_the_map_does_not_know_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="billing"):
        mapped(tmp_path).upsert_terms({"X": {"definition": "d", "avoid": "", "context": "billing"}})


def test_a_mapped_product_needs_the_context_of_every_term(tmp_path):
    with pytest.raises(ValueError, match="multi-context"):
        mapped(tmp_path).upsert_terms({"X": {"definition": "d", "avoid": "", "context": ""}})


def test_one_consolidation_writes_every_context_the_task_touched(tmp_path):
    knowledge_ = mapped(tmp_path)
    (tmp_path / "core" / "measurement").mkdir(parents=True)
    (tmp_path / "core" / "measurement" / "CONTEXT.md").write_text("# M\n\n## Language\n")
    (tmp_path / "CONTEXT-MAP.md").write_text(
        MAP + "- [Medición](./core/measurement/CONTEXT.md): the metrics.\n"
    )
    knowledge_.upsert_terms(
        {
            "Ola": {"definition": "A wave.", "avoid": "", "context": "process"},
            "Muestra": {"definition": "A sample.", "avoid": "", "context": "measurement"},
        }
    )
    assert "**Ola**" in (tmp_path / "core" / "process" / "CONTEXT.md").read_text()
    assert "**Muestra**" in (tmp_path / "core" / "measurement" / "CONTEXT.md").read_text()


def test_index_files_hold_only_the_files_of_the_families_asked_for(tmp_path):
    _product(tmp_path)
    knowledge = FilesystemKnowledge(tmp_path)
    product = knowledge.index_files(None, ("product",))
    assert set(product) == {".agentic/index/product.md", ".agentic/index/adr.md"}
    engineering = knowledge.index_files(None, ("engineering",))
    assert ".agentic/index/standards.md" in engineering
    assert ".agentic/index/standards/01-hexagonal.md" in engineering
    assert not {".agentic/index/product.md", ".agentic/index/adr.md"} & set(engineering)
    assert not any(path.startswith(".agentic/index/standards") for path in product)
    assert knowledge.index_files(None, ()) == {}
    # Both together are what the one-family builds add up to.
    assert knowledge.index_files(None, FAMILIES) == product | engineering


def test_design_is_a_family_that_contributes_no_index_file(tmp_path):
    _product(tmp_path)
    (tmp_path / "DESIGN_STANDARDS.md").write_text("# Design\n\n## Icons\n\nText beside.\n")
    knowledge = FilesystemKnowledge(tmp_path)
    assert knowledge.index_files(None, ("design",)) == {}
    assert knowledge.index_files(None, ("product", "engineering", "design")) == (
        knowledge.index_files(None, FAMILIES)
    )
    assert "design" not in knowledge.read().lower()


def test_design_is_empty_without_the_file_and_fenced_whole_with_it(tmp_path):
    knowledge = FilesystemKnowledge(tmp_path)
    assert knowledge.design() == ""
    text = "# Design\n\n## Icons\n\nText beside.\n"
    (tmp_path / "DESIGN_STANDARDS.md").write_text(text)
    assert knowledge.design() == (
        "## Design standards\n\n`DESIGN_STANDARDS.md`:\n\n````markdown\n" + text.strip() + "\n````"
    )


def test_design_reads_from_the_given_root(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "DESIGN_STANDARDS.md").write_text("Tabs.\n")
    knowledge = FilesystemKnowledge(tmp_path)
    assert knowledge.design() == ""
    assert "Tabs." in knowledge.design(checkout)


def test_over_cap_reports_the_documents_past_300_lines_and_none_at_300(tmp_path):
    knowledge = FilesystemKnowledge(tmp_path)
    at_cap = "x\n" * 300
    assert knowledge.over_cap({"CODING_STANDARDS.md": at_cap}) == []
    messages = knowledge.over_cap(
        {"CODING_STANDARDS.md": at_cap, "DESIGN_STANDARDS.md": "x\n" * 301}
    )
    assert len(messages) == 1 and "DESIGN_STANDARDS.md" in messages[0] and "301" in messages[0]


def test_criteria_files_are_the_knowledge_constants(tmp_path):
    from agentic.core.knowledge.domain import knowledge as domain

    assert FilesystemKnowledge(tmp_path).criteria_files() == (
        domain.STANDARDS,
        domain.DESIGN_STANDARDS,
        domain.WRITING_STANDARDS,
    )


def test_writing_is_empty_without_the_file_and_fenced_whole_with_it(tmp_path):
    knowledge = FilesystemKnowledge(tmp_path)
    assert knowledge.writing() == ""
    text = "# Writing\n\n## Voice\n\nPlain.\n"
    (tmp_path / "WRITING_STANDARDS.md").write_text(text)
    assert knowledge.writing() == (
        "## Writing standards\n\n`WRITING_STANDARDS.md`:\n\n````markdown\n"
        + text.strip()
        + "\n````"
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "WRITING_STANDARDS.md").write_text("Tabs.\n")
    assert "Tabs." in knowledge.writing(checkout)


def test_writing_is_a_family_that_contributes_no_index_file(tmp_path):
    _product(tmp_path)
    (tmp_path / "WRITING_STANDARDS.md").write_text("# Writing\n\n## Voice\n\nPlain.\n")
    knowledge = FilesystemKnowledge(tmp_path)
    assert knowledge.index_files(None, ("writing",)) == {}
    assert knowledge.index_files(None, ("product", "engineering", "writing")) == (
        knowledge.index_files(None, FAMILIES)
    )
    assert "writing standards" not in knowledge.read().lower()


def test_the_line_cap_measures_the_writing_standards_too(tmp_path):
    from agentic.core.knowledge.domain import knowledge as domain

    knowledge = FilesystemKnowledge(tmp_path)
    assert domain.WRITING_STANDARDS in knowledge.criteria_files()
    messages = knowledge.over_cap({"WRITING_STANDARDS.md": "x\n" * 301})
    assert len(messages) == 1 and "WRITING_STANDARDS.md" in messages[0]


def test_the_default_standards_are_the_text_of_construction(tmp_path, monkeypatch):
    default = tmp_path / "default.md"
    default.write_text("## Always\n\nOriginal default.\n", encoding="utf-8")
    monkeypatch.setattr(knowledge_domain, "DEFAULT_STANDARDS", default)
    adapter = FilesystemKnowledge(tmp_path / "product")
    default.write_text("## Always\n\nRewritten after the ports were built.\n", encoding="utf-8")
    assert "Original default." in adapter.standards()
    assert "Rewritten" not in adapter.standards()


def test_an_unreadable_default_does_not_stop_the_construction_and_fails_the_read(
    tmp_path, monkeypatch
):
    default = tmp_path / "default.md"
    default.write_bytes(b"\xff\xfe")
    monkeypatch.setattr(knowledge_domain, "DEFAULT_STANDARDS", default)
    adapter = FilesystemKnowledge(tmp_path / "product")
    with pytest.raises(UnicodeDecodeError):
        adapter.standards()


# --- GH-431: the owner's decisions and the orchestration rules ----------------------------

RULE = "- D-bbbbbbbb: Una parte se construye entera. (2026-10-20, issue #2)\n"
OLD = "- D-aaaaaaaa: Se parte sobre 150 USD. (2026-10-02, issue #1) \n"


def _owner_product(tmp_path):
    (tmp_path / "docs" / "product").mkdir(parents=True)
    (tmp_path / "docs" / "product" / "decisiones-del-dueño.md").write_text(
        "# Decisiones\n\n" + OLD + RULE.rstrip() + " Reemplaza: D-aaaaaaaa\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "product" / "check-ins.md").write_text("# Check-ins\n", encoding="utf-8")
    (tmp_path / "docs" / "orquestacion.md").write_text("# Orquestación\n", encoding="utf-8")
    return FilesystemKnowledge(tmp_path)


def test_read_gives_the_live_decisions_whole_and_lists_the_orchestration_rules(tmp_path):
    knowledge = _owner_product(tmp_path)
    text = knowledge.read()
    assert "Una parte se construye entera" in text and "sobre 150 USD" not in text
    assert "owner.md" in text
    files = knowledge.index_files(None, ["product"])
    assert "docs/orquestacion.md" in files[".agentic/index/owner.md"]
    assert "decisiones" not in files[".agentic/index/product.md"]


def test_the_decisions_of_the_given_root_are_the_ones_read(tmp_path):
    knowledge = _owner_product(tmp_path)
    checkout = tmp_path / "checkout"
    (checkout / "docs").mkdir(parents=True)
    (checkout / "docs" / "decisiones-del-dueño.md").write_text(
        "# S\n" + RULE.replace("bbbbbbbb", "cccccccc"), encoding="utf-8"
    )
    text = knowledge.read(checkout)
    assert "D-cccccccc" in text and "D-bbbbbbbb" not in text


@pytest.mark.parametrize("slug", ["decisiones-del-dueño", "../decisiones-del-dueño"])
def test_consolidate_refuses_a_definition_that_resolves_to_an_owner_decisions_file(tmp_path, slug):
    knowledge = _owner_product(tmp_path)
    before = (tmp_path / "docs" / "product" / "decisiones-del-dueño.md").read_text()
    verdict = {
        "definitions": [
            {"capability": "check-ins", "content": "## Jobs\n"},
            {"capability": unicodedata.normalize("NFD", slug), "content": "## Jobs\n\nx\n"},
        ],
        "glossary": [],
    }
    with pytest.raises(ValueError, match="owner"):
        knowledge.consolidate(verdict)
    assert (tmp_path / "docs" / "product" / "decisiones-del-dueño.md").read_text() == before
    assert (tmp_path / "docs" / "product" / "check-ins.md").read_text() == "# Check-ins\n"


def test_owner_over_cap_counts_the_live_lines_and_none_at_the_cap(tmp_path):
    knowledge = FilesystemKnowledge(tmp_path)
    line = "- D-{:08x}: regla\n"
    at_cap = "".join(line.format(n) for n in range(120))
    over = at_cap + line.format(999)
    assert knowledge.owner_over_cap({"docs/decisiones-del-dueño.md": at_cap}) == []
    (message,) = knowledge.owner_over_cap({"docs/decisiones-del-dueño.md": over})
    assert "docs/decisiones-del-dueño.md" in message and "121" in message and "120" in message
    # Struck and replaced entries are not counted: only what a role reads.
    struck = "".join(f"- ~~D-{n:08x}: regla~~\n" for n in range(200))
    assert knowledge.owner_over_cap({"docs/decisiones-del-dueño.md": struck + at_cap}) == []


def test_owner_files_are_the_knowledge_constants(tmp_path):
    from agentic.core.knowledge.domain import knowledge as domain

    assert FilesystemKnowledge(tmp_path).owner_files() == domain.OWNER_DECISIONS
