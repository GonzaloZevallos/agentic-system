"""The default standards state practices, not tools (ADR-0012, adenda `standards`): a
product in any stack replaces `CODING_STANDARDS.md` whole and materialises how each
practice is met, so the shipped default must carry no token that only makes sense in
one stack. A golden over the file's bytes would break on every reword with no
behaviour change (§5); this asserts the property the task claims instead."""

from agentic.core.knowledge.domain import knowledge, knowledge_index


def assemble_standards(text: str) -> str:
    return knowledge_index.build_index({}, {}, {}, None, text, standards_whole=True).standards


# A transport, a storage kind, a test runner, a typing habit, a source extension and a
# tool name: the six supposed-Python spellings the inventory found leaking into the
# default. One constant so a later task extends it in a single place.
STACK_SPECIFIC_TOKENS = (
    "HTTP",
    "database",
    "pytest",
    "type hints",
    ".py",
    "mypy",
    "ruff",
)


def test_default_standards_carry_no_stack_specific_token():
    default = knowledge.DEFAULT_STANDARDS.read_text(encoding="utf-8")
    assembled = assemble_standards(default).casefold()
    for token in STACK_SPECIFIC_TOKENS:
        assert token.casefold() not in assembled, (
            f"{token!r} is stack-specific; the shipped default states a practice, "
            "not a tool (ADR-0012)"
        )


def test_the_token_scan_actually_rejects_a_stack_specific_default():
    """The guard above is not vacuous: fed a default that names a tool, it finds the
    tool. Proves the property test would have failed before this task, not only that
    it passes after."""
    assembled = assemble_standards(
        "Behaviour tests at the HTTP seam, over a throwaway database, run with pytest."
    ).casefold()
    found = [token for token in STACK_SPECIFIC_TOKENS if token.casefold() in assembled]
    assert found == ["HTTP", "database", "pytest"]


# GH-157: `## 9. Naming of persisted schema` in the default standards. The property
# test above guards the whole assembled default; this guards one section of it, so the
# assertions below run on the `## 9.` slice alone, never on the whole assembled text
# (that would turn into a golden that breaks on any unrelated reword, which §5 rules
# out).
SECTION_9_HEADING = "## 9. Naming of persisted schema"

# Every rule the section must state, as the exact backticked literal the brief names.
# One tuple so a later task extends it in one place, the same pattern as
# `STACK_SPECIFIC_TOKENS` above.
NAMING_ANCHORS = (
    "`snake_case`",
    "`customer`",
    "`<a>_<b>`",
    "`id`",
    "`<referenced_table>_id`",
    "`is_vip`",
    "`_on`",
    "`_at`",
    "`ix_<table>_<columns>`",
    "`uq_<table>_<columns>`",
)


def _section_9(assembled: str) -> str | None:
    """The `## 9. Naming of persisted schema` slice of assembled standards text: from
    that heading line up to, but not including, the next line starting with `## `, or
    to the end of the text. `None` when the heading is absent, so a caller can tell "no
    section" from "an empty one" without scanning twice."""
    lines = assembled.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines) if line.rstrip("\n") == SECTION_9_HEADING),
        None,
    )
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "".join(lines[start:end])


def _missing_anchors(section: str) -> list[str]:
    return [anchor for anchor in NAMING_ANCHORS if anchor not in section]


def test_default_standards_carry_section_9_with_every_naming_anchor():
    default = knowledge.DEFAULT_STANDARDS.read_text(encoding="utf-8")
    assembled = assemble_standards(default)
    section = _section_9(assembled)
    assert section
    assert _missing_anchors(section) == []


def test_without_section_9_the_anchor_check_finds_no_section():
    """Rejecting case: the default with the whole `## 9.` slice cut out of the
    assembled text. `_section_9` must report the heading absent, not an empty pass."""
    default = knowledge.DEFAULT_STANDARDS.read_text(encoding="utf-8")
    assembled = assemble_standards(default)
    section = _section_9(assembled)
    assert section is not None
    without_section_9 = assembled.replace(section, "")
    assert _section_9(without_section_9) is None


def test_a_section_9_missing_the_index_rule_fails_naming_it():
    """Rejecting case: the heading stays, but the index/unique-constraint rule line is
    gone. The anchor check must fail and name exactly the anchors that line carried."""
    default = knowledge.DEFAULT_STANDARDS.read_text(encoding="utf-8")
    assembled = assemble_standards(default)
    section = _section_9(assembled)
    assert section is not None
    index_line = next(
        line for line in section.splitlines(keepends=True) if "`ix_<table>_<columns>`" in line
    )
    mutilated = assembled.replace(index_line, "")
    mutilated_section = _section_9(mutilated)
    assert mutilated_section is not None
    assert _missing_anchors(mutilated_section) == [
        "`ix_<table>_<columns>`",
        "`uq_<table>_<columns>`",
    ]
