"""GH-344: the sweep that closes the issues left open after a task's own merge, at the
`close_issues.sweep` seam with the fake tracker and documents."""

import pytest

from agentic.core.process.application import close_issues
from agentic.core.process.application.ports import TrackerUnreachable
from tests.fakes import memory_ports

MERGED = {"delivery_outcome": "merged", "merge_commit": "a" * 40}


def _ports(documents: dict[int, dict], queue: list[int]):
    ports = memory_ports()
    for number, fields in documents.items():
        ports.documents.save({"id": f"GH-{number}", **fields})
    ports.tracker._queue = queue
    return ports


def test_an_open_issue_of_a_merged_task_with_closed_at_set_is_closed():
    ports = _ports({339: {**MERGED, "closed_at": "2026-10-25T10:00:00+00:00"}}, [339])

    lines = close_issues.sweep(ports)

    assert ports.tracker.closed_issues == ["GH-339"]
    assert lines == ["agentic: close: GH-339: issue closed"]


def test_a_failed_close_is_retried_on_the_next_sweep():
    ports = _ports({339: {**MERGED, "closed_at": "t"}}, [339])
    ports.tracker.close_issue_failures = [ValueError("gh issue close failed: scripted")]

    first = close_issues.sweep(ports)
    second = close_issues.sweep(ports)

    assert first == ["agentic: close: GH-339: issue close failed: ValueError"]
    assert second == ["agentic: close: GH-339: issue closed"]
    assert ports.tracker.closed_issues == ["GH-339"]
    # Nothing was written to the document: the retry never depended on it.
    assert len(ports.documents.saved) == 1


def test_an_oserror_is_reported_by_its_class():
    ports = _ports({339: MERGED}, [339])
    ports.tracker.close_issue_failures = [OSError("no gh")]
    assert close_issues.sweep(ports) == ["agentic: close: GH-339: issue close failed: OSError"]


def test_an_issue_not_in_the_queue_is_not_touched():
    ports = _ports({339: MERGED}, [])
    assert close_issues.sweep(ports) == []
    assert ports.tracker.closed_issues == []


@pytest.mark.parametrize("outcome", ["merged", "merged_with_changes", "auto_merged"])
def test_each_merged_outcome_closes_its_issue(outcome):
    ports = _ports({5: {"delivery_outcome": outcome, "merge_commit": "b" * 40}}, [5])
    close_issues.sweep(ports)
    assert ports.tracker.closed_issues == ["GH-5"]


@pytest.mark.parametrize(
    "fields",
    [
        {"delivery_outcome": "closed", "merge_commit": "b" * 40},
        {"closed_at": "t"},
        {"status": "tracker_only", "closed_at": "t"},
        {"delivery_outcome": "merged"},
        {"delivery_outcome": "merged", "merge_commit": ""},
    ],
)
def test_an_outcome_that_is_not_a_merge_leaves_the_issue_open(fields):
    ports = _ports({5: fields}, [5])
    assert close_issues.sweep(ports) == []
    assert ports.tracker.closed_issues == []


def test_slices_that_closed_the_task_close_its_issue():
    ports = _ports({5: {"closed_by_slices": ["https://github.com/o/r/pull/3"]}}, [5])
    close_issues.sweep(ports)
    assert ports.tracker.closed_issues == ["GH-5"]


def test_an_open_issue_without_a_document_is_left_alone():
    ports = _ports({}, [7])
    assert close_issues.sweep(ports) == []
    assert ports.tracker.closed_issues == []


def test_an_unreadable_queue_skips_the_sweep_without_raising():
    ports = _ports({339: MERGED}, [339])
    ports.tracker.queue_error = ValueError("gh issue list failed")
    assert close_issues.sweep(ports) == ["agentic: close: issue sweep skipped: ValueError"]
    assert ports.tracker.closed_issues == []


def test_without_failure_reports_a_failed_close_says_nothing_and_a_closed_one_still_does():
    ports = _ports({5: MERGED, 6: MERGED}, [5, 6])
    ports.tracker.close_issue_failures = [OSError("no gh")]

    lines = close_issues.sweep(ports, report_failures=False)

    assert lines == ["agentic: close: GH-6: issue closed"]
    assert ports.tracker.closed_issues == ["GH-6"]


def test_without_failure_reports_an_unreadable_queue_says_nothing():
    ports = _ports({339: MERGED}, [339])
    ports.tracker.queue_error = OSError("gh missing")
    assert close_issues.sweep(ports, report_failures=False) == []
    assert ports.tracker.closed_issues == []


def test_without_failure_reports_a_transport_failure_of_the_queue_read_still_escapes():
    """The poller's own handler ends the round early on it, as for any other read."""
    ports = _ports({339: MERGED}, [339])
    ports.tracker.queue_error = TrackerUnreachable("gh issue list could not reach GitHub")
    with pytest.raises(TrackerUnreachable):
        close_issues.sweep(ports, report_failures=False)
    assert close_issues.sweep(ports) == ["agentic: close: issue sweep skipped: TrackerUnreachable"]
