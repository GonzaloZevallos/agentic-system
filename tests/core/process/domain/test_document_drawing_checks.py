"""The `drawing_checks` record survives `document.render` and `document.parse` (GH-766 part 5.1):
parse keeps it raw, because judging it belongs to the merge-facts reader."""

from typing import Any

import pytest

from agentic.core.process.domain import document

HEAD = "a" * 40


def _state(**extra: Any) -> dict[str, Any]:
    return {"id": "T-1", "title": "t", "description": "d", **extra}


def test_a_well_formed_record_round_trips():
    record = {
        "head": HEAD,
        "rows": [
            {"subject": "screen", "ref": "home", "verdict": "matches"},
            {"subject": "screen", "ref": "cart", "verdict": "departs", "settled": True},
            {"subject": "question", "ref": "q1", "verdict": "unverified"},
        ],
        "open": 1,
    }
    text = document.render(_state(drawing_checks=record))
    assert document.parse(text)["drawing_checks"] == record
    assert document.render({**_state(), **document.parse(text)}) == text


@pytest.mark.parametrize(
    "record",
    [
        {"head": HEAD, "rows": [], "open": "x"},
        {"head": HEAD, "rows": 3, "open": 0},
        {"head": 7, "rows": [{"verdict": 3}], "open": None},
        {},
    ],
    ids=["open-string", "rows-int", "wrong-types", "empty-mapping"],
)
def test_a_malformed_record_is_kept_as_written_never_dropped(record: dict[str, Any]):
    text = document.render(_state(drawing_checks=record))
    assert document.parse(text)["drawing_checks"] == record


def test_a_document_without_the_record_writes_no_key_and_is_unchanged():
    text = document.render(_state())
    assert "drawing_checks" not in text
    assert "drawing_checks" not in document.parse(text)
    assert text == document.render(_state(drawing_checks=None))
