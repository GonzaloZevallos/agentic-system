"""The memo of issue threads (GH-1220): what `store` keeps and when `served` gives it back.
Pure: no IO, no clock; `read_at` is passed in."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.domain.thread_memo import ThreadMemo, served, store

READ_AT = datetime(2026, 11, 28, 12, 0, 0, tzinfo=UTC)


def stamp_ago(seconds: int) -> str:
    return (READ_AT - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def test_an_equal_stamp_serves_what_was_stored() -> None:
    memo = ThreadMemo[str]()
    stamp = stamp_ago(120)
    store(memo, 7, stamp, ["a", "b"], READ_AT)
    assert served(memo, 7, stamp) == ("a", "b")


def test_a_different_stamp_misses() -> None:
    memo = ThreadMemo[str]()
    store(memo, 7, stamp_ago(120), ["a"], READ_AT)
    assert served(memo, 7, stamp_ago(90)) is None


def test_an_unknown_number_misses() -> None:
    memo = ThreadMemo[str]()
    store(memo, 7, stamp_ago(120), ["a"], READ_AT)
    assert served(memo, 8, stamp_ago(120)) is None


def test_an_empty_stamp_is_neither_stored_nor_served() -> None:
    memo = ThreadMemo[str]()
    store(memo, 7, "", ["a"], READ_AT)
    assert served(memo, 7, "") is None
    store(memo, 7, stamp_ago(120), ["a"], READ_AT)
    assert served(memo, 7, "") is None


@pytest.mark.parametrize(("age", "kept"), [(59, False), (60, True), (61, True)])
def test_a_stamp_is_kept_only_when_at_least_60_seconds_old(age: int, kept: bool) -> None:
    memo = ThreadMemo[str]()
    stamp = stamp_ago(age)
    store(memo, 7, stamp, ["a"], READ_AT)
    assert (served(memo, 7, stamp) is not None) is kept


def test_a_stamp_later_than_read_at_is_not_stored() -> None:
    memo = ThreadMemo[str]()
    stamp = (READ_AT + timedelta(seconds=300)).isoformat()
    store(memo, 7, stamp, ["a"], READ_AT)
    assert served(memo, 7, stamp) is None


@pytest.mark.parametrize("stamp", ["not a date", "2026-11-28T10:00:00"])
def test_an_unparseable_or_naive_stamp_is_not_stored(stamp: str) -> None:
    memo = ThreadMemo[str]()
    store(memo, 7, stamp, ["a"], READ_AT)
    assert served(memo, 7, stamp) is None


def test_a_rejected_store_keeps_the_earlier_read() -> None:
    memo = ThreadMemo[str]()
    old = stamp_ago(300)
    store(memo, 7, old, ["a"], READ_AT)
    store(memo, 7, stamp_ago(10), ["b"], READ_AT)
    assert served(memo, 7, old) == ("a",)


def test_mutating_the_input_or_the_output_leaves_the_stored_value_unchanged() -> None:
    memo = ThreadMemo[str]()
    stamp = stamp_ago(120)
    given = ["a", "b"]
    store(memo, 7, stamp, given, READ_AT)
    given.append("c")
    first = served(memo, 7, stamp)
    assert first == ("a", "b")
    handed = list(first or ())
    handed.append("d")
    assert served(memo, 7, stamp) == ("a", "b")
