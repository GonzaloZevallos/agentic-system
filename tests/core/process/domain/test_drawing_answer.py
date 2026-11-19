"""The drawing-answer domain (GH-766 parts 2a-2, 4a and 5): which row an answer reaches, when the
escalation is due, what it says, which cause a pause carries, the plan gate and the checks."""

from __future__ import annotations

from typing import Any, Literal, cast

import pytest

from agentic.core.process.domain import (
    document,
    drawing,
    drawing_answer,
    escalation_prompts,
    rules,
    task,
)
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


def _verdict_row(row: task.DepartureRecord, verdict: str, **over: Any) -> task.DepartureRecord:
    return cast(
        task.DepartureRecord,
        {**row, "answer": {"verdict": verdict, "by": "comment", "at": "T1"}, **over},
    )


def _two_rows() -> list[task.DepartureRecord]:
    a, b = _rows("a", "b")
    return [_verdict_row(a, "reject"), _verdict_row(b, "accept")]


def test_rejected_keys_maps_each_rejected_key_to_its_number_and_skips_the_rest():
    open_row, accepted = _rows("open", "ok")
    rejected = _verdict_row(_rows("no")[0], "reject", number=7)
    state = _state(drawing_departures=[open_row, _verdict_row(accepted, "accept"), rejected])
    assert drawing_answer.rejected_keys(state) == {rejected["key"]: 7}
    assert drawing_answer.rejected_keys(_state()) == {}


def test_must_match_is_the_rejected_rows_of_the_seat_with_no_fix_pass():
    rows = _two_rows()
    state = _state(drawing_departures=rows)
    assert drawing_answer.must_match(state, "developer") == {rows[0]["key"]: 1}
    assert drawing_answer.must_match(state, "pm_interviewee") == {}
    done = _state(drawing_departures=[{**rows[0], "fix_passes": 1}, rows[1]])
    assert drawing_answer.must_match(done, "developer") == {}


# --- GH-766 part 4a: the plan gate's verdicts, the stage and the resume patch ------------------


def _verdict(row: task.DepartureRecord, verdict: str) -> task.DepartureRecord:
    return cast(
        task.DepartureRecord,
        {**row, "answer": {"verdict": verdict, "by": "comment", "at": "T1"}},
    )


def _split_state(**verdicts: str) -> task.TaskState:
    """Rows a, b, c numbered 1..3; `verdicts` maps an element to its verdict (absent: open)."""
    rows = task.departure_records(None, [_departure(e) for e in ("a", "b", "c")], AT)
    return _state(
        drawing_departures=[
            _verdict(r, verdicts[r["element"]]) if r["element"] in verdicts else r for r in rows
        ]
    )


def test_plan_split_sends_each_departure_to_accepted_rejected_or_open():
    state = _split_state(a="accept", b="reject")
    split = drawing_answer.plan_split(
        state, [_departure("a"), _departure("b"), _departure("c"), _departure("d")]
    )
    assert [d.element for d in split.accepted] == ["a"]
    assert [d.element for d in split.rejected] == ["b"]
    assert [d.element for d in split.open] == ["c", "d"]  # unanswered, and without a row
    assert dict(split.rejected_numbers) == {drawing.departure_key("design/home.png", "b"): 2}


def test_plan_split_dedupes_by_key_in_order_of_appearance():
    state = _split_state(b="reject")
    first = _departure("B ")
    again = _departure("  b")  # same key as the first
    split = drawing_answer.plan_split(state, [first, _departure("c"), again, _departure("c")])
    assert split.rejected == (first,)
    assert [d.element for d in split.open] == ["c"]
    assert len(split.rejected_numbers) == 1


def test_plan_split_keeps_accepted_and_open_out_of_the_rejected_numbers():
    state = _split_state(a="accept", b="reject")
    split = drawing_answer.plan_split(state, [_departure("a"), _departure("c")])
    assert dict(split.rejected_numbers) == {}
    with pytest.raises(TypeError):
        cast(Any, split.rejected_numbers)["x"] = 1


def test_plan_split_of_a_state_without_rows_is_all_open_and_split_is_frozen():
    split = drawing_answer.plan_split(_state(), [_departure("a")])
    assert [d.element for d in split.open] == ["a"]
    assert split.accepted == () and split.rejected == ()
    with pytest.raises(AttributeError):
        cast(Any, split).open = ()


def test_escalation_patch_writes_the_stage_it_is_given_and_the_default_does_not():
    plan = drawing_answer.escalation_patch(_state(), [_departure("a")], AT, stage="plan")
    assert plan["drawing_escalation"]["stage"] == "plan"  # type: ignore[index]
    default = drawing_answer.escalation_patch(_state(), [_departure("a")], AT)
    assert "stage" not in default["drawing_escalation"]  # type: ignore[operator]
    folded = _applied(_state(), default)
    assert "stage" not in folded["drawing_escalation"]
    assert _applied(_state(), plan)["drawing_escalation"].get("stage") == "plan"


def _paused(stage: str | None, **over: Any) -> task.TaskState:
    patch = (
        drawing_answer.escalation_patch(_state(), [_departure("a")], AT)
        if stage is None
        else drawing_answer.escalation_patch(
            _state(), [_departure("a")], AT, stage=cast(Any, stage)
        )
    )
    state = _applied(_state(), patch)
    return _state(**{**state, **over})


def _answers(state: task.TaskState) -> list[task.DepartureRecord]:
    return [_answered(r) for r in state["drawing_departures"]]


def test_resume_patch_on_a_live_plan_pause_re_plans_even_with_old_subtasks():
    state = _paused("plan", subtasks={"s": {"status": "approved"}})
    patch = drawing_answer.resume_patch(state, "ok", None, _answers(state), at=AT)
    assert patch["plan_due"] is True
    assert patch["drawing_departures"] == _answers(state)
    resumed = _applied(state, patch)
    assert rules.resume_target(resumed) == "plan"


@pytest.mark.parametrize("stage", [None, "delivery"])
def test_resume_patch_does_not_re_plan_without_a_plan_stage(stage):
    state = _paused(stage)
    patch = drawing_answer.resume_patch(state, "ok", None, _answers(state), at=AT)
    assert "plan_due" not in patch


@pytest.mark.parametrize(
    "pause",
    [
        rules.escalation("failure", "x"),
        rules.escalation("cap", "x"),
        rules.escalation("estimate", "x"),
    ],
)
def test_resume_patch_ignores_a_stale_plan_record_under_another_pause(pause):
    state = _paused("plan", **pause)
    patch = drawing_answer.resume_patch(state, "ok", None, _answers(state), at=AT)
    assert "plan_due" not in patch


def test_resume_patch_without_answered_rows_is_exactly_rules_resumed():
    state = _paused("plan")
    patch = drawing_answer.resume_patch(state, "ok", 3.0, [], at=AT)
    assert patch == rules.resumed(state, "ok", 3.0, at=AT)
    assert "drawing_departures" not in patch


def test_resume_patch_after_a_document_round_trip_still_goes_to_plan():
    state = _paused(
        "plan", id="T-1", title="t", description="d", subtasks={"s": {"status": "approved"}}
    )
    again = cast(task.TaskState, document.parse(document.render(state)))
    assert again["drawing_escalation"]["stage"] == "plan"
    patch = drawing_answer.resume_patch(again, "ok", None, _answers(again), at=AT)
    assert rules.resume_target(_applied(again, patch)) == "plan"


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


@pytest.mark.parametrize("flip", [False, True], ids=["ux-first", "ui-first"])
def test_merge_checks_keeps_the_gravest_verdict_per_image_in_either_order(flip: bool):
    ux = [_check("matches", ref="home.png"), _check("unverified", ref="list.png")]
    ui = [_check("departs", ref="home.png"), _check("matches", ref="list.png")]
    merged = drawing_answer.merge_checks(*((ui, ux) if flip else (ux, ui)))
    assert {c.ref: c.verdict for c in merged} == {"home.png": "departs", "list.png": "unverified"}
    assert len(merged) == 2


def test_merge_checks_with_one_side_empty_is_the_other_side_once_per_image():
    twice = [_check("matches", ref="a.png"), _check("matches", ref="a.png")]
    assert [c.ref for c in drawing_answer.merge_checks([], twice)] == ["a.png"]


def test_fix_pass_rows_raises_fix_passes_to_one_for_fix_due_only():
    fix = _row("Save", verdict="reject")
    back = _row("Cancel", verdict="reject", fix_passes=1)
    state = _state(drawing_departures=[fix, back])
    outcomes: dict[str, drawing_answer.Outcome] = {
        fix["key"]: "fix_due",
        back["key"]: "return_due",
    }
    rows = drawing_answer.fix_pass_rows(state, outcomes)
    assert [(r["key"], r["fix_passes"]) for r in rows] == [(fix["key"], 1)]
    assert drawing_answer.fix_pass_rows(state, {back["key"]: "return_due"}) == []


UNVERIFIED_AT = "2026-11-18T10:00:00.000+00:00"
LATER_AT = "2026-11-18T10:05:00.000+00:00"


def _checks(*refs: str) -> list[DrawingCheck]:
    return [
        DrawingCheck(subject="question", ref=ref, verdict="unverified", drawing="design/./home.png")
        for ref in refs
    ]


def _asks(*refs: str) -> list[drawing.UnverifiedAsk]:
    return drawing.unverified_lines(_checks(*refs))


def test_an_unverified_only_pause_without_a_spec_is_a_plain_questions_pause():
    asks = _asks("Q1", "Q2")
    patch = drawing_answer.escalation_patch(_state(), [], UNVERIFIED_AT, unverified=asks)
    assert patch["escalation_class"] == "questions"
    assert "spec_cause" not in patch
    assert patch["escalated"] == "\n".join(a.line for a in asks)
    assert patch["questions_for_human"] == [a.line for a in asks]
    assert patch["drawing_escalation"] == {
        "round": 1,
        "numbers": [],
        "cause": "drawing_departure",
        "unverified": ["design/home.png"],
        "unverified_at": UNVERIFIED_AT,
    }
    assert patch["drawing_departures"] == []


def test_an_unverified_only_pause_with_a_spec_carries_the_drawing_spec_cause():
    state = _state(spec=cast(Any, {"issue": 7}))
    patch = drawing_answer.escalation_patch(state, [], UNVERIFIED_AT, unverified=_asks("Q1"))
    assert patch["spec_cause"] == "drawing"


def test_a_mixed_pause_words_the_departures_first_and_carries_only_the_ask_lines():
    asks = _asks("Q4")
    patch = drawing_answer.escalation_patch(
        _state(), [_departure("hero")], UNVERIFIED_AT, unverified=asks
    )
    reason = cast(str, patch["escalated"]).split("\n")
    assert reason[0].startswith("Departure #1:") and reason[-1] == asks[0].line
    assert patch["questions_for_human"] == [asks[0].line]
    record = cast(task.DrawingEscalation, patch["drawing_escalation"])
    assert record["numbers"] == [1] and record["unverified"] == ["design/home.png"]


def test_without_asks_the_patch_has_no_questions_and_no_unverified_keys():
    patch = drawing_answer.escalation_patch(_state(), [_departure("hero")], UNVERIFIED_AT)
    assert "questions_for_human" not in patch
    assert patch["drawing_escalation"] == {"round": 1, "numbers": [1], "cause": "drawing_departure"}


def test_a_pause_with_nothing_to_ask_raises_and_an_ask_alone_does_not():
    with pytest.raises(ValueError, match="nothing to escalate"):
        drawing_answer.escalation_patch(_state(), [], UNVERIFIED_AT, unverified=())
    drawing_answer.escalation_patch(_state(), [], UNVERIFIED_AT, unverified=_asks("Q1"))


def test_an_unverified_only_plan_pause_is_refused_but_a_plan_pause_with_a_number_is_not():
    with pytest.raises(ValueError, match="an unverified-only drawing pause cannot be a plan pause"):
        drawing_answer.escalation_patch(
            _state(), [], UNVERIFIED_AT, unverified=_asks("Q1"), stage="plan"
        )
    patch = drawing_answer.escalation_patch(
        _state(), [_departure("a")], UNVERIFIED_AT, unverified=_asks("Q1"), stage="plan"
    )
    assert cast(task.DrawingEscalation, patch["drawing_escalation"])["stage"] == "plan"


def test_round_four_with_asks_only_is_the_cap_with_the_history_line_then_the_asks():
    state = _state(
        drawing_escalation=task.DrawingEscalation(round=3, numbers=[], cause="drawing_departure")
    )
    asks = _asks("Q1")
    patch = drawing_answer.escalation_patch(state, [], UNVERIFIED_AT, unverified=asks)
    record = cast(task.DrawingEscalation, patch["drawing_escalation"])
    assert (record["round"], record["cause"]) == (4, "drawing_cap")
    assert patch["escalated"] == (
        "The task escalated drawing departures in 3 rounds (the cap is "
        f"{drawing.DEPARTURE_ESCALATIONS_MAX}); 0 departures are answered and 0 still open. "
        "Recommendation: close the issue, or rethink the task so it stops departing from the "
        f"approved drawings.\n{asks[0].line}"
    )


def _paused_on_asks() -> task.TaskState:
    patch = drawing_answer.escalation_patch(_state(), [], UNVERIFIED_AT, unverified=_asks("Q1"))
    return _applied(_state(), rules.logged(cast(dict[str, Any], patch), at=UNVERIFIED_AT))


def test_prompt_cause_of_an_asks_only_pause_is_the_recorded_cause():
    assert drawing_answer.prompt_cause(_paused_on_asks()) == "drawing_departure"


def test_prompt_cause_of_an_asks_only_pause_is_none_after_it_was_resumed_and_others_follow():
    state = _paused_on_asks()
    resumed = _applied(state, rules.resumed(state, "go", None, at=LATER_AT, drawing_plan=False))
    conflict = rules.logged(
        dict(
            rules.escalation(
                "questions", "a conflict", spec=cast(Any, {"issue": 7}), spec_cause="conflict"
            )
        ),
        at=LATER_AT,
    )
    assert drawing_answer.prompt_cause(_applied(resumed, conflict)) is None
    typed = rules.logged(
        dict(rules.escalation("questions", "typed items", tracker_human=["h1"])), at=UNVERIFIED_AT
    )
    assert drawing_answer.prompt_cause(_applied(state, typed)) is None


def test_prompt_cause_needs_the_asks_instant_to_be_the_last_escalations():
    state = _paused_on_asks()
    assert drawing_answer.prompt_cause(_state(**{**state, "pause_log": []})) is None
    assert drawing_answer.prompt_cause(_state(**{**state, "escalation_class": "ci"})) is None


def _raise_state(**over: Any) -> task.TaskState:
    """What `fold_raised` reads: the state a verifier set ran on."""
    return _state(**over)


def test_fold_raised_without_the_key_returns_merged_as_it_is():
    merged = _state(cost_usd=1.0)
    assert drawing_answer.fold_raised(_raise_state(), merged, AT) == {"cost_usd": 1.0}


def test_fold_raised_escalates_a_raise_and_keeps_a_siblings_deltas():
    sibling = {"returned_with": ["review"], "cost_usd": 2.0}
    merged = _state(**sibling, drawing_raised=[_departure("Save")])
    folded = drawing_answer.fold_raised(_raise_state(), merged, AT)
    assert "drawing_raised" not in folded and "drawing_raised" in merged
    assert folded["escalation_class"] == "questions" and folded["escalated"]
    assert folded["drawing_escalation"] == {
        "round": 1,
        "numbers": [1],
        "cause": "drawing_departure",
    }
    assert [r["number"] for r in folded["drawing_departures"]] == [1]
    assert folded["returned_with"] == ["review"] and folded["cost_usd"] == 2.0
    assert folded["status"] == task.WAITING_ON_HUMAN
    assert [e["kind"] for e in folded["pause_log"]] == ["escalation"]


def test_fold_raised_with_an_empty_list_escalates_the_row_still_open():
    state = _raise_state(drawing_departures=_rows("a", "b", answered=(1,)))
    folded = drawing_answer.fold_raised(state, _state(drawing_raised=[]), AT)
    assert "drawing_raised" not in folded
    assert folded["drawing_escalation"]["numbers"] == [2]
    assert folded["escalation_class"] == "questions"


def test_fold_raised_drops_the_departures_when_a_sibling_already_escalated():
    sibling = _state(escalated="the suite is red", escalation_class="returns", cost_usd=1.0)
    merged = _applied(sibling, {"drawing_raised": [_departure("Save")]})
    folded = drawing_answer.fold_raised(_raise_state(), merged, AT)
    assert folded == sibling
    assert "drawing_departures" not in folded and "drawing_escalation" not in folded


def test_fold_raised_never_opens_a_second_row_for_another_capitalisation():
    merged = _state(drawing_raised=[_departure("Save"), _departure("save")])
    folded = drawing_answer.fold_raised(_raise_state(), merged, AT)
    assert [r["number"] for r in folded["drawing_departures"]] == [1]
    held = _raise_state(drawing_departures=_rows("Save"))
    again = drawing_answer.fold_raised(held, _state(drawing_raised=[_departure("save")]), AT)
    assert again["drawing_escalation"]["numbers"] == [1]
    assert [r["element"] for r in again["drawing_departures"]] == ["Save"]


def test_fold_raised_after_an_answered_round_opens_no_second_one_for_another_case():
    record = task.DrawingEscalation(round=1, numbers=[1], cause="drawing_departure")
    held = _raise_state(drawing_departures=_rows("Save", answered=(1,)), drawing_escalation=record)
    # a pass that cites `save` against the answered `Save` raises nothing (`classify`), so the
    # node leaves no key and the fold changes nothing
    assert drawing_answer.fold_raised(held, _state(cost_usd=1.0), AT) == {"cost_usd": 1.0}
    classified = drawing_answer.classify(
        held,
        [
            DrawingCheck(
                subject="screen",
                ref="design/home.png",
                verdict="departs",
                drawing="design/home.png",
                element="save",
            )
        ],
        [
            DrawingDeparture(
                drawing="design/home.png",
                element="save",
                shows="a",
                would_ship="b",
                recommendation="r",
                reason="r",
                raised_by="developer",
            )
        ],
    )
    assert classified.raised == [] and "raise" not in classified.outcomes.values()
