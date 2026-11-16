import pytest

from agentic.core.measurement.application.listing_reads import (
    Listing,
    Stopped,
    read_listing,
)
from agentic.core.measurement.application.ports import (
    Page,
    PageUnreadable,
    QuotaReading,
    QuotaUnreadable,
    Spend,
)
from agentic.core.measurement.domain.issue_listing import (
    PAGE_SIZE,
    ListedIssue,
)


def item(number):
    return ListedIssue(number, f"t{number}", False, None)


class CountingIssueListing:
    """Serves `numbers` in pages of PAGE_SIZE and counts the reads of each page."""

    def __init__(self, numbers, unreadable=None, delete_after_page_1=None, quotas=None):
        self.numbers = list(numbers)
        self.unreadable = unreadable or {}
        self.delete_after_page_1 = delete_after_page_1
        self.quotas = quotas  # the readings in order; None: a plentiful quota every time
        self.reads: list[int] = []
        self.quota_reads = 0

    def rate_limit(self):
        self.quota_reads += 1
        if self.quotas is None:
            return QuotaReading(5000)
        return self.quotas[self.quota_reads - 1]

    def spent(self):
        return Spend(self.quota_reads + len(self.reads), len(self.reads))

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
    assert read_listing(listing) == Listing({}, Spend(2, 1), 5000)
    assert listing.reads == [1]


def test_a_transient_page_two_stops_with_its_cause_and_reads_nothing_after():
    listing = CountingIssueListing(range(1, 801), unreadable={2: "transient"})
    assert read_listing(listing) == Stopped("transient", Spend(4, 2), 5000)
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


def test_the_quota_reaching_the_reserve_before_page_3_stops_with_it_and_reads_no_more():
    listing = CountingIssueListing(
        range(1, 801), quotas=[QuotaReading(1500), QuotaReading(1001), QuotaReading(1000)]
    )
    assert read_listing(listing) == Stopped("reserve", Spend(5, 2), 1000)
    assert listing.reads == [1, 2]


def test_a_quota_just_over_the_reserve_reads_the_page_and_a_custom_reserve_is_honoured():
    assert isinstance(read_listing(CountingIssueListing([1], quotas=[QuotaReading(1001)])), Listing)
    stopped = read_listing(CountingIssueListing([1], quotas=[QuotaReading(50)]), reserve=50)
    assert stopped == Stopped("reserve", Spend(1, 0), 50)


def test_an_unreadable_first_quota_stops_before_any_page_with_no_reading():
    listing = CountingIssueListing(range(1, 801), quotas=[QuotaUnreadable()])
    assert read_listing(listing) == Stopped("quota_unreadable", Spend(1, 0), None)
    assert listing.reads == []


def test_an_unreadable_later_quota_keeps_the_last_reading():
    listing = CountingIssueListing(range(1, 801), quotas=[QuotaReading(4000), QuotaUnreadable()])
    assert read_listing(listing) == Stopped("quota_unreadable", Spend(3, 1), 4000)


@pytest.mark.parametrize(("items", "calls", "points"), [(799, 16, 8), (800, 18, 9), (801, 18, 9)])
def test_a_listing_costs_two_calls_and_one_point_a_page(items, calls, points):
    done = read_listing(CountingIssueListing(range(1, items + 1)))
    assert isinstance(done, Listing) and done.spend == Spend(calls, points)
    assert done.remaining == 5000
