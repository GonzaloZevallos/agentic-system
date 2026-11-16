from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.measurement.domain.issue_listing import (
    ABOVE_RANGE_FLOOR_S,
    CLOSED_PAGE_WINDOW_S,
    OPEN_PAGE_WINDOW_S,
    PAGE_SIZE,
    QUOTA_RESERVE,
    ListedIssue,
    Missing,
    PageRecord,
    TaskTimes,
    complete,
    cost_line,
    fresh,
    listing_of,
    merge,
    needed_numbers,
    page_open,
    repo_key,
    reserve_reached,
    times_for,
)


def issue(number, created="2026-01-01T00:00:00Z", state="closed"):
    return ListedIssue(number, created, False, None, state)


def pull(number, created="2026-01-02T00:00:00Z", merged="2026-01-03T00:00:00Z", state="closed"):
    return ListedIssue(number, created, True, merged, state)


def keyed(*items):
    return merge({}, items)


def test_the_page_size_is_what_github_is_asked_for():
    assert PAGE_SIZE == 100


def test_the_reserve_is_a_thousand_core_points():
    assert QUOTA_RESERVE == 1000


@pytest.mark.parametrize(
    ("remaining", "stop"),
    [(QUOTA_RESERVE - 1, True), (QUOTA_RESERVE, True), (QUOTA_RESERVE + 1, False)],
)
def test_reserve_reached_when_remaining_is_at_or_under_the_reserve(remaining, stop):
    assert reserve_reached(remaining, QUOTA_RESERVE) is stop


def test_cost_line_names_the_product_the_counts_and_the_last_reading():
    assert (
        cost_line("apostrophe", 18, 9, 4321)
        == "agentic: github reads for apostrophe: 18 calls (9 core points), core remaining 4321"
    )


def test_cost_line_without_a_reading_says_unknown():
    assert cost_line("p", 0, 0, None) == (
        "agentic: github reads for p: 0 calls (0 core points), core remaining unknown"
    )


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


def at(seconds):
    return datetime(2026, 11, 16, tzinfo=UTC) + timedelta(seconds=seconds)


def record(read_at=0, open_=False, count=1):
    return PageRecord(at(read_at), open_, tuple(issue(i) for i in range(count)))


def test_the_windows_are_ten_minutes_for_open_pages_and_three_hours_for_closed_ones():
    assert (OPEN_PAGE_WINDOW_S, CLOSED_PAGE_WINDOW_S, ABOVE_RANGE_FLOOR_S) == (600, 10800, 60)


def test_a_page_with_an_open_pull_request_is_open():
    assert page_open([issue(1), pull(2, merged=None, state="open")], is_last=False)


@pytest.mark.parametrize(
    "item",
    [issue(1, state="open"), pull(2, merged=None, state="closed"), pull(3, state="closed")],
    ids=["open issue", "closed unmerged pull request", "merged pull request"],
)
def test_an_open_issue_a_closed_pull_request_and_a_merged_one_never_make_a_page_open(item):
    assert not page_open([item], is_last=False)


def test_the_last_page_is_always_open_even_when_empty():
    assert page_open([], is_last=True)


@pytest.mark.parametrize(
    ("open_", "age", "expected"),
    [
        (True, 599, True),
        (True, 600, False),
        (False, 600, True),
        (False, 3 * 3600 - 1, True),
        (False, 3 * 3600, False),
        (True, -1, False),
        (False, -1, False),
        (False, 0, True),
    ],
)
def test_fresh_is_the_age_against_the_window_and_never_for_a_future_read(open_, age, expected):
    assert fresh(record(0, open_), at(age)) is expected


def test_complete_needs_every_page_up_to_a_short_last_one():
    assert complete({1: record(count=PAGE_SIZE), 2: record(count=3)})
    assert not complete({})
    assert not complete({1: record(count=PAGE_SIZE)})
    assert not complete({1: record(count=PAGE_SIZE), 3: record(count=3)})


def test_complete_takes_an_empty_terminal_page_as_short():
    assert complete({1: record(count=PAGE_SIZE), 2: record(count=PAGE_SIZE), 3: record(count=0)})
    assert not complete({1: record(count=PAGE_SIZE), 2: record(count=PAGE_SIZE)})


def test_listing_of_lets_the_later_read_win_and_a_dropped_number_is_gone():
    old = PageRecord(at(0), False, (issue(1, "old"), issue(2, "old")))
    new = PageRecord(at(5), False, (issue(2, "new"),))
    assert {n: i.created_at for n, i in listing_of({1: new, 2: old}).items()} == {
        2: "new",
        1: "old",
    }
    assert sorted(listing_of({1: new})) == [2]


def test_needed_numbers_are_the_issue_and_pull_request_numbers_and_skip_what_does_not_parse():
    documents = [
        ("GH-5", "https://github.com/o/r/pull/9"),
        ("EVAL-1-a", None),
        ("GH-6", "not a url"),
        ("GH-7", ""),
    ]
    assert needed_numbers(documents) == frozenset({5, 9, 6, 7})


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("https://github.com/o/r", "o/r"),
        ("https://github.com/o/r.git", "o/r"),
        ("https://github.com/o/r/", "o/r"),
        ("git@github.com:o/r.git", "o/r"),
        ("https://github.com/Gonzalo-Z/Agentic.System", "gonzalo-z/agentic.system"),
        ("https://github.com/o_x/r", None),
        ("https://github.com/o/..", None),
        ("https://github.com/o/.", None),
        ("https://gitlab.com/o/r", None),
        ("https://github.com/o/r/extra", None),
        ("https://github.com/../r", None),
        ("", None),
        (None, None),
    ],
)
def test_repo_key_reads_a_github_remote_and_nothing_that_could_escape_a_path(url, key):
    assert repo_key(url) == key
