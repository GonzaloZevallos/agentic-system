import hashlib

import pytest

from agentic.core.process.domain import tracker_items

ITEM = {"kind": "human", "question": "Ship it?", "recommendation": "Yes, ship."}


def test_parse_accepts_a_valid_item_and_returns_a_new_dict_with_exactly_the_three_keys():
    raw = [{**ITEM, "extra": "dropped"}]
    parsed = tracker_items.parse(raw)
    assert parsed == [ITEM]
    assert parsed[0] is not raw[0]


def test_kinds_holds_only_human():
    assert tracker_items.KINDS == frozenset({"human"})


@pytest.mark.parametrize("kind", ["comment", "open_issue", "", None])
def test_parse_rejects_an_unknown_kind_and_says_to_write_it_as_human(kind):
    with pytest.raises(ValueError, match="human") as error:
        tracker_items.parse([{**ITEM, "kind": kind}])
    assert "{kind: human, question, recommendation}" in str(error.value)
    if kind:
        assert kind in str(error.value)


def test_parse_rejects_a_missing_kind():
    with pytest.raises(ValueError, match="human"):
        tracker_items.parse([{"question": "q", "recommendation": "r"}])


@pytest.mark.parametrize("missing", ["question", "recommendation"])
def test_parse_rejects_a_missing_field(missing):
    item = {k: v for k, v in ITEM.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        tracker_items.parse([item])


@pytest.mark.parametrize("field", ["question", "recommendation"])
@pytest.mark.parametrize("value", ["one\ntwo", "   ", ""])
def test_parse_rejects_a_field_that_is_not_one_line(field, value):
    with pytest.raises(ValueError, match="one non-empty line"):
        tracker_items.parse([{**ITEM, field: value}])


@pytest.mark.parametrize("raw", ["text", {"kind": "human"}, None, 3])
def test_parse_rejects_a_non_list(raw):
    with pytest.raises(ValueError, match="list"):
        tracker_items.parse(raw)


def test_parse_rejects_a_non_mapping_item():
    with pytest.raises(ValueError, match="human"):
        tracker_items.parse(["do the thing"])


def test_parse_of_an_empty_list_is_empty():
    assert tracker_items.parse([]) == []


def test_item_hash_is_pinned_and_covers_the_kind():
    assert tracker_items.item_hash(ITEM) == "28383afe2b80"
    assert tracker_items.item_hash(ITEM) == tracker_items.digest(
        ["human", "Ship it?", "Yes, ship."]
    )


def test_digest_gives_the_bytes_of_the_old_tracker_hash_formula():
    lines = ["  a ", "b", "c d"]
    expected = hashlib.sha256("\n".join(s.strip() for s in lines).encode()).hexdigest()[:12]
    assert tracker_items.digest(lines) == expected
