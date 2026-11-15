"""The `drawing_departures` record (GH-766 part 1b): who assigns a number, how rows fold."""

from __future__ import annotations

from typing import Any, cast

import pytest

from agentic.core.process.domain.drawing import Departure
from agentic.core.process.domain.task import (
    DepartureRecord,
    TaskInput,
    TaskState,
    add_drawing_departures,
    apply,
    delta,
    departure_records,
)


def _departure(element: str, drawing: str = "design/home.png") -> Departure:
    return Departure(
        drawing=drawing,
        element=element,
        shows="a blue button",
        would_ship="a green button",
        recommendation="keep the drawing",
        reason="the owner approved it",
        raised_by="developer",
    )


def _record(number: int, key: str = "design/home.png:cta", **over: Any) -> DepartureRecord:
    row: dict[str, Any] = {
        "number": number,
        "key": key,
        "drawing": "design/home.png",
        "element": "cta",
        "shows": "a blue button",
        "would_ship": "a green button",
        "recommendation": "keep the drawing",
        "reason": "the owner approved it",
        "raised_by": "developer",
        "raised_at": "2026-11-15T10:00:00Z",
        "answer": None,
        "fix_passes": 0,
    }
    return cast(DepartureRecord, {**row, **over})


def test_two_new_keys_are_numbered_in_order():
    rows = departure_records(None, [_departure("Hero"), _departure("Footer")], "T0")
    assert [(r["number"], r["key"]) for r in rows] == [
        (1, "design/home.png:hero"),
        (2, "design/home.png:footer"),
    ]
    assert all(
        r["raised_at"] == "T0" and r["answer"] is None and r["fix_passes"] == 0 for r in rows
    )


def test_a_known_key_keeps_its_row_and_a_new_one_takes_the_next_number():
    existing = [_record(2, key="design/home.png:hero", element="hero", raised_at="T-old")]
    rows = departure_records(existing, [_departure("Hero"), _departure("Footer")], "T1")
    assert rows[0] == existing[0]
    assert (rows[1]["number"], rows[1]["raised_at"]) == (3, "T1")


def test_a_key_repeated_within_the_departures_yields_one_row():
    rows = departure_records(None, [_departure("Hero"), _departure("  HERO ")], "T0")
    assert [r["number"] for r in rows] == [1]


def test_a_malformed_drawing_never_gets_a_number():
    with pytest.raises(ValueError, match="relative"):
        departure_records(None, [_departure("hero", drawing="/abs.png")], "T0")


def test_replaying_the_same_rows_changes_nothing():
    new = [_record(1), _record(2, key="design/home.png:hero", element="hero")]
    once = add_drawing_departures(None, new)
    assert once == new
    assert add_drawing_departures(once, new) == once


def test_the_first_answer_stays_and_a_conflict_never_raises():
    held = [_record(1, answer="accept")]
    assert add_drawing_departures(held, [_record(1, answer="reject")])[0]["answer"] == "accept"
    assert add_drawing_departures([_record(1)], [_record(1, answer="reject")])[0]["answer"] == (
        "reject"
    )


def test_number_raised_at_raised_by_and_text_are_never_overwritten():
    held = [_record(1)]
    other = _record(
        9,
        raised_at="later",
        raised_by="ux",
        shows="x",
        would_ship="y",
        recommendation="z",
        reason="w",
    )
    assert add_drawing_departures(held, [other]) == held


def test_fix_passes_takes_the_max():
    low, high = _record(1, fix_passes=1), _record(1, fix_passes=3)
    assert add_drawing_departures([high], [low])[0]["fix_passes"] == 3
    assert add_drawing_departures([low], [high])[0]["fix_passes"] == 3


def test_apply_folds_rows_over_an_absent_key_through_the_reducer():
    state = cast(TaskState, {"id": "T-1"})
    patch = cast(TaskState, {"drawing_departures": [_record(1), _record(1, answer="x")]})
    merged = apply(state, patch)
    assert len(merged["drawing_departures"]) == 1
    assert merged["drawing_departures"][0]["answer"] == "x"


def test_delta_round_trips_an_absent_key_and_a_changed_answer():
    rows = [_record(1), _record(2, key="design/home.png:hero", element="hero")]
    bare = cast(TaskState, {"id": "T-1"})
    after = cast(TaskState, {"id": "T-1", "drawing_departures": rows})
    assert apply(bare, delta(bare, after)) == after

    answered = [_record(1, answer="accept", fix_passes=1), rows[1]]
    final = cast(TaskState, {"id": "T-1", "drawing_departures": answered})
    patch = delta(after, final)
    assert patch["drawing_departures"] == [answered[0]]
    assert apply(after, patch) == final
    assert "drawing_departures" not in delta(final, final)


def test_the_initial_input_does_not_carry_drawing_departures():
    assert "drawing_departures" not in TaskInput.__annotations__
    assert "drawing_departures" in TaskState.__optional_keys__
