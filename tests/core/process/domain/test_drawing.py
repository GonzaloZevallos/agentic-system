import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentic.core.process.domain import drawing as drawing_module
from agentic.core.process.domain.design import Capture, Design
from agentic.core.process.domain.drawing import (
    DEPARTURE_ESCALATIONS_MAX,
    DRAWING_RULE,
    DRAWING_SEATS,
    Departure,
    Verdicts,
    answers,
    departure_key,
    departure_question,
    flag_verdict,
    flag_verdicts,
    interview_check,
    interviewer_check,
    normalize_drawing,
    question_numbers,
    question_ref,
    row_is_open,
    rule_for,
    screens_due,
    unverified_question,
    verdicts,
)


def test_a_declared_design_has_the_rule():
    design = Design(prototype="pantallas", entry="Home", sample_app=None, compare=None)
    assert rule_for(design, "tech_lead") == DRAWING_RULE
    assert "owner" in DRAWING_RULE and "never by a role" in DRAWING_RULE


def test_no_design_has_no_rule():
    assert rule_for(None, "tech_lead") == ""


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


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("**Q1** one\n\n**Q2** two", {1, 2}),
        ("  \u2753 **Q3** a\n\u2753\ufe0f**Q4** b", {3, 4}),
        ("**Q1** see **Q3** below", {1}),
        ("**Q1** a\n**Q1** again", {1}),
        ("**Q10** ten", {10}),
        ("```\n**Q1** in a fence\n```\n**Q2** out", {2}),
        ("~~~\n**Q1**\n~~~", set()),
        ("> **Q1** quoted\n**Q2** real", {2}),
        ("**Q0** zero\n**Q1234567** long\nQ1 plain", set()),
        ("text before **Q1**", set()),
    ],
)
def test_question_numbers_reads_only_the_heads_of_questions(text, found):
    assert question_numbers(text) == found


def test_question_ref_builds_the_ref():
    assert question_ref(7) == "Q7"


@pytest.mark.parametrize(
    "line", ["Drawing 0: accept", "Drawing 1234567: accept", "Drawing 1: maybe", "Drawing 1 accept"]
)
def test_a_line_that_is_not_the_grammar_gives_no_verdict(line):
    assert verdicts(line) == Verdicts(given={}, contradicted=frozenset())


def test_a_drawing_shaped_line_is_not_a_question_head():
    assert question_numbers("Drawing 1: maybe\n**Q2** x") == {2}


def test_unverified_question_names_the_ref_and_the_drawing():
    line = unverified_question("Q2", "pantallas/Home.png")
    assert "Q2" in line and "pantallas/Home.png" in line and "\n" not in line
    assert "Q3" in unverified_question("Q3", None)


TEXT = "**Q1** one?\nrecommendation\n\n**Q2** two?"


def test_the_interviewer_check_accepts_a_round_without_visible_questions():
    assert interviewer_check([], True, "") is None
    assert interviewer_check([], False, TEXT) is None
    assert interviewer_check([1, 2], False, TEXT) is None


def test_the_interviewer_check_rejects_a_number_that_is_not_a_question_of_the_round():
    reason = interviewer_check([5], False, TEXT)
    assert reason is not None and "Q5" in reason


def test_the_interviewer_check_rejects_visible_with_done():
    assert interviewer_check([1], True, TEXT) is not None


def test_the_interviewer_check_rejects_visible_without_any_question_head():
    assert interviewer_check([1], False, "questions without markers, see **Q1**") is not None


@dataclass(frozen=True)
class Chk:
    subject: str
    ref: str
    verdict: str
    drawing: str | None = None
    element: str | None = None


@dataclass(frozen=True)
class Dep:
    drawing: str
    element: str


DESIGN = Design(
    prototype="pantallas",
    entry="Home",
    sample_app=None,
    compare=None,
    captures=(Capture(image="capturas/Login.png", screen="Login"),),
)
HOME = "pantallas/Home.png"
KEY = "pantallas/Home.png:title"


def _check(visible=(1,), checks=None, departures=(), rejected=None, must_match=None):
    if checks is None:
        checks = [Chk("question", "Q1", "matches", HOME, "Title")]
    return interview_check(visible, checks, departures, DESIGN, rejected or {}, must_match or {})


def test_a_round_with_a_check_per_visible_question_passes():
    assert _check() is None
    assert _check(visible=()) is None


def test_a_visible_question_without_a_check_is_rejected():
    reason = _check(visible=(1, 2))
    assert reason is not None and "Q2" in reason
    assert _check(checks=[Chk("screen", "Q1", "matches", HOME, "Title")]) is not None


def test_unverified_covers_a_visible_question_without_a_drawing():
    assert _check(checks=[Chk("question", "Q1", "unverified")]) is None


def test_departs_needs_its_departure_and_a_departure_needs_its_departs():
    departs = Chk("question", "Q1", "departs", HOME, "Title")
    reason = _check(checks=[departs])
    assert reason is not None and "Q1" in reason
    assert _check(checks=[departs], departures=[Dep(HOME, "Title")]) is None
    assert _check(checks=[departs], departures=[Dep(HOME, " TITLE ")]) is None
    assert _check(departures=[Dep(HOME, "Title")]) is not None
    assert _check(checks=[departs], departures=[Dep(HOME, "Other")]) is not None


def test_a_check_that_says_departs_or_matches_without_a_drawing_is_rejected():
    assert _check(checks=[Chk("question", "Q1", "departs")]) is not None
    assert _check(checks=[Chk("question", "Q1", "matches", HOME)]) is not None


def test_a_drawing_the_design_does_not_declare_is_rejected_whatever_the_verdict():
    for verdict in ("matches", "departs", "unverified"):
        reason = _check(checks=[Chk("question", "Q1", verdict, "otra/Home.png", "Title")])
        assert reason is not None and "Q1" in reason
    assert _check(checks=[Chk("question", "Q1", "matches", "capturas/Login.png", "Logo")]) is None
    assert _check(checks=[Chk("question", "Q1", "matches", "./pantallas/a/b.png", "x")]) is None
    assert _check(departures=[Dep("otra/x.png", "Title")]) is not None


def test_an_invalid_drawing_path_is_rejected_without_the_exception_text():
    reason = _check(checks=[Chk("question", "Q1", "matches", "../x.png", "Title")])
    assert reason is not None and "Q1" in reason and "inside the design" not in reason
    assert _check(departures=[Dep("/abs.png", "Title")]) is not None


def test_a_rejected_key_that_returns_as_departs_is_rejected_with_its_number():
    departs = Chk("question", "Q1", "departs", HOME, "Title")
    reason = _check(checks=[departs], departures=[Dep(HOME, "Title")], rejected={KEY: 4})
    assert reason is not None and "Drawing #4" in reason and KEY not in reason
    assert _check(rejected={KEY: 4}) is None


def test_a_must_match_key_needs_a_matches_check():
    assert _check(must_match={KEY: 2}) is None
    reason = _check(checks=[Chk("question", "Q1", "matches", HOME, "Other")], must_match={KEY: 2})
    assert reason is not None and "Drawing #2" in reason and KEY not in reason
    assert (
        _check(
            checks=[Chk("screen", "home", "matches", HOME, "TITLE")],
            visible=(),
            must_match={KEY: 2},
        )
        is None
    )


def test_unverified_and_departs_never_satisfy_a_must_match():
    unverified = Chk("question", "Q1", "unverified", HOME, "Title")
    assert _check(checks=[unverified], must_match={KEY: 2}) is not None
    departs = Chk("question", "Q1", "departs", HOME, "Title")
    assert (
        _check(checks=[departs], departures=[Dep(HOME, "Title")], must_match={KEY: 2}) is not None
    )


def test_no_reason_contains_a_key():
    token = "zzkeyzz"
    key = f"{HOME}:{token}"
    departs = Chk("question", "Q1", "departs", HOME, token)
    cases = [
        _check(checks=[departs]),
        _check(departures=[Dep(HOME, token)]),
        _check(checks=[Chk("question", "Q1", "matches", HOME, token)], visible=(1, 2)),
        _check(checks=[Chk("question", "Q1", "matches", "otra/a.png", token)]),
        _check(checks=[Chk("question", "Q1", "matches", "../a.png", token)]),
        _check(checks=[Chk("question", "Q1", "matches", None, token)]),
        _check(departures=[Dep("otra/a.png", token)]),
        _check(departures=[Dep("../a.png", token)]),
        _check(checks=[departs], departures=[Dep(HOME, token)], rejected={key: 3}),
        _check(checks=[Chk("question", "Q1", "matches", HOME, "Title")], must_match={key: 5}),
        _check(checks=[Chk("question", "Q1", "matches", HOME, " ")]),
    ]
    for reason in cases:
        assert reason is not None and token not in reason


def test_the_schema_classes_fit_the_protocols():
    from agentic.core.process.domain.schemas import DrawingCheck, DrawingDeparture

    check = DrawingCheck(subject="question", ref="Q1", verdict="departs", drawing=HOME, element="T")
    departure = DrawingDeparture(
        drawing=HOME,
        element="T",
        shows="a",
        would_ship="b",
        recommendation="c",
        reason="d",
        raised_by="e",
    )
    assert interview_check([1], [check], [departure], DESIGN, {}, {}) is None


_DESIGN = Design(prototype="pantallas", entry="Home", sample_app=None, compare=None)


def test_the_seats_table_has_exactly_the_two_definition_interview_seats():
    assert dict(DRAWING_SEATS) == {
        "product_analyst_interviewer": "interviewer",
        "pm_interviewee": "interviewee",
    }


def test_the_rule_has_one_line_variant_per_seat_and_the_base_for_any_other_slug():
    interviewer = rule_for(_DESIGN, "product_analyst_interviewer")
    interviewee = rule_for(_DESIGN, "pm_interviewee")
    assert interviewer.startswith(DRAWING_RULE) and "`visible`" in interviewer
    assert interviewee.startswith(DRAWING_RULE) and "`departures`" in interviewee
    assert "assumption" in interviewee
    for line in (interviewer, interviewee):
        assert "\n" not in line
    assert rule_for(_DESIGN, "tech_lead") == DRAWING_RULE
    assert rule_for(_DESIGN, "pm_consultant") == DRAWING_RULE


def test_without_a_design_no_slug_has_a_rule():
    for slug in (*DRAWING_SEATS, "tech_lead"):
        assert rule_for(None, slug) == ""
