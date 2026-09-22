"""The default standards state practices, not tools (ADR-0012, adenda `standards`): a
product in any stack replaces `CODING_STANDARDS.md` whole and materialises how each
practice is met, so the shipped default must carry no token that only makes sense in
one stack. A golden over the file's bytes would break on every reword with no
behaviour change (§5); this asserts the property the task claims instead."""

from agentic.core.knowledge.domain import knowledge

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
    assembled = knowledge.assemble_standards(default).casefold()
    for token in STACK_SPECIFIC_TOKENS:
        assert token.casefold() not in assembled, (
            f"{token!r} is stack-specific; the shipped default states a practice, "
            "not a tool (ADR-0012)"
        )


def test_the_token_scan_actually_rejects_a_stack_specific_default():
    """The guard above is not vacuous: fed a default that names a tool, it finds the
    tool. Proves the property test would have failed before this task, not only that
    it passes after."""
    assembled = knowledge.assemble_standards(
        "Behaviour tests at the HTTP seam, over a throwaway database, run with pytest."
    ).casefold()
    found = [token for token in STACK_SPECIFIC_TOKENS if token.casefold() in assembled]
    assert found == ["HTTP", "database", "pytest"]
