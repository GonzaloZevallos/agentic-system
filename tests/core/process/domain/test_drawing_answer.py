"""The drawing-answer domain (GH-766 part 2a-2): which row an answer reaches, when the escalation
is due, what it says, and which cause a pause carries."""

from __future__ import annotations

from typing import Any, Literal, cast

import pytest

from agentic.core.process.domain import drawing, drawing_answer, escalation_prompts, rules, task
from agentic.core.process.domain.schemas import DrawingCheck, DrawingDeparture

AT = "2026-11-15T10:00:00Z"


def _departure(element: str, drawing_path: str = "design/home.png") -> drawing.Departure:
    return drawing.Departure(
        drawing=drawing_path,
        element=element,
        shows="a blue button",
        would_ship="a green button",
        recommendation="keep the drawing",
        reason="the owner approved it",
        raised_by="developer",
    )


def _state(**over: Any) -> task.TaskState:
    return cast(task.TaskState, over)


def _answered(row: task.DepartureRecord) -> task.DepartureRecord:
    return cast(
        task.DepartureRecord,
        {**row, "answer": {"verdict": "accept", "by": "comment", "at": "T1"}},
    )


def _rows(*elements: str, answered: tuple[int, ...] = ()) -> list[task.DepartureRecord]:
    rows = task.departure_records(None, [_departure(e) for e in elements], AT)
    return [_answered(r) if r["number"] in answered else r for r in rows]


def _applied(state: task.TaskState, patch: dict[str, object]) -> task.TaskState:
    return cast(task.TaskState, task.apply(state, cast(Any, patch)))


def test_unanswered_lists_open_numbers_sorted_and_tolerates_no_key():
    assert drawing_answer.unanswered(_state()) == []
    rows = _rows("a", "b", "c", answered=(2,))
    assert drawing_answer.unanswered(_state(drawing_departures=rows[::-1])) == [1, 3]


def test_applicable_ignores_a_number_without_row_and_an_answered_row():
    state = _state(drawing_departures=_rows("a", "b", answered=(2,)))
    take, ignored = drawing_answer.applicable(state, {1: "accept", 2: "reject", 99: "accept"})
    assert take == {1: "accept"}
    assert ignored == frozenset({2, 99})


def test_patch_without_spec_has_no_spec_cause_and_no_mechanical_cause():
    patch = drawing_answer.escalation_patch(_state(), [_departure("hero")], AT)
    assert "spec_cause" not in patch
    assert patch["escalation_cause"] is None
    assert patch["escalation_class"] == "questions"
    assert patch["drawing_escalation"] == {"round": 1, "numbers": [1], "cause": "drawing_departure"}
    assert [r["number"] for r in cast(list[task.DepartureRecord], patch["drawing_departures"])] == [
        1
    ]


def test_patch_with_spec_carries_exactly_the_drawing_spec_cause():
    state = _state(spec=cast(Any, {"issue": 7}))
    patch = drawing_answer.escalation_patch(state, [_departure("hero")], AT)
    assert patch["spec_cause"] == "drawing"
    assert patch["escalation_cause"] is None


def _with_round(previous: int, *elements: str) -> task.TaskState:
    return _state(
        drawing_departures=_rows(*elements),
        drawing_escalation=task.DrawingEscalation(
            round=previous, numbers=[1], cause="drawing_departure"
        ),
    )


def test_no_record_is_round_one_due():
    due = drawing_answer.escalation_due(_state(), [_departure("a")])
    assert isinstance(due, drawing_answer.Due)
    assert (due.round, due.cause) == (1, "drawing_departure")


@pytest.mark.parametrize("previous", [0, 1, 2])
def test_rounds_up_to_three_are_due(previous):
    due = drawing_answer.escalation_due(_with_round(previous, "a"), [])
    assert isinstance(due, drawing_answer.Due)
    assert due.round == previous + 1


def test_round_four_is_exhausted_and_records_the_new_rows_unanswered():
    state = _with_round(3, "a")
    new = [_departure("b")]
    due = drawing_answer.escalation_due(state, new)
    assert isinstance(due, drawing_answer.Exhausted)
    assert (due.round, due.cause) == (4, "drawing_cap")
    patch = drawing_answer.escalation_patch(state, new, AT)
    rows = cast(list[task.DepartureRecord], patch["drawing_departures"])
    assert [(r["number"], r["answer"]) for r in rows] == [(2, None)]
    assert patch["drawing_escalation"] == {"round": 4, "numbers": [1, 2], "cause": "drawing_cap"}
    reason = cast(str, patch["escalated"])
    assert reason.startswith("The task escalated drawing departures in 3 rounds")
    assert "0 departures are answered and 2 still open" in reason
    assert "close the issue" in reason


def test_round_five_is_still_the_cap():
    due = drawing_answer.escalation_due(_with_round(4, "a"), [])
    assert isinstance(due, drawing_answer.Exhausted)
    assert (due.round, due.cause) == (5, "drawing_cap")


def test_nothing_unanswered_raises():
    state = _state(drawing_departures=_rows("a", answered=(1,)))
    with pytest.raises(ValueError, match="nothing to escalate"):
        drawing_answer.escalation_patch(state, [_departure("a")], AT)
    with pytest.raises(ValueError):
        drawing_answer.escalation_patch(_state(), [], AT)


def test_a_contradicted_open_row_gets_its_line_and_a_row_without_one_does_not():
    state = _state(drawing_departures=_rows("a", "b", answered=(2,)))
    patch = drawing_answer.escalation_patch(state, [], AT, contradicted=frozenset({1, 2, 99}))
    reason = cast(str, patch["escalated"])
    assert "Drawing 1 got both accept and reject in one comment; answer it once." in reason
    assert "Drawing 2 got both" not in reason
    assert "Drawing 99 got both" not in reason


def test_the_reason_carries_the_question_but_no_command():
    state = _state(drawing_departures=_rows("a", "b"))
    reason = cast(str, drawing_answer.escalation_patch(state, [], AT)["escalated"])
    assert "Departure #1:" in reason and "Departure #2:" in reason
    assert "agentic resume" not in reason


def test_prompt_lines_are_both_commands_per_open_row_in_number_order():
    rows = _rows("a", "b", "c", answered=(2,))
    lines = drawing_answer.prompt_lines(rows[::-1], 42)
    assert lines == [
        "agentic resume --issue 42 --drawing 1=accept",
        "agentic resume --issue 42 --drawing 1=reject",
        "agentic resume --issue 42 --drawing 3=accept",
        "agentic resume --issue 42 --drawing 3=reject",
    ]
    assert lines[0] == escalation_prompts.DRAWING_ACCEPT.format(issue=42).replace("<n>", "1")
    assert drawing_answer.prompt_lines([], 42) == []


def test_the_internal_key_is_in_no_reason_and_no_section_line():
    state = _with_round(3, "Hero Banner")
    patch = drawing_answer.escalation_patch(state, [_departure("Footer")], AT)
    rows = (
        cast(list[task.DepartureRecord], patch["drawing_departures"]) + state["drawing_departures"]
    )
    keys = [drawing.departure_key(r["drawing"], r["element"]) for r in rows]
    reason = cast(str, patch["escalated"])
    lines = drawing_answer.section_lines(rows) + drawing_answer.section_lines(
        [_answered(r) for r in rows]
    )
    for key in keys:
        assert key not in reason
        assert all(key not in line for line in lines)


def test_an_answered_round_one_row_stays_in_numbers_and_does_not_trigger_alone():
    patch = drawing_answer.escalation_patch(_state(), [_departure("a")], AT)
    state = _applied(_state(), patch)
    assert drawing_answer.prompt_cause(state) == "drawing_departure"
    answered = _state(
        **{**state, "drawing_departures": [_answered(r) for r in state["drawing_departures"]]}
    )
    assert answered["drawing_escalation"]["numbers"] == [1]
    assert drawing_answer.prompt_cause(answered) is None


def test_prompt_cause_is_none_for_a_tracker_human_pause_with_an_old_unanswered_row():
    patch = drawing_answer.escalation_patch(_state(), [_departure("a")], AT)
    state = _applied(_state(), patch)
    other = rules.escalation("questions", "typed items", tracker_human=["h1"])
    paused = _state(**{**state, **other})
    assert drawing_answer.unanswered(paused) == [1]
    assert drawing_answer.prompt_cause(paused) is None


def test_prompt_cause_is_none_for_api_break_missing_with_an_unregistered_unanswered_row():
    patch = drawing_answer.escalation_patch(_state(), [_departure("a")], AT)
    state = _applied(_state(), patch)
    first = _answered(state["drawing_departures"][0])
    second = task.departure_records([first], [_departure("b")], AT)[0]
    paused = _state(
        **{
            **state,
            "drawing_departures": [first, second],
            **rules.escalation("questions", "api break"),
        }
    )
    assert drawing_answer.unanswered(paused) == [2]
    assert paused["drawing_escalation"]["numbers"] == [1]
    assert drawing_answer.prompt_cause(paused) is None


def test_prompt_cause_is_none_when_the_pause_is_not_a_questions_one():
    patch = drawing_answer.escalation_patch(_state(), [_departure("a")], AT)
    state = _applied(_state(), patch)
    assert drawing_answer.prompt_cause(_state(**{**state, "escalation_class": "ci"})) is None


def test_the_drawing_escalation_is_a_humans_pause():
    patch = drawing_answer.escalation_patch(_state(), [_departure("a")], AT)
    logged = rules.logged(cast(dict[str, Any], patch), at=AT)
    assert rules.pause_kind(logged["pause_log"]) == "human"
    state = _applied(_state(), logged)
    assert rules.auto_resumable(state) is None


def test_section_lines_one_line_per_answered_row_and_bounded():
    rows = [_answered(r) for r in _rows(*[f"e{i}" for i in range(50)])]
    lines = drawing_answer.section_lines(rows)
    assert len(lines) == 50
    assert lines[0] == "#1 · design/home.png · e0 · accept (comment, T1)"


def test_section_lines_open_row_has_four_collapsed_and_cut_fields():
    row = _rows("a")[0]
    long_row = cast(
        task.DepartureRecord, {**row, "shows": "x" * 1000, "reason": "one\ntwo\n\nthree"}
    )
    lines = drawing_answer.section_lines([long_row])
    assert len(lines) == 4
    assert lines[0] == "#1 · shows: " + "x" * 239 + "…"
    assert lines[3] == "#1 · reason: one two three"
    assert all(len(line) <= task.ASSUMPTION_MAX_CHARS + 30 for line in lines)


def test_section_lines_of_no_rows_is_empty():
    assert drawing_answer.section_lines([]) == []


def test_a_state_without_a_drawing_escalation_goes_through_without_raising():
    state = _state()
    assert drawing_answer.applicable(state, {1: "accept"}) == ({}, frozenset({1}))
    assert drawing_answer.escalation_due(state, []).round == 1
    assert drawing_answer.prompt_cause(state) is None
    assert drawing_answer.section_lines(state.get("drawing_departures", [])) == []
    assert task.apply(state, {}) == state
    assert task.delta(state, state) == {}


def test_a_part_one_row_with_a_null_answer_loads():
    row = _rows("a")[0]
    assert row["answer"] is None
    assert task.add_drawing_departures(None, [row]) == [row]


# ---- classify (GH-766 part 5.1) -------------------------------------------------------------


def _check(
    verdict: Literal["matches", "departs", "unverified"],
    ref: str = "home",
    element: str | None = "Save",
) -> DrawingCheck:
    named = verdict != "unverified"
    return DrawingCheck(
        subject="screen",
        ref=ref,
        verdict=verdict,
        drawing="design/home.png" if named else None,
        element=element if named else None,
    )


def _entry(element: str = "Save") -> DrawingDeparture:
    return DrawingDeparture(
        drawing="design/home.png",
        element=element,
        shows="a blue button",
        would_ship="a green button",
        recommendation="keep the drawing",
        reason="the owner approved it",
        raised_by="ux_lead_final",
    )


def _row(element: str = "Save", *, verdict: str | None, fix_passes: int = 0):
    (row,) = task.departure_records(None, [_departure(element)], AT)
    answer = None if verdict is None else {"verdict": verdict, "by": "comment", "at": "T1"}
    return cast(task.DepartureRecord, {**row, "answer": answer, "fix_passes": fix_passes})


KEY = "design/home.png:save"


def test_a_departs_row_with_no_row_is_raised_with_its_departure():
    result = drawing_answer.classify(_state(), [_check("departs")], [_entry()])
    assert result.outcomes == {KEY: "raise"}
    assert result.raised == [
        drawing.Departure(
            drawing="design/home.png",
            element="Save",
            shows="a blue button",
            would_ship="a green button",
            recommendation="keep the drawing",
            reason="the owner approved it",
            raised_by="ux_lead_final",
        )
    ]
    assert [c.verdict for c in result.open] == ["departs"]


def test_an_unanswered_row_is_raised_again_without_duplicating_the_row():
    state = _state(drawing_departures=[_row(verdict=None)])
    result = drawing_answer.classify(state, [_check("departs")], [_entry()])
    assert result.outcomes == {KEY: "raise"}
    assert result.raised == []


def test_an_unanswered_row_with_no_entry_is_refused_like_a_missing_one():
    state = _state(drawing_departures=[_row(verdict=None)])
    with pytest.raises(drawing_answer.ClassifyMismatch) as error:
        drawing_answer.classify(state, [_check("departs")], [])
    assert error.value.cause == "departs_unraised"


def test_an_accepted_row_is_settled_and_not_open():
    state = _state(drawing_departures=[_row(verdict="accept")])
    result = drawing_answer.classify(state, [_check("departs")], [])
    assert result.outcomes == {KEY: "settled_accept"}
    assert result.open == []


@pytest.mark.parametrize(
    ("fix_passes", "outcome"), [(0, "fix_due"), (1, "return_due"), (2, "return_due")]
)
def test_a_rejected_row_is_a_fix_then_a_return(fix_passes: int, outcome: str):
    state = _state(drawing_departures=[_row(verdict="reject", fix_passes=fix_passes)])
    result = drawing_answer.classify(state, [_check("departs")], [])
    assert result.outcomes == {KEY: outcome}
    assert result.raised == []
    assert [c.verdict for c in result.open] == ["departs"]


def test_the_key_ignores_case_and_spaces_of_the_element_and_the_path_spelling():
    check = DrawingCheck(
        subject="screen", ref="h", verdict="departs", drawing="./design/home.png", element=" SAVE "
    )
    state = _state(drawing_departures=[_row(verdict="accept")])
    assert drawing_answer.classify(state, [check], []).outcomes == {KEY: "settled_accept"}


def test_a_departs_check_with_no_entry_and_no_answered_row_is_refused():
    with pytest.raises(drawing_answer.ClassifyMismatch) as error:
        drawing_answer.classify(_state(), [_check("departs")], [])
    assert error.value.cause == "departs_unraised"
    assert KEY in str(error.value) and "departures" in str(error.value)
    assert isinstance(error.value, ValueError)


def test_a_departure_without_its_departs_check_is_refused():
    with pytest.raises(drawing_answer.ClassifyMismatch) as error:
        drawing_answer.classify(_state(), [_check("matches")], [_entry()])
    assert error.value.cause == "departure_without_check"
    assert KEY in str(error.value) and "departs" in str(error.value)


def test_open_keeps_input_order_with_unverified_and_unsettled_departs_only():
    state = _state(drawing_departures=[_row("Done", verdict="accept")])
    checks = [
        _check("unverified", ref="a"),
        _check("matches", ref="b"),
        _check("departs", ref="c", element="Done"),
        _check("departs", ref="d", element="Save"),
        _check("unverified", ref="e"),
    ]
    result = drawing_answer.classify(state, checks, [_entry("Save")])
    assert [c.ref for c in result.open] == ["a", "d", "e"]
    assert result.outcomes == {"design/home.png:done": "settled_accept", KEY: "raise"}


def test_an_unverified_check_is_never_turned_into_a_match():
    result = drawing_answer.classify(_state(), [_check("unverified")], [])
    assert result.outcomes == {}
    assert [c.verdict for c in result.open] == ["unverified"]
