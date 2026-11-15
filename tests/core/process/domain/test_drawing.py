import ast
from pathlib import Path

import pytest

from agentic.core.process.domain import drawing as drawing_module
from agentic.core.process.domain.design import Design
from agentic.core.process.domain.drawing import (
    DEPARTURE_ESCALATIONS_MAX,
    DRAWING_RULE,
    Departure,
    Verdicts,
    answers,
    departure_key,
    departure_question,
    flag_verdict,
    flag_verdicts,
    normalize_drawing,
    rule_for,
    verdicts,
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
    assert text.endswith('Answer with a line "Drawing 7: accept" or "Drawing 7: reject".')


def test_the_cap_is_three_rounds():
    assert DEPARTURE_ESCALATIONS_MAX == 3


@pytest.mark.parametrize(
    ("text", "given"),
    [
        ("Drawing 3: accept", {3: "accept"}),
        ("Drawing 3: reject", {3: "reject"}),
        ("DRAWING 3: ACCEPT\n", {3: "accept"}),
        ("drawing 12: Reject  ", {12: "reject"}),
        ("thanks\nDrawing 1: accept\nDrawing 2: reject\nbye", {1: "accept", 2: "reject"}),
        ("    Drawing 4: accept", {4: "accept"}),
        ("Drawing 007: accept", {7: "accept"}),
        ("Drawing 3: accept\nDrawing 3: accept", {3: "accept"}),
        ("Drawing 0: accept", {}),
        ("> Drawing 3: accept", {}),
        ("  >Drawing 3: accept", {}),
        ("```\nDrawing 3: accept\n```", {}),
        ("~~~\nDrawing 3: accept\n~~~", {}),
        ("```text\nDrawing 3: accept\n```\nDrawing 4: reject", {4: "reject"}),
        ("```\nDrawing 3: accept\n~~~\nDrawing 4: reject\n```", {}),
        ("Drawing \u0661: accept", {}),
        ("Drawing " + "9" * 30 + ": accept", {}),
        ("Drawing 1234567: accept", {}),
        ("Drawing 3: accept please", {}),
        ("so Drawing 3: accept", {}),
        ("Drawing 3 accept", {}),
    ],
)
def test_verdicts_reads_whole_lines_only(text, given):
    got = verdicts(text)
    assert dict(got.given) == given
    assert got.contradicted == frozenset()


def test_two_verdicts_for_one_number_in_one_comment_contradict():
    got = verdicts("Drawing 3: accept\nDrawing 3: reject\nDrawing 4: accept")
    assert dict(got.given) == {4: "accept"}
    assert got.contradicted == frozenset({3})


def test_answers_takes_the_last_line_across_comments():
    got = answers(["Drawing 1: accept\nDrawing 2: accept", "Drawing 1: reject"])
    assert dict(got.given) == {1: "reject", 2: "accept"}
    assert got.contradicted == frozenset()


def test_a_later_contradiction_drops_the_earlier_verdict_and_a_clean_one_clears_it():
    contradiction = "Drawing 1: accept\nDrawing 1: reject"
    dropped = answers(["Drawing 1: accept", contradiction])
    assert dropped == Verdicts(given={}, contradicted=frozenset({1}))
    cleared = answers(["Drawing 1: accept", contradiction, "Drawing 1: reject"])
    assert cleared == Verdicts(given={1: "reject"}, contradicted=frozenset())


def test_answers_does_not_raise_on_nonsense():
    assert answers([]) == Verdicts(given={}, contradicted=frozenset())
    assert answers(["Drawing " + "7" * 30 + ": accept", ""]).given == {}


@pytest.mark.parametrize(
    ("raw", "pair"), [("1=accept", (1, "accept")), ("12=reject", (12, "reject"))]
)
def test_flag_verdict_parses(raw, pair):
    assert flag_verdict(raw) == pair


@pytest.mark.parametrize("raw", ["1=maybe", "x=accept", "1", "0=accept", "=accept", "1=accept=1"])
def test_flag_verdict_refuses_anything_else(raw):
    with pytest.raises(ValueError):
        flag_verdict(raw)


def test_flag_verdicts_folds_and_refuses_a_conflict():
    assert flag_verdicts(["1=accept", "2=reject", "1=accept"]) == {1: "accept", 2: "reject"}
    with pytest.raises(ValueError):
        flag_verdicts(["1=accept", "1=reject"])


def test_drawing_is_a_leaf_that_imports_neither_task_nor_rules():
    tree = ast.parse(Path(drawing_module.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            imported.add(base)
            imported |= {f"{base}.{a.name}" for a in node.names}
    leaves = {name.rsplit(".", 1)[-1] for name in imported}
    assert not leaves & {"task", "rules"}
