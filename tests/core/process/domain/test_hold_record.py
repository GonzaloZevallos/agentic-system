"""The holds record: `agentic.core.process.domain.hold_record` (GH-839, part B1)."""

from datetime import UTC, datetime

import pytest

from agentic.core.process.domain import hold_record as hr
from agentic.core.process.domain.hold_record import HoldUnknown, LabelEvent, Unreadable

MERGED = "2026-10-01T12:00:00Z"


def added(at):
    return LabelEvent(at, "added")


def removed(at):
    return LabelEvent(at, "removed")


def doc(record):
    return {hr.HOLDS_KEY: record}


def interval(start="2026-10-01T09:00:00Z", end="2026-10-01T10:00:00Z"):
    return {"start": start, "end": end}


# --- read ------------------------------------------------------------------------------------


def test_an_absent_key_reads_as_none():
    assert hr.read({}) is None
    assert hr.read({"other": 1}) is None


def test_the_intervals_read_as_aware_pairs_and_a_naive_bound_is_utc():
    got = hr.read(doc({"intervals": [interval(end="2026-10-01T10:00:00")]}))
    assert got == (
        (datetime(2026, 10, 1, 9, 0, tzinfo=UTC), datetime(2026, 10, 1, 10, 0, tzinfo=UTC)),
    )


def test_an_empty_list_is_read_with_no_holds_not_none():
    assert hr.read(doc({"intervals": []})) == ()


def test_zero_length_and_overlapping_intervals_are_valid():
    same = "2026-10-01T09:00:00Z"
    got = hr.read(
        doc({"intervals": [interval(same, same), interval(), interval("2026-10-01T09:30:00Z")]})
    )
    assert isinstance(got, tuple) and len(got) == 3


def test_an_unpaired_record_reads_as_unpaired():
    assert hr.read(doc({"unknown": "unpaired"})) == HoldUnknown("unpaired")


def test_an_unreadable_record_reads_as_unreadable():
    assert hr.read(doc({"unknown": "unreadable"})) == HoldUnknown("unreadable")


@pytest.mark.parametrize(
    "record",
    [
        None,
        "intervals",
        [interval()],
        {},
        {"intervals": [], "unknown": "unpaired"},
        {"other": []},
        {"unknown": "no_merge"},
        {"unknown": None},
        {"intervals": None},
        {"intervals": "x"},
        {"intervals": (interval(),)},
        {"intervals": {"start": "a"}},
        {"intervals": ["x"]},
        {"intervals": [None]},
        {"intervals": [{"start": "2026-10-01T09:00:00Z"}]},
        {"intervals": [{"end": "2026-10-01T09:00:00Z"}]},
        {"intervals": [interval(start="nope")]},
        {"intervals": [interval(end="nope")]},
        {"intervals": [interval(start="")]},
        {"intervals": [interval(end=None)]},
        {"intervals": [interval(start=5)]},
        {"intervals": [interval("2026-10-01T10:00:00Z", "2026-10-01T09:00:00Z")]},
        # one good interval does not save a malformed one: nothing is silently dropped.
        {"intervals": [interval(), interval(start="nope")]},
        {"intervals": [interval(), {"start": "2026-10-01T09:00:00Z"}]},
    ],
)
def test_every_malformed_record_reads_as_unreadable_never_a_dropped_interval(record):
    assert hr.read(doc(record)) == HoldUnknown("unreadable")


# --- from_events -----------------------------------------------------------------------------


def test_an_addition_and_the_next_removal_make_one_interval():
    events = [added("2026-10-01T09:00:00Z"), removed("2026-10-01T10:00:00Z")]
    assert hr.from_events(events, MERGED) == {
        "intervals": [{"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"}]
    }


def test_two_pairs_make_two_intervals_in_instant_order_even_given_shuffled():
    events = [
        removed("2026-10-01T10:00:00Z"),
        added("2026-10-01T11:00:00Z"),
        added("2026-10-01T09:00:00Z"),
        removed("2026-10-01T11:30:00Z"),
    ]
    assert hr.from_events(events, MERGED) == {
        "intervals": [
            {"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"},
            {"start": "2026-10-01T11:00:00Z", "end": "2026-10-01T11:30:00Z"},
        ]
    }


def test_a_hold_open_at_the_merge_is_clipped_to_the_merge_and_is_not_unknown():
    assert hr.from_events([added("2026-10-01T11:00:00Z")], MERGED) == {
        "intervals": [{"start": "2026-10-01T11:00:00Z", "end": MERGED}]
    }


def test_events_after_the_merge_are_ignored():
    events = [
        added("2026-10-01T09:00:00Z"),
        removed("2026-10-01T13:00:00Z"),
        added("2026-10-01T14:00:00Z"),
    ]
    assert hr.from_events(events, MERGED) == {
        "intervals": [{"start": "2026-10-01T09:00:00Z", "end": MERGED}]
    }


def test_a_removal_after_the_merge_with_nothing_open_is_not_unpaired():
    assert hr.from_events([removed("2026-10-01T13:00:00Z")], MERGED) == {"intervals": []}


def test_an_event_at_the_merge_instant_counts():
    events = [added("2026-10-01T11:00:00Z"), removed(MERGED)]
    assert hr.from_events(events, MERGED) == {
        "intervals": [{"start": "2026-10-01T11:00:00Z", "end": MERGED}]
    }


def test_a_removal_with_no_earlier_addition_is_unpaired():
    assert hr.from_events([removed("2026-10-01T09:00:00Z")], MERGED) == {"unknown": "unpaired"}


def test_a_second_removal_is_unpaired():
    events = [
        added("2026-10-01T09:00:00Z"),
        removed("2026-10-01T10:00:00Z"),
        removed("2026-10-01T10:30:00Z"),
    ]
    assert hr.from_events(events, MERGED) == {"unknown": "unpaired"}


def test_a_repeated_addition_is_ignored_and_the_hold_stays_open_from_the_first():
    events = [
        added("2026-10-01T09:00:00Z"),
        added("2026-10-01T09:30:00Z"),
        removed("2026-10-01T10:00:00Z"),
    ]
    assert hr.from_events(events, MERGED) == {
        "intervals": [{"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"}]
    }


def test_no_events_is_read_with_no_holds():
    assert hr.from_events([], MERGED) == {"intervals": []}


@pytest.mark.parametrize("bad", ["nope", "", "2026-13-45"])
def test_an_unparsable_event_instant_is_unreadable(bad):
    events = [added("2026-10-01T09:00:00Z"), removed(bad)]
    assert hr.from_events(events, MERGED) == {"unknown": "unreadable"}


@pytest.mark.parametrize("bad", ["nope", ""])
def test_an_unparsable_merge_instant_is_unreadable(bad):
    assert hr.from_events([added("2026-10-01T09:00:00Z")], bad) == {"unknown": "unreadable"}


def test_what_from_events_writes_reads_back_as_the_same_intervals():
    record = hr.from_events([added("2026-10-01T11:00:00Z")], MERGED)
    got = hr.read(doc(record))
    assert got == ((datetime(2026, 10, 1, 11, tzinfo=UTC), datetime(2026, 10, 1, 12, tzinfo=UTC)),)


# --- patch -----------------------------------------------------------------------------------


def test_patch_wraps_the_events_record_under_the_holds_key():
    events = [added("2026-10-01T09:00:00Z"), removed("2026-10-01T10:00:00Z")]
    assert hr.patch(events, MERGED) == {"holds": hr.from_events(events, MERGED)}


def test_patch_ignores_events_after_the_merge():
    patched = hr.patch([added("2026-10-01T14:00:00Z")], MERGED)
    assert patched == {"holds": {"intervals": []}}


def test_patch_of_an_unreadable_timeline_is_unknown_unreadable():
    assert hr.patch(Unreadable(), MERGED) == {"holds": {"unknown": "unreadable"}}
