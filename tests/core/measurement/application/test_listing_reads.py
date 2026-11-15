import pytest

from agentic.core.measurement.application.listing_reads import (
    Listing,
    Stopped,
    read_listing,
    times_for,
)
from agentic.core.measurement.application.ports import Page, PageUnreadable
from agentic.core.measurement.domain.issue_listing import (
    PAGE_SIZE,
    ListedIssue,
    Missing,
    TaskTimes,
    merge,
)


def item(number):
    return ListedIssue(number, f"t{number}", False, None)


class CountingIssueListing:
    """Serves `numbers` in pages of PAGE_SIZE and counts the reads of each page."""

    def __init__(self, numbers, unreadable=None, delete_after_page_1=None):
        self.numbers = list(numbers)
        self.unreadable = unreadable or {}
        self.delete_after_page_1 = delete_after_page_1
        self.reads: list[int] = []

    def page(self, n):
        self.reads.append(n)
        if n in self.unreadable:
            return PageUnreadable(self.unreadable[n])
        if n == 2 and self.delete_after_page_1 is not None:
            self.numbers.remove(self.delete_after_page_1)
        start = (n - 1) * PAGE_SIZE
        chunk = self.numbers[start : start + PAGE_SIZE]
        return Page([item(i) for i in chunk], len(chunk))


def test_800_items_are_read_in_nine_pages_each_once():
    listing = CountingIssueListing(range(1, 801))
    done = read_listing(listing)
    assert isinstance(done, Listing) and sorted(done.items) == list(range(1, 801))
    assert listing.reads == list(range(1, 10))


def test_a_listing_of_nothing_is_complete_after_one_page():
    listing = CountingIssueListing([])
    assert read_listing(listing) == Listing({})
    assert listing.reads == [1]


def test_a_transient_page_two_stops_with_its_cause_and_reads_nothing_after():
    listing = CountingIssueListing(range(1, 801), unreadable={2: "transient"})
    assert read_listing(listing) == Stopped("transient")
    assert listing.reads == [1, 2]


def test_an_item_deleted_between_page_reads_leaves_its_neighbour_out_once_each():
    # Page 1 is 1..100; deleting 50 before page 2 slides 101 into page 1's range, so 101 is
    # skipped: every number appears once and the skipped one is simply absent.
    listing = CountingIssueListing(range(1, 251), delete_after_page_1=50)
    done = read_listing(listing)
    assert isinstance(done, Listing)
    assert 50 in done.items  # read on page 1 before it vanished
    assert 101 not in done.items
    assert sorted(done.items) == [n for n in range(1, 251) if n != 101]
    assert listing.reads == [1, 2, 3]


def issue(number, created="2026-01-01T00:00:00Z"):
    return ListedIssue(number, created, False, None)


def pull(number, created="2026-01-02T00:00:00Z", merged="2026-01-03T00:00:00Z"):
    return ListedIssue(number, created, True, merged)


def keyed(*items):
    return merge({}, items)


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
