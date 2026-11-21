"""The repairs of a role's raw output (GH-1016): pure, new dict out, input untouched."""

import copy

from agentic.core.process.domain.output_repair import (
    ELLIPSIS,
    REPAIRS,
    RepairContext,
    parts_dropped_when_fits,
    reason_truncated,
    repair_note,
)
from agentic.core.process.domain.task_path import TEXT_MAX

CONTEXT = RepairContext()


def _part(*reasons: str) -> dict:
    return {
        "title": "t",
        "body": "b",
        "depends_on": list(range(1, len(reasons) + 1)),
        "depends_on_reasons": list(reasons),
    }


def _words(count: int) -> str:
    return " ".join(f"word{i:03d}" for i in range(count))


def test_the_registry_holds_exactly_the_two_mechanical_rules():
    assert set(REPAIRS) == {"depends_on_reason_too_long", "parts_when_fits"}
    assert REPAIRS["depends_on_reason_too_long"] is reason_truncated
    assert REPAIRS["parts_when_fits"] is parts_dropped_when_fits


def test_the_registry_is_read_only():
    assert not hasattr(REPAIRS, "__setitem__")


def test_a_long_reason_is_cut_at_a_word_boundary_with_a_final_ellipsis():
    long = _words(60)
    raw = {"parts": [_part("short reason", long)], "other": 1}
    before = copy.deepcopy(raw)

    fixed = reason_truncated(raw, CONTEXT)

    assert fixed is not None and fixed is not raw
    assert raw == before  # the input and its nested lists are untouched
    cut = fixed["parts"][0]["depends_on_reasons"][1]
    assert len(cut) <= TEXT_MAX and cut.endswith(ELLIPSIS)
    assert long.startswith(cut[:-1]) and long[len(cut) - 1] == " "  # no word is split
    assert fixed["parts"][0]["depends_on_reasons"][0] == "short reason"
    assert fixed["other"] == 1


def test_a_reason_with_no_space_is_cut_hard_to_the_limit():
    fixed = reason_truncated({"parts": [_part("x" * 500)]}, CONTEXT)
    assert fixed is not None
    assert fixed["parts"][0]["depends_on_reasons"][0] == "x" * (TEXT_MAX - 1) + ELLIPSIS


def test_every_long_reason_of_every_part_is_fixed_at_once():
    raw = {"parts": [_part(_words(60)), _part("fine", _words(70))]}
    fixed = reason_truncated(raw, CONTEXT)
    assert fixed is not None
    lengths = [len(r) for p in fixed["parts"] for r in p["depends_on_reasons"]]
    assert max(lengths) <= TEXT_MAX
    assert fixed["parts"][1]["depends_on_reasons"][0] == "fine"


def test_a_cut_that_lands_on_a_boundary_keeps_the_last_whole_word():
    text = "a" * (TEXT_MAX - 2) + " " + "b" * 40
    fixed = reason_truncated({"parts": [_part(text)]}, CONTEXT)
    assert fixed is not None
    assert fixed["parts"][0]["depends_on_reasons"][0] == "a" * (TEXT_MAX - 2) + ELLIPSIS


def test_nothing_is_repaired_when_no_reason_is_too_long():
    assert reason_truncated({"parts": [_part("fine")]}, CONTEXT) is None
    assert reason_truncated({"subtasks": []}, CONTEXT) is None
    assert reason_truncated({"parts": "not a list"}, CONTEXT) is None


def test_a_reason_that_is_long_and_has_a_newline_is_left_for_its_own_refusal():
    text = "x" * 300 + "\n" + "y"
    assert reason_truncated({"parts": [_part(text)]}, CONTEXT) is None


def test_the_parts_are_dropped_when_there_are_subtasks():
    raw = {"subtasks": [{"id": "a"}], "parts": [_part("r")], "other": 1}
    before = copy.deepcopy(raw)

    fixed = parts_dropped_when_fits(raw, CONTEXT)

    assert fixed == {"subtasks": [{"id": "a"}], "other": 1}
    assert fixed is not raw and raw == before


def test_the_refusal_stands_when_there_are_no_subtasks_to_keep():
    assert parts_dropped_when_fits({"subtasks": [], "parts": [_part("r")]}, CONTEXT) is None
    assert parts_dropped_when_fits({"parts": [_part("r")]}, CONTEXT) is None


def test_no_parts_means_nothing_to_drop():
    assert parts_dropped_when_fits({"subtasks": [{"id": "a"}]}, CONTEXT) is None


def test_the_note_names_the_rule_and_the_field():
    assert repair_note("parts_when_fits", "parts") == "System repair: parts_when_fits on parts"
