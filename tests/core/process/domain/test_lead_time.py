"""`lead_time`: the one builder of the value a document stores (GH-599)."""

from __future__ import annotations

from agentic.core.process.domain import lead_time


def test_both_timestamps_read_make_the_known_form():
    assert lead_time.from_read("2026-11-01T10:00:00Z", "2026-11-02T10:00:00Z") == {
        "labeled_at": "2026-11-01T10:00:00Z",
        "merged_at": "2026-11-02T10:00:00Z",
    }


def test_a_missing_or_empty_timestamp_is_unreadable_never_a_guess():
    unreadable = {"unknown": "unreadable"}
    assert lead_time.from_read(None, "2026-11-02T10:00:00Z") == unreadable
    assert lead_time.from_read("2026-11-01T10:00:00Z", None) == unreadable
    assert lead_time.from_read("", "2026-11-02T10:00:00Z") == unreadable
    assert lead_time.from_read("2026-11-01T10:00:00Z", "") == unreadable


def test_no_merge_is_a_ready_patch_under_the_lead_time_key():
    assert lead_time.NO_MERGE == {"lead_time": {"unknown": "no_merge"}}
    assert lead_time.LEAD_TIME_KEY == "lead_time"


def test_only_no_merge_may_be_replaced():
    assert lead_time.replaceable({"unknown": "no_merge"}) is True
    assert lead_time.replaceable({"unknown": "unreadable"}) is False
    assert lead_time.replaceable({"labeled_at": "a", "merged_at": "b"}) is False
    assert lead_time.replaceable(None) is False
