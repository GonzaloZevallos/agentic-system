import pytest

from agentic.core.process.domain.design import Design
from agentic.core.process.domain.drawing import (
    DRAWING_RULE,
    Departure,
    departure_key,
    departure_question,
    normalize_drawing,
    rule_for,
)


def test_a_declared_design_has_the_rule():
    design = Design(prototype="pantallas", entry="Home", sample_app=None, compare=None)
    assert rule_for(design) == DRAWING_RULE
    assert "owner" in DRAWING_RULE and "never by a role" in DRAWING_RULE


def test_no_design_has_no_rule():
    assert rule_for(None) == ""


def _departure(**over: str) -> Departure:
    fields = {
        "drawing": "pantallas/Home.png",
        "element": "Title",
        "shows": "a blue header",
        "would_ship": "a green header",
        "recommendation": "keep the blue header",
        "reason": "it matches the brand",
        "raised_by": "ux_designer",
    }
    return Departure(**{**fields, **over})


def test_the_key_joins_the_normalized_path_and_the_folded_element():
    assert departure_key("./a/../x.png", "  Main   TITLE ") == "x.png:main title"


def test_the_key_is_injective_over_path_and_element():
    assert departure_key("./x.png", "t") == departure_key("x.png", "t")
    assert departure_key("X.png", "t") != departure_key("x.png", "t")
    assert departure_key("x.png", "Title ") == departure_key("x.png", "title")
    with pytest.raises(ValueError):
        departure_key("a:b", "c")
    assert departure_key("a", "b:c") != departure_key("a", "b")


@pytest.mark.parametrize(
    "path", ["", "   ", "a:b", "/abs/x.png", "..", "../x.png", "a/../../x.png", "."]
)
def test_a_bad_path_is_refused(path):
    with pytest.raises(ValueError):
        normalize_drawing(path)


def test_a_numeric_suffix_is_refused_by_name():
    with pytest.raises(ValueError, match=":12"):
        departure_key("x.png:12", "title")


def test_an_empty_element_is_refused():
    with pytest.raises(ValueError):
        departure_key("x.png", "  \t ")


def test_the_question_quotes_the_path_and_never_the_key():
    d = _departure(drawing="./pantallas/Home.png")
    text = departure_question(d, 7)
    for part in (
        "Departure #7",
        "./pantallas/Home.png",
        d.shows,
        d.would_ship,
        d.recommendation,
        d.reason,
    ):
        assert part in text
    assert departure_key(d.drawing, d.element) not in text
