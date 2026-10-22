"""The two-level index of product knowledge (GH-292): what goes whole, what goes through
the index, and what each clause refuses. Example fixtures only, never the repo's ADRs."""

import pytest

from agentic.core.knowledge.domain import knowledge, knowledge_index
from agentic.core.knowledge.domain.knowledge_index import (
    ALWAYS_MARKER,
    always_whole,
    area_of,
    build_index,
    superseded,
)

BRIEF = "# Brief\n\n## Jobs\n\n- When a member skips, I want a flag.\n"
GLOSSARY = "# Gym\n\n## Language\n\n**Gym**:\nA business.\n"
CONTEXT_MAP = "# Context Map\n\n- [Process](agentic/core/process/CONTEXT.md)\n"
PROCESS_GLOSSARY = "# Process\n\n## Language\n\n**Task**:\nWork.\n"
STANDARDS = f"""# Coding standards

Intro line.

## 1. Hexagonal architecture

Imports point inward.

## 9. Business isolation
{ALWAYS_MARKER}

No business reads another business's rows.

## 2. Strict typing, without silencing

```markdown
## not a section, it is inside a fence
```

Types are checked.
"""
ACCEPTED = "---\nstatus: accepted\ndate: 2026-01-01\n---\n\n# Use a queue\n\nBody.\n"
SUPERSEDED = "---\nstatus: superseded by 0003\n---\n\n# Old queue\n"
NO_FRONTMATTER = "# Plain decision\n\nNo frontmatter at all.\n"


def index(**overrides):
    args = {
        "glossaries": {"CONTEXT.md": GLOSSARY},
        "capabilities": {"check-ins.md": "# Check-ins\n"},
        "adrs": {"0001-queue.md": ACCEPTED},
        "brief": BRIEF,
        "standards": None,
    } | overrides
    return build_index(**args)


def test_every_always_whole_member_is_present_byte_for_byte():
    built = index(
        glossaries={
            "CONTEXT-MAP.md": CONTEXT_MAP,
            "agentic/core/process/CONTEXT.md": PROCESS_GLOSSARY,
        },
        standards=STANDARDS,
    )
    for path, text in {
        "BRIEF.md": BRIEF,
        "CONTEXT-MAP.md": CONTEXT_MAP,
        "agentic/core/process/CONTEXT.md": PROCESS_GLOSSARY,
    }.items():
        assert knowledge.fenced(path, text) in built.always
    marked = (
        "## 9. Business isolation\n"
        + ALWAYS_MARKER
        + "\n\nNo business reads another business's rows."
    )
    assert marked in built.always
    # The Brief first, then the glossaries in the order given, then the marked sections.
    assert built.always.index("`BRIEF.md`") < built.always.index("`CONTEXT-MAP.md`")
    assert built.always.index("`CONTEXT-MAP.md`") < built.always.index(
        "CONTEXT.md`:\n\n````markdown\n# Process"
    )
    assert built.always.index("CONTEXT.md`:\n\n````markdown\n# Process") < built.always.index(
        "Business isolation"
    )


def test_always_whole_is_a_named_list_not_a_size_or_a_name_guess():
    assert always_whole("BRIEF.md") and always_whole("CONTEXT-MAP.md")
    named = ("CONTEXT.md", "agentic/core/process/CONTEXT.md")
    assert all(always_whole(path, glossaries=named) for path in named)
    # Rejected: a `CONTEXT.md` the caller did not name is not whole, whatever its name.
    assert not always_whole("CONTEXT.md")
    assert not always_whole("docs/product/CONTEXT.md", glossaries=named)
    # Rejected: a small file with a suggestive name is not whole; it goes through the index.
    assert not always_whole("docs/product/BRIEF-notes.md")
    assert not always_whole("docs/adr/0001-queue.md")
    assert not always_whole("CODING_STANDARDS.md")


def test_an_unlinked_context_file_is_an_index_entry_and_a_linked_one_is_whole_once():
    stray = "# Stray\n\nNot a mapped glossary.\n"
    built = index(
        glossaries={
            "CONTEXT-MAP.md": CONTEXT_MAP,
            "agentic/core/process/CONTEXT.md": PROCESS_GLOSSARY,
        },
        capabilities={"CONTEXT.md": stray, "check-ins.md": "# Check-ins\n"},
    )
    assert "Not a mapped glossary." not in built.always
    assert "`docs/product/CONTEXT.md` | Stray" in built.files[".agentic/index/product.md"]
    # A glossary the map links that sits under docs/product/ reaches the role once, whole.
    linked = index(
        glossaries={"docs/product/CONTEXT.md": stray},
        capabilities={"CONTEXT.md": stray, "check-ins.md": "# Check-ins\n"},
    )
    assert linked.text.count("Not a mapped glossary.") == 1
    assert "docs/product/CONTEXT.md" not in linked.files[".agentic/index/product.md"]


def test_a_section_is_whole_only_with_the_marker_on_the_line_right_after_its_heading():
    assert always_whole("CODING_STANDARDS.md", f"## Rule\n{ALWAYS_MARKER}\nText")
    # Rejected: marker one line late, marker with trailing words, a non-section.
    assert not always_whole("CODING_STANDARDS.md", f"## Rule\n\n{ALWAYS_MARKER}\nText")
    assert not always_whole("CODING_STANDARDS.md", f"## Rule\n{ALWAYS_MARKER} please\nText")
    assert not always_whole("CODING_STANDARDS.md", f"### Rule\n{ALWAYS_MARKER}\nText")
    assert not always_whole("CODING_STANDARDS.md", "## Rule")


def test_the_marked_section_is_inlined_with_no_file_and_the_unmarked_one_gets_a_file():
    built = index(standards=STANDARDS)
    section_files = {
        p: t for p, t in built.files.items() if p.startswith(".agentic/index/standards/")
    }
    assert sorted(section_files) == [
        ".agentic/index/standards/00-coding-standards.md",
        ".agentic/index/standards/01-hexagonal-architecture.md",
        ".agentic/index/standards/03-strict-typing-without-silencing.md",
    ]
    assert (
        "Imports point inward."
        in section_files[".agentic/index/standards/01-hexagonal-architecture.md"]
    )
    # The fenced `## ` line stayed inside its section.
    assert (
        "inside a fence"
        in section_files[".agentic/index/standards/03-strict-typing-without-silencing.md"]
    )
    assert not any("isolation" in p for p in built.files)
    assert "No business reads another business's rows." in built.always
    # Nothing reaches the role both ways (invariant 1).
    assert "Imports point inward." not in built.always
    assert all("No business reads" not in t for t in built.files.values())
    assert built.standards == ""


def test_standards_entries_point_at_their_own_file_never_at_a_line_range():
    built = index(standards=STANDARDS)
    listing = built.files[".agentic/index/standards.md"]
    assert (
        "`.agentic/index/standards/01-hexagonal-architecture.md` | 1. Hexagonal architecture"
        in listing
    )
    assert "#L" not in listing and "lines " not in listing
    assert "CODING_STANDARDS.md" not in listing


def test_standards_whole_reproduces_the_old_block_byte_for_byte():
    built = index(standards=STANDARDS, standards_whole=True)
    assert built.standards == "## Engineering standards\n\n" + knowledge.fenced(
        "CODING_STANDARDS.md", STANDARDS
    )
    # One mechanism, one way: whole means no standards area, file or inlined section.
    assert "standards" not in built.areas
    assert not any(p.startswith(".agentic/index/standards") for p in built.files)
    assert "Business isolation" not in built.always


def test_a_superseded_adr_has_no_entry_next_to_an_accepted_one_and_one_without_frontmatter():
    built = index(
        adrs={
            "0001-queue.md": ACCEPTED,
            "0002-old.md": SUPERSEDED,
            "0004-plain.md": NO_FRONTMATTER,
        }
    )
    listing = built.files[".agentic/index/adr.md"]
    assert "docs/adr/0001-queue.md" in listing and "docs/adr/0004-plain.md" in listing
    assert "0002-old" not in listing and "Old queue" not in listing
    assert "(2 entries)" in built.areas


def test_superseded_reads_only_the_frontmatter_status():
    assert superseded(SUPERSEDED)
    assert superseded("---\nstatus: Superseded\n---\n# X\n")
    assert not superseded(ACCEPTED)
    assert not superseded(NO_FRONTMATTER)
    # Rejected: prose, an addendum and a body line that mention supersession.
    assert not superseded("---\nstatus: accepted\n---\n# X\n\nstatus: superseded\n")
    assert not superseded("# X\n\nThis decision supersedes 0002 (status: superseded).\n")
    assert not superseded("---\nstatus: accepted (superseded in part by 0005)\n---\n# X\n")
    assert not superseded("---\nstatus: [unclosed\n---\n# X\n")


def test_an_unknown_role_tag_raises_naming_the_file_and_the_slug():
    bad = "---\nstatus: accepted\nroles: [developer, wizard]\n---\n# X\n"
    with pytest.raises(ValueError, match=r"docs/adr/0009-x\.md.*'wizard'"):
        index(adrs={"0009-x.md": bad})
    with pytest.raises(ValueError, match=r"docs/product/odd\.md.*roles"):
        index(capabilities={"odd.md": "---\nroles: developer\n---\n# X\n"})


def test_known_role_tags_reach_the_entry():
    tagged = "---\nroles: [developer, tech_lead]\n---\n# Tagged\n"
    listing = index(capabilities={"tagged.md": tagged}).files[".agentic/index/product.md"]
    assert (
        "`docs/product/tagged.md` | Tagged | area: product | roles: developer, tech_lead" in listing
    )
    assert "roles: all" in index().files[".agentic/index/product.md"]


def test_a_path_outside_the_areas_raises_naming_the_path():
    assert [
        area_of("docs/product/a.md"),
        area_of("docs/adr/b.md"),
        area_of("CODING_STANDARDS.md"),
        area_of("DESIGN_STANDARDS.md"),
    ] == [
        "product",
        "adr",
        "standards",
        "design",
    ]
    for path in (
        "docs/other/a.md",
        "README.md",
        "docs/product",
        "src/CODING_STANDARDS.md",
        "src/DESIGN_STANDARDS.md",
    ):
        with pytest.raises(ValueError, match=path.replace(".", r"\.")):
            area_of(path)
    assert knowledge_index.AREAS == ("product", "adr", "standards", "design")


def test_a_glossary_off_the_always_list_is_refused_not_silently_dropped():
    with pytest.raises(ValueError, match=r"notes\.md"):
        index(glossaries={"notes.md": "# Notes\n"})


def test_the_area_index_names_each_area_file_with_its_count_and_how_to_open_it():
    built = index(capabilities={"a.md": "# A\n", "b.md": "# B\n"}, standards=STANDARDS)
    assert "`.agentic/index/product.md`: product" in built.areas and "(2 entries)" in built.areas
    assert "`.agentic/index/adr.md`: adr" in built.areas
    assert (
        "`.agentic/index/standards.md`: standards" in built.areas and "(3 entries)" in built.areas
    )
    assert "relative to your working directory" in built.areas and "`Read`" in built.areas
    assert built.text.startswith("## Product knowledge\n\n`BRIEF.md`:")
    assert built.text.endswith(built.areas)
    assert built.files[".agentic/index/product.md"].splitlines()[4:] == [
        "- `docs/product/a.md` | A | area: product | roles: all",
        "- `docs/product/b.md` | B | area: product | roles: all",
    ]


def test_entries_never_carry_the_document_text():
    listing = index().files[".agentic/index/adr.md"]
    assert "Body." not in listing and "docs/adr/0001-queue.md" in listing


def test_nothing_to_say_is_empty():
    built = build_index({}, {}, {}, None, None)
    assert built.text == "" and built.files == {} and built.standards == ""


def test_an_area_without_entries_has_no_line_and_no_file():
    built = index(capabilities={}, adrs={"0002-old.md": SUPERSEDED})
    assert built.files == {} and built.areas == ""
    assert built.text.startswith("## Product knowledge\n\n`BRIEF.md`:")


def test_a_marker_inside_an_adr_or_capability_does_not_inline_it():
    """Those files are opened whole through the index; inlining their marked section
    would deliver it twice. The marker is honoured in the standards only."""
    marked = f"# Doc\n\n## Rule\n{ALWAYS_MARKER}\n\nSecret rule.\n"
    built = index(capabilities={"cap.md": marked}, adrs={"0005-x.md": marked})
    assert "Secret rule." not in built.always
    assert "docs/product/cap.md" in built.files[".agentic/index/product.md"]
    assert "docs/adr/0005-x.md" in built.files[".agentic/index/adr.md"]


def test_the_design_area_adds_no_line_and_no_file_to_the_index():
    """Design is served whole (ADR-0054): the area exists in the closed set, but nothing
    ever lists entries under it, so `read()`'s text and files are what they were."""
    built = index()
    assert "design" not in built.areas
    assert not any("design" in path for path in built.files)
    assert "design" in knowledge_index.AREA_SUMMARY
