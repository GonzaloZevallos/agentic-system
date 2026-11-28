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


@pytest.mark.parametrize(
    ("reference", "path"),
    [
        ("https://github.com/o/r/issues/9", "repos/o/r/issues/9"),
        (
            "https://github.com/Some-Org/my_repo.js/issues/1234",
            "repos/Some-Org/my_repo.js/issues/1234",
        ),
        ("42", "repos/{owner}/{repo}/issues/42"),
        ("7", "repos/{owner}/{repo}/issues/7"),
    ],
)
def test_issue_target_turns_an_issue_reference_into_its_rest_path(reference, path):
    assert tracker_refs.issue_target(reference) == path


@pytest.mark.parametrize(
    "reference",
    [
        "https://github.com.evil/o/r/issues/9",
        "https://evil.com/github.com/o/r/issues/9",
        "https://github.com/o/../issues/9",
        "https://github.com/../r/issues/9",
        "https://github.com/./r/issues/9",
        "https://github.com/o/./issues/9",
        "https://github.com/o/../../issues/9",
        "https://github.com/o/r/../issues/9",
        "http://github.com/o/r/issues/9",
        "https://github.com:443/o/r/issues/9",
        "https://user@github.com/o/r/issues/9",
        "https://user:pw@github.com/o/r/issues/9",
        "https://github.com/o/r/pull/9",
        "https://github.com/o/r/issues/9/",
        "https://github.com/o/r/issues/9?x=1",
        "https://github.com/o/r/issues/9#c",
        "https://github.com/o/r/issues/09",
        "https://github.com/o/r/issues/0",
        "https://github.com/o/r/issues/",
        "https://github.com/o/issues/9",
        "https://github.com/o/r/x/issues/9",
        "https://github.com/o/r%2f/issues/9",
        "#9",
        "09",
        "0",
        "-1",
        "+3",
        "1.5",
        " 7",
        "7 ",
        "7\n",
        "https://github.com/o/r/issues/9\n",
        "",
        "GH-9",
        "٣",
        "https://github.com/o/r/issues/٣",
    ],
)
def test_issue_target_refuses_anything_else(reference):
    assert tracker_refs.issue_target(reference) is None
