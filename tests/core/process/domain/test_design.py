"""The optional design block, as a validated `Design` and the rule it hands the leads
(ADR-0040): `design_rules` mirrors `roles.check_rules`, so a lead gets the one `Bash`
prefix the product's own `compare` command needs, nothing wider."""

from agentic.core.process.domain.design import (
    NO_COMPARE_NOTE,
    NO_DESIGN_NOTE,
    NO_SAMPLE_APP_NOTE,
    Design,
    design_rules,
)


def test_no_block_gives_no_rule():
    assert design_rules(None) == ()


def test_a_block_without_compare_gives_no_rule():
    design = Design(prototype="pantallas", entry="Home", sample_app=None, compare=None)
    assert design_rules(design) == ()


def test_a_block_with_compare_gives_the_one_bash_prefix():
    design = Design(prototype="pantallas", entry="Home", sample_app=None, compare="npm run compare")
    assert design_rules(design) == ("Bash(npm run compare:*)",)


def test_the_notes_are_distinct_constants():
    # One line each, for the lead's own section when the product cannot back a claim.
    assert NO_DESIGN_NOTE != NO_COMPARE_NOTE != NO_SAMPLE_APP_NOTE
    assert "no design block" in NO_DESIGN_NOTE
    assert "comparisons" in NO_DESIGN_NOTE and "comparisons" in NO_COMPARE_NOTE
