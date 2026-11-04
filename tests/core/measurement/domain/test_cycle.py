"""Cycle and active time of a task document: `agentic.core.measurement.domain.cycle`."""

from typing import get_args

import pytest

from agentic.core.measurement.domain import cycle, scorecard
from agentic.core.process.domain.task import Status

START = "2026-10-20T10:00:00.000Z"
MID = "2026-10-20T10:30:00.000Z"
END = "2026-10-20T11:00:00.000Z"
CLOSED = "2026-10-20T14:00:00.000Z"

NOT_A_DELIVERY = {"split", "discarded", "tracker_only"}


def run(started=START, ended=END):
    record = {"role": "Developer", "version": 1, "cost_usd": 0.1, "session_id": "s", "model": "m"}
    if started is not None:
        record["started_at"] = started
    if ended is not None:
        record["ended_at"] = ended
    return record


def doc(**over):
    return {"status": "ready_for_review", "runs": [run()], **over}


@pytest.mark.parametrize("status", get_args(Status))
def test_cycle_class_of_every_status(status):
    expected_open = "excluded" if status in NOT_A_DELIVERY else "open"
    expected_closed = "excluded" if status in NOT_A_DELIVERY else "closed"
    assert cycle.cycle_class(doc(status=status)) == expected_open
    assert cycle.cycle_class(doc(status=status, closed_at=CLOSED)) == expected_closed


@pytest.mark.parametrize(
    ("label", "document", "expected"),
    [
        ("merged", doc(closed_at=CLOSED, delivery_outcome="merged"), "closed"),
        (
            "merged_with_changes",
            doc(closed_at=CLOSED, delivery_outcome="merged_with_changes"),
            "closed",
        ),
        ("auto_merged", doc(closed_at=CLOSED, delivery_outcome="auto_merged"), "closed"),
        ("closed unmerged", doc(closed_at=CLOSED, delivery_outcome="closed_unmerged"), "closed"),
        ("ready_for_review open", doc(), "open"),
        ("waiting_on_human", doc(status="waiting_on_human"), "open"),
        ("split", doc(status="split", closed_at=CLOSED), "excluded"),
        ("discarded", doc(status="discarded"), "excluded"),
        ("tracker_only", doc(status="tracker_only"), "excluded"),
        ("closed by slices", doc(closed_at=CLOSED, closed_by_slices="1,2"), "excluded"),
        ("no runs", doc(runs=[], closed_at=CLOSED), "excluded"),
        ("no runs key", {"status": "open", "closed_at": CLOSED}, "excluded"),
        ("run never started", doc(runs=[run(started=None)], closed_at=CLOSED), "excluded"),
        ("open and still running", doc(runs=[run(ended=None)]), "excluded"),
        ("closed whose run never ended", doc(runs=[run(ended=None)], closed_at=CLOSED), "closed"),
    ],
)
def test_cycle_class_cases(label, document, expected):
    assert cycle.cycle_class(document) == expected, label


def test_cycle_seconds_of_a_closed_task_runs_to_closed_at():
    document = doc(runs=[run(MID, END), run(START, MID)], closed_at=CLOSED)
    assert cycle.cycle_seconds(document) == 4 * 3600


def test_cycle_seconds_of_an_open_task_runs_to_the_last_ended_at():
    document = doc(runs=[run(START, MID), run(MID, END), run(END, None)])
    assert cycle.cycle_seconds(document) == 3600


def test_cycle_seconds_is_none_for_an_excluded_task_or_an_unreadable_bound():
    assert cycle.cycle_seconds(doc(status="discarded")) is None
    assert cycle.cycle_seconds(doc(runs=[run("not a date", END)], closed_at=CLOSED)) is None
    assert cycle.cycle_seconds(doc(closed_at="garbage")) is None
    # A close recorded before the first run is a corrupt document, not a negative time.
    assert cycle.cycle_seconds(doc(closed_at="2026-10-20T09:00:00.000Z")) is None


def test_active_seconds_is_the_union_of_overlapping_intervals():
    runs = [
        run("2026-10-20T10:00:00.000Z", "2026-10-20T10:10:00.000Z"),
        run("2026-10-20T10:05:00.000Z", "2026-10-20T10:15:00.000Z"),
        run("2026-10-20T11:00:00.000Z", "2026-10-20T11:05:00.000Z"),
    ]
    assert cycle.active_seconds({"runs": runs}) == 20 * 60


def test_active_seconds_ignores_incomplete_runs_and_is_none_without_any_complete_one():
    assert cycle.active_seconds({"runs": [run(ended=None), run(started=None)]}) is None
    assert cycle.active_seconds({}) is None
    assert cycle.active_seconds({"runs": [run(START, MID), run(ended=None)]}) == 1800


def test_the_scorecard_counts_closed_and_open_and_drops_a_task_missing_a_bound():
    documents = [
        doc(closed_at=CLOSED),  # 4 h cycle, 1 h active
        doc(runs=[run(START, MID)]),  # open: 0.5 h cycle, 0.5 h active
        doc(status="discarded"),  # excluded
        doc(runs=[run(ended=None)]),  # open with no end: excluded, not a zero
        doc(runs=[run(ended=None)], closed_at=CLOSED),  # closed: cycle 4 h, no active
    ]
    sc = scorecard.build([scorecard.task_figures(d) for d in documents])
    assert (sc.cycle_h.value, sc.cycle_h.denominator, sc.cycle_h.excluded) == (4.0, 3, 2)
    assert (sc.active_h.value, sc.active_h.denominator, sc.active_h.excluded) == (0.75, 2, 3)
    assert (sc.cycle_closed, sc.cycle_open) == (2, 1)
    empty = scorecard.build([])
    assert empty.cycle_h.value is None and empty.cycle_h.denominator == 0
    assert empty.active_h.value is None
