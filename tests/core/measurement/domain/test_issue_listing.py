import pytest

from agentic.core.measurement.domain.issue_listing import (
    PAGE_SIZE,
    ListedIssue,
    Missing,
    TaskTimes,
    merge,
    times_for,
)


def issue(number, created="2026-01-01T00:00:00Z"):
    return ListedIssue(number, created, False, None)


def pull(number, created="2026-01-02T00:00:00Z", merged="2026-01-03T00:00:00Z"):
    return ListedIssue(number, created, True, merged)


def keyed(*items):
    return merge({}, items)


def test_the_page_size_is_what_github_is_asked_for():
    assert PAGE_SIZE == 100


def test_merge_keys_by_number_and_a_later_read_wins():
    first = merge({}, [issue(1, "a"), issue(2, "b")])
    second = merge(first, [issue(2, "c"), issue(3, "d")])
    assert {n: i.created_at for n, i in second.items()} == {1: "a", 2: "c", 3: "d"}


def test_merge_leaves_the_items_it_was_given_untouched():
    first = keyed(issue(1))
    merge(first, [issue(2)])
    assert list(first) == [1]


def test_a_gh_id_and_its_pull_request_give_all_three_times():
    items = keyed(issue(5), pull(9))
    assert times_for(items, "GH-5", "https://github.com/o/r/pull/9") == TaskTimes(
        "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"
    )


def test_an_unmerged_pull_request_has_no_merge_time():
    items = keyed(issue(5), pull(9, merged=None))
    found = times_for(items, "GH-5", "https://github.com/o/r/pull/9")
    assert isinstance(found, TaskTimes) and found.pr_merged_at is None


def test_an_id_that_is_not_gh_n_has_no_issue_time_and_is_not_a_failure():
    assert times_for(keyed(), "EVAL-12-abc", None) == TaskTimes(None, None, None)


def test_no_pull_request_reference_gives_no_pull_request_times():
    assert times_for(keyed(issue(5)), "GH-5", None) == TaskTimes("2026-01-01T00:00:00Z", None, None)


def test_a_gh_id_absent_from_the_listing_is_missing():
    assert times_for(keyed(issue(6)), "GH-5", None) == Missing(5)


def test_a_gh_id_that_is_a_pull_request_takes_that_items_created_at():
    found = times_for(keyed(pull(5, created="x")), "GH-5", None)
    assert found == TaskTimes("x", None, None)


def test_an_unparsable_pull_request_reference_is_missing():
    assert times_for(keyed(issue(5)), "GH-5", "https://github.com/o/r/pull/9/") == Missing(5)


def test_a_pull_request_number_absent_from_the_listing_is_missing():
    assert times_for(keyed(issue(5)), "GH-5", "https://github.com/o/r/pull/9") == Missing(9)


def test_a_pull_request_reference_to_an_issue_is_missing_never_the_issues_times():
    items = keyed(issue(5), issue(9))
    assert times_for(items, "GH-5", "https://github.com/o/r/pull/9") == Missing(9)


@pytest.mark.parametrize("reference", ["", "not a url"])
def test_an_unparsable_reference_with_no_number_is_missing_with_the_task_number(reference):
    assert times_for(keyed(issue(5)), "GH-5", reference) == Missing(5)


def test_an_unparsable_reference_on_an_id_with_no_number_is_missing_with_no_number():
    assert times_for(keyed(), "EVAL-1-a", "nope") == Missing(None)
