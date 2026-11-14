from agentic.core.process.domain.design import Design
from agentic.core.process.domain.drawing import DRAWING_RULE, rule_for


def test_a_declared_design_has_the_rule():
    design = Design(prototype="pantallas", entry="Home", sample_app=None, compare=None)
    assert rule_for(design) == DRAWING_RULE
    assert "owner" in DRAWING_RULE and "never by a role" in DRAWING_RULE


def test_no_design_has_no_rule():
    assert rule_for(None) == ""
