"""The `tracker_failures` field of the task state and its reducer (GH-389)."""

import dataclasses

import pytest

from agentic.core.process.domain.task import (
    REDUCERS,
    TrackerFailure,
    add_tracker_failures,
    apply,
    delta,
)


def row(target="comment", cls="transient", at="2026-10-28T10:00:00Z"):
    return {"target": target, "cls": cls, "at": at}


def test_tracker_failures_is_reduced_by_add_tracker_failures():
    assert REDUCERS["tracker_failures"] is add_tracker_failures


def test_the_value_object_is_frozen_and_renders_the_row_the_document_keeps():
    failure = TrackerFailure(target="label", cls="refused", at="t")
    assert failure.record() == {"target": "label", "cls": "refused", "at": "t"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        failure.cls = "transient"  # type: ignore[misc]


def test_a_repeat_of_target_and_cls_keeps_the_first_row_and_its_time():
    first = row(at="t1")
    merged = add_tracker_failures([first], [row(at="t2"), row(target="label", at="t3")])
    assert merged == [first, row(target="label", at="t3")]


def test_the_same_failure_twice_in_the_new_side_is_one_row():
    assert add_tracker_failures([], [row(at="t1"), row(at="t2")]) == [row(at="t1")]


def test_the_class_is_part_of_the_key():
    merged = add_tracker_failures([row(cls="transient")], [row(cls="timed_out")])
    assert [r["cls"] for r in merged] == ["transient", "timed_out"]


def test_the_list_is_capped_at_ten_rows_keeping_the_oldest():
    # The closed values give ten distinct keys at most; the cap holds on any input.
    rows = [row(cls=f"c{n}", at=f"t{n}") for n in range(14)]
    merged = add_tracker_failures(rows[:6], rows[6:])
    assert merged == rows[:10]
    assert add_tracker_failures(merged, [row(cls="c99")]) == rows[:10]


def test_an_empty_side_is_tolerated():
    assert add_tracker_failures([], []) == []
    assert add_tracker_failures([row()], []) == [row()]


def test_a_document_without_the_key_reads_as_empty():
    assert apply({"id": "GH-1"}, {"tracker_failures": [row()]})["tracker_failures"] == [row()]
    assert {"id": "GH-1"}.get("tracker_failures", []) == []


def test_delta_sends_only_new_rows_and_apply_round_trips():
    before = {"tracker_failures": [row()]}
    after = {"tracker_failures": add_tracker_failures(before["tracker_failures"], [row("label")])}
    assert delta(before, after) == {"tracker_failures": [row("label")]}
    assert apply(before, delta(before, after)) == after


def test_delta_sends_nothing_when_nothing_was_added():
    state = {"tracker_failures": [row()]}
    assert "tracker_failures" not in delta(state, {"tracker_failures": [row()]})
