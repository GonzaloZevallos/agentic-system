import pytest

from agentic.core.process.adapters.github import tracker
from agentic.core.process.domain import tracker_refs


@pytest.mark.parametrize(
    ("task_id", "number"),
    [("GH-12", 12), ("EVAL-12-abc", None), ("gh-12", None), ("GH-12-x", None), ("", None)],
)
def test_issue_number_reads_only_a_bare_gh_id(task_id, number):
    assert tracker_refs.issue_number(task_id) == number


@pytest.mark.parametrize(
    ("url", "number"),
    [("https://github.com/o/r/pull/7", 7), ("/pull/42", 42)],
)
def test_pull_request_number_reads_the_trailing_number(url, number):
    assert tracker_refs.pull_request_number(url) == number


@pytest.mark.parametrize("url", ["https://github.com/o/r/pull/7/", "https://github.com/o/r", ""])
def test_pull_request_number_rejects_what_does_not_end_in_a_number(url):
    with pytest.raises(ValueError, match="not a pull request URL"):
        tracker_refs.pull_request_number(url)


def test_the_tracker_keeps_exporting_both_names():
    assert tracker.issue_number is tracker_refs.issue_number
    assert tracker.pull_request_number is tracker_refs.pull_request_number
