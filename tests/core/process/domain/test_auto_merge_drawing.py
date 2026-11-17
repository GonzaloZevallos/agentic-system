"""The merge gate of the drawing record (GH-766 part 5): `rules.drawing_merge_facts` is the only
reader of the `drawing_checks` record, `auto_merge_decision` refuses with `drawing_unsettled`
unless the record covers the pull request's head (`head_reviewed`, never `same_commit`) and
nothing is open, and `drawing_body_lines` is the pull request body's one line per open row.
A table, one row per input that provokes it (ADR-0023); without a record, the old chain."""

from dataclasses import replace
from typing import Any, cast

import pytest

from agentic.core.process.domain import landing, rules
from agentic.core.process.domain.rules import MergeDecision, MergeFacts
from agentic.core.process.domain.task import TaskState

HEAD = "a" * 40
CLOSE = "d" * 40
MASTER = "b" * 40
OTHER = "c" * 40
REFUSED = MergeDecision("refuse", "drawing_unsettled")


def _facts(**over: Any) -> MergeFacts:
    base = MergeFacts(
        open_escalation=False,
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("agentic/core/x.py",),
        attempts=1,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
    )
    return replace(base, **over)


def _decide(**over: Any) -> MergeDecision:
    return rules.auto_merge_decision(_facts(**over))


def _drawing(head: str | None = HEAD, open: int = 0) -> dict[str, Any]:
    return {"drawing_checked": True, "drawing_head": head, "drawing_open": open}


def _state(record: Any) -> TaskState:
    return cast(TaskState, {"drawing_checks": record})


def _row(verdict: str, **more: Any) -> dict[str, Any]:
    return {"subject": "Home", "ref": "docs/design/home.png", "verdict": verdict, **more}


def test_no_record_decides_as_today_for_every_other_fact():
    for over in (
        {},
        {"status": "developing"},
        {"reviewed_head": OTHER},
        {"open_blocking": 1},
        {"checks_passed": False},
        {"in_eval_set": True},
    ):
        plain = _facts(**over)
        assert plain.drawing_checked is False
        assert rules.auto_merge_decision(plain) == rules.auto_merge_decision(
            replace(plain, drawing_head=OTHER, drawing_open=4)
        )
    assert _decide() == MergeDecision("merge", None)


def test_the_same_head_with_nothing_open_merges():
    assert _decide(**_drawing()) == MergeDecision("merge", None)


def test_a_valid_close_docs_child_of_the_drawing_head_merges():
    close_docs = {"parent": HEAD, "head": CLOSE, "paths": ["docs/product/home.md"]}
    assert _decide(
        **_drawing(), pr_head=CLOSE, close_docs=close_docs, checks_head=CLOSE
    ) == MergeDecision("merge", None)


def test_a_close_docs_child_that_holds_a_code_path_refuses():
    close_docs = {"parent": HEAD, "head": CLOSE, "paths": ["agentic/core/x.py"]}
    # Tech Lead's review covers the pull request head itself, so only the drawing gate is left.
    assert _decide(
        **_drawing(), pr_head=CLOSE, close_docs=close_docs, checks_head=CLOSE, reviewed_head=CLOSE
    ) == (REFUSED)


DRAWING_REFUSALS = [
    ("moved_head", _drawing(OTHER)),
    ("open_rows", _drawing(HEAD, 1)),
    ("abbreviated_head", _drawing(HEAD[:12])),
    ("no_head", _drawing(None)),
]


@pytest.mark.parametrize(("name", "over"), DRAWING_REFUSALS, ids=[n for n, _ in DRAWING_REFUSALS])
def test_each_drawing_refusal_has_the_input_that_provokes_it(name, over):
    assert _decide(**over) == REFUSED


def test_the_drawing_refusal_comes_right_after_the_performance_one_and_before_the_findings():
    perf = {"perf_convened": True, "perf_verdict": "returned", "perf_head": HEAD}
    assert _decide(**perf, **_drawing(OTHER)).reason == "perf_unverified"
    assert _decide(**_drawing(OTHER), open_blocking=1) == REFUSED
    assert _decide(**_drawing(OTHER), criteria_regressed=None) == REFUSED
    assert _decide(**_drawing(OTHER), reviewed_head=OTHER).reason == "head_not_reviewed"


def test_a_stored_zero_over_an_unverified_row_refuses_through_the_facts():
    record = {"head": HEAD, "rows": [_row("unverified")], "open": 0}
    checked, head, open_rows = rules.drawing_merge_facts(_state(record))
    assert (checked, head, open_rows) == (True, HEAD, 1)
    assert _decide(drawing_checked=checked, drawing_head=head, drawing_open=open_rows) == REFUSED


@pytest.mark.parametrize(
    "record",
    [
        "garbage",
        [],
        {},
        {"head": HEAD, "rows": "x", "open": 0},
        {"head": HEAD, "rows": [], "open": "0"},
        {"head": HEAD, "rows": [], "open": -1},
        {"head": HEAD, "rows": [], "open": True},
        {"head": HEAD, "rows": ["x"], "open": 0},
        {"head": HEAD, "rows": [{"verdict": 3}], "open": 0},
        {"head": 7, "rows": [], "open": 0},
    ],
)
def test_a_malformed_record_reads_as_checked_and_open_and_refuses(record):
    checked, head, open_rows = rules.drawing_merge_facts(_state(record))
    assert checked is True
    # A head that is not a `str` reads as `None`, which no head covers; every other defect
    # makes the open count at least 1.
    assert open_rows >= 1 or head is None
    assert _decide(drawing_checked=checked, drawing_head=head, drawing_open=open_rows) == REFUSED


def test_a_well_formed_record_reads_its_head_and_counts_open_rows_by_the_one_predicate():
    rows = [
        _row("matches"),
        _row("departs", settled=True),
        _row("departs"),
        _row("unverified"),
        _row("something_new"),
    ]
    assert rules.drawing_merge_facts(_state({"head": HEAD, "rows": rows, "open": 0})) == (
        True,
        HEAD,
        3,
    )
    closed = [_row("matches"), _row("departs", settled=True)]
    assert rules.drawing_merge_facts(_state({"head": HEAD, "rows": closed, "open": 0})) == (
        True,
        HEAD,
        0,
    )
    assert rules.drawing_merge_facts(_state({"head": HEAD, "rows": closed, "open": 2}))[2] == 2


def test_no_record_reads_as_not_checked():
    assert rules.drawing_merge_facts(cast(TaskState, {})) == (False, None, 0)


def test_the_refusal_is_closed_and_left_to_the_human():
    assert "drawing_unsettled" in landing.HUMAN_OWNED_REFUSALS
    step = landing.landing_step(
        "drawing_unsettled", _facts(**_drawing(OTHER)), _spent(), ("api",), turn="head"
    )
    assert step == landing.LandingStep("none", "drawing_unsettled", ())


def _spent():
    from agentic.core.process.application import landing_plan

    return landing_plan.spent(cast(TaskState, {}), HEAD, None, MASTER)


def test_the_body_has_one_fixed_line_per_open_row():
    rows = [
        _row("matches"),
        _row("unverified", subject="Cart", ref="docs/design/cart.png"),
        _row("departs", subject="Pay", ref="docs/design/pay.png"),
        _row("departs", subject="Done", ref="x", settled=True),
    ]
    assert rules.drawing_body_lines(_state({"head": HEAD, "rows": rows, "open": 2})) == [
        "Drawing not verified: Cart docs/design/cart.png",
        "Drawing departure not settled: Pay docs/design/pay.png",
    ]


def test_the_body_flattens_and_caps_the_ref():
    row = _row("unverified", ref="one\ntwo   three " + "x" * 400)
    [line] = rules.drawing_body_lines(_state({"head": HEAD, "rows": [row], "open": 1}))
    assert "\n" not in line
    assert line.startswith("Drawing not verified: Home one two three xxx")
    assert len(line) == len("Drawing not verified: Home ") + 240


@pytest.mark.parametrize("record", ["garbage", {"head": HEAD, "rows": "x", "open": 0}])
def test_a_malformed_record_is_one_unreadable_line(record):
    assert rules.drawing_body_lines(_state(record)) == ["Drawing check record unreadable"]


def test_no_record_gives_no_body_lines():
    assert rules.drawing_body_lines(cast(TaskState, {})) == []
