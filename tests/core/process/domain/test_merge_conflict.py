"""GH-492: `merge_conflict.applies`, the one question a recorded conflict answers: is it the
conflict of this exact head and master."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import merge_conflict

HEAD = "a" * 40
MASTER = "b" * 40
OTHER = "c" * 40
RECORD = {"head": HEAD, "master": MASTER, "at": "2026-11-04T12:20:00Z"}


def test_the_record_applies_to_the_exact_pair():
    assert merge_conflict.applies(RECORD, HEAD, MASTER) is True


def test_a_record_compares_by_commit_never_by_text():
    assert merge_conflict.applies({**RECORD, "head": HEAD[:12].upper()}, HEAD, MASTER) is True


@pytest.mark.parametrize(
    ("head", "master"),
    [(OTHER, MASTER), (HEAD, OTHER), (None, MASTER), (HEAD, None), ("", ""), (None, None)],
)
def test_it_is_ignored_once_either_sha_moves_or_is_unknown(head, master):
    assert merge_conflict.applies(RECORD, head, master) is False


@pytest.mark.parametrize(
    "record",
    [
        None,
        {},
        "not a mapping",
        {"head": HEAD, "at": "t"},
        {"master": MASTER, "at": "t"},
        {"head": None, "master": MASTER, "at": "t"},
        {"head": HEAD, "master": 7, "at": "t"},
        {"head": HEAD, "master": MASTER},
        {"head": HEAD, "master": MASTER, "at": ""},
    ],
)
def test_a_missing_or_malformed_record_applies_to_nothing(record):
    assert merge_conflict.applies(record, HEAD, MASTER) is False


def test_the_record_is_one_row_that_each_write_replaces():
    first = merge_conflict.record(HEAD, MASTER, "t1")
    second = merge_conflict.record(OTHER, MASTER, "t2")
    assert first == {"head": HEAD, "master": MASTER, "at": "t1"}
    assert second == {"head": OTHER, "master": MASTER, "at": "t2"}
    assert merge_conflict.applies(second, HEAD, MASTER) is False
