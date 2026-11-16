import ast
from pathlib import Path

import pytest

from agentic.core.process.domain import drawing as drawing_module
from agentic.core.process.domain.design import Capture, Design
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
    row_is_open,
    rule_for,
    screens_due,
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


def _forbidden_imports(source: str) -> set[str]:
    """The names `task` or `rules` that `source` imports anywhere, deferred imports included."""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            imported.add(base)
            imported |= {f"{base}.{a.name}" for a in node.names}
    return {name.rsplit(".", 1)[-1] for name in imported} & {"task", "rules"}


def test_drawing_is_a_leaf_that_imports_neither_task_nor_rules():
    assert not _forbidden_imports(Path(drawing_module.__file__).read_text())


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("def f():\n    from .task import TaskState\n", {"task"}),
        ("from . import rules\n", {"rules"}),
        ("import agentic.core.process.domain.task\n", {"task"}),
        ("from agentic.core.process.domain.rules import escalation\n", {"rules"}),
        ("from .design import Design\n", set()),
    ],
)
def test_the_leaf_check_rejects_a_forbidden_import_wherever_it_sits(source, found):
    assert _forbidden_imports(source) == found


def _design(*captures: Capture) -> Design:
    return Design(
        prototype="pantallas", entry="Home", sample_app=None, compare=None, captures=captures
    )


def test_screens_due_returns_the_captures_whose_paths_match_in_declared_order():
    home = Capture("design/home.png", "Home", ("src/home/*.py",))
    cart = Capture("design/cart.png", "Cart", ("src/cart/*.py",))
    admin = Capture("design/admin.png", "Admin", ("src/admin/*.py",))
    due = screens_due(_design(home, cart, admin), ["src/admin/a.py", "src/home/b.py", "README.md"])
    assert due == [home, admin]


def test_a_capture_without_paths_is_always_due():
    always = Capture("design/home.png", "Home")
    assert screens_due(_design(always), []) == [always]
    assert screens_due(_design(always), ["anything.txt"]) == [always]


def test_a_capture_with_paths_is_not_due_when_nothing_matches_or_nothing_changed():
    home = Capture("design/home.png", "Home", ("src/home/*.py",))
    assert screens_due(_design(home), ["src/cart/a.py"]) == []
    assert screens_due(_design(home), []) == []


def test_two_captures_of_the_same_screen_are_two_screens_due():
    one = Capture("design/home-1.png", "Home", ("src/*.py",))
    two = Capture("design/home-2.png", "Home", ("src/*.py",))
    assert screens_due(_design(one, two), ["src/a.py"]) == [one, two]


def test_a_star_crosses_directories():
    deep = Capture("design/home.png", "Home", ("src/*.py",))
    assert screens_due(_design(deep), ["src/a/b.py"]) == [deep]


def test_a_deleted_path_still_matches_textually():
    home = Capture("design/home.png", "Home", ("src/home/*.py",))
    assert screens_due(_design(home), ["src/home/removed.py"]) == [home]


def test_matching_is_case_sensitive():
    home = Capture("design/home.png", "Home", ("src/Home/*.py",))
    assert screens_due(_design(home), ["src/home/a.py"]) == []


def test_screens_due_without_captures_is_empty():
    assert screens_due(_design(), ["src/a.py"]) == []


@pytest.mark.parametrize(
    ("verdict", "settled", "expected"),
    [
        ("matches", False, False),
        ("matches", True, False),
        ("departs", True, False),
        ("departs", False, True),
        ("unverified", False, True),
        ("unverified", True, True),
        ("invented", False, True),
        ("invented", True, True),
    ],
)
def test_row_is_open_only_for_matches_and_settled_departs_is_closed(
    verdict: str, settled: bool, expected: bool
):
    assert row_is_open(verdict, settled) is expected


def _package_imports(source: str) -> set[str]:
    """Every import of `source`, deferred ones included, that reaches the package itself:
    a relative one, or one under `agentic`."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (
            node.level or (node.module or "").startswith("agentic")
        ):
            base = "." * node.level + (node.module or "")
            found |= {base if node.module else f"{base}{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("agentic")}
    return found


def test_drawing_imports_only_design_from_the_package():
    assert _package_imports(Path(drawing_module.__file__).read_text()) == {".design"}


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import DrawingCheck\n",
        "from . import schemas\n",
        "def f():\n    from .rules import x\n",
        "import agentic.core.process.domain.task\n",
        "from agentic.core.process.domain import drawing_answer\n",
    ],
)
def test_the_package_import_check_rejects_anything_but_design(source: str):
    assert _package_imports(source) != {".design"}
