from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.measurement.application.listing_reads import (
    Listing,
    Stopped,
    read_listing,
)
from agentic.core.measurement.application.ports import (
    NoCache,
    Page,
    PageUnreadable,
    QuotaReading,
    QuotaUnreadable,
    Spend,
)
from agentic.core.measurement.domain.issue_listing import (
    PAGE_SIZE,
    ListedIssue,
    PageRecord,
)

START = datetime(2026, 11, 16, tzinfo=UTC)


class Clock:
    """A controllable clock: every call gives the same moment until it is advanced."""

    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class MemoryCache:
    """A `ReadCache` in memory; `saves` counts the calls to `save`."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.saves = 0

    def load(self):
        return dict(self.pages)

    def save(self, pages):
        self.saves += 1
        self.pages = dict(pages)


def item(number, state="closed", pr=False, merged=None):
    return ListedIssue(number, f"t{number}", pr, merged, state)


class CountingIssueListing:
    """Serves `numbers` in pages of PAGE_SIZE and counts the reads of each page; `items`
    overrides the item a number is served as."""

    def __init__(self, numbers, unreadable=None, delete_after_page_1=None, quotas=None):
        self.numbers = list(numbers)
        self.unreadable = unreadable or {}
        self.delete_after_page_1 = delete_after_page_1
        self.quotas = quotas  # the readings in order; None: a plentiful quota every time
        self.items: dict[int, ListedIssue] = {}
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
        return Page([self.items.get(i) or item(i) for i in chunk], len(chunk))


def run(listing, cache=None, clock=None, needed=frozenset(), **kwargs):
    return read_listing(
        listing, NoCache() if cache is None else cache, clock or Clock(), needed, **kwargs
    )


def test_800_items_are_read_in_nine_pages_each_once():
    listing = CountingIssueListing(range(1, 801))
    done = run(listing)
    assert isinstance(done, Listing) and sorted(done.items) == list(range(1, 801))
    assert listing.reads == list(range(1, 10))


def test_a_listing_of_nothing_is_complete_after_one_page():
    listing = CountingIssueListing([])
    assert run(listing) == Listing({}, Spend(2, 1), 5000, 0)
    assert listing.reads == [1]


def test_a_transient_page_two_stops_with_its_cause_and_reads_nothing_after():
    listing = CountingIssueListing(range(1, 801), unreadable={2: "transient"})
    assert run(listing) == Stopped("transient", Spend(4, 2), 5000, 1)
    assert listing.reads == [1, 2]


def test_an_item_deleted_between_page_reads_leaves_its_neighbour_out_once_each():
    # Page 1 is 1..100; deleting 50 before page 2 slides 101 into page 1's range, so 101 is
    # skipped: every number appears once and the skipped one is simply absent.
    listing = CountingIssueListing(range(1, 251), delete_after_page_1=50)
    done = run(listing)
    assert isinstance(done, Listing)
    assert 50 in done.items  # read on page 1 before it vanished
    assert 101 not in done.items
    assert sorted(done.items) == [n for n in range(1, 251) if n != 101]
    assert listing.reads == [1, 2, 3]


def test_the_quota_reaching_the_reserve_before_page_3_stops_with_it_and_reads_no_more():
    listing = CountingIssueListing(
        range(1, 801), quotas=[QuotaReading(1500), QuotaReading(1001), QuotaReading(1000)]
    )
    assert run(listing) == Stopped("reserve", Spend(5, 2), 1000, 2)
    assert listing.reads == [1, 2]


def test_a_quota_just_over_the_reserve_reads_the_page_and_a_custom_reserve_is_honoured():
    assert isinstance(run(CountingIssueListing([1], quotas=[QuotaReading(1001)])), Listing)
    stopped = run(CountingIssueListing([1], quotas=[QuotaReading(50)]), reserve=50)
    assert stopped == Stopped("reserve", Spend(1, 0), 50, 0)


def test_an_unreadable_first_quota_stops_before_any_page_with_no_reading():
    listing = CountingIssueListing(range(1, 801), quotas=[QuotaUnreadable()])
    assert run(listing) == Stopped("quota_unreadable", Spend(1, 0), None, 0)
    assert listing.reads == []


def test_an_unreadable_later_quota_keeps_the_last_reading():
    listing = CountingIssueListing(range(1, 801), quotas=[QuotaReading(4000), QuotaUnreadable()])
    assert run(listing) == Stopped("quota_unreadable", Spend(3, 1), 4000, 1)


@pytest.mark.parametrize(("items", "calls", "points"), [(799, 16, 8), (800, 18, 9), (801, 18, 9)])
def test_a_listing_costs_two_calls_and_one_point_a_page(items, calls, points):
    done = run(CountingIssueListing(range(1, items + 1)))
    assert isinstance(done, Listing) and done.spend == Spend(calls, points)
    assert done.remaining == 5000


def test_a_second_run_within_the_window_over_closed_items_makes_no_call():
    listing, cache, clock = CountingIssueListing(range(1, 251)), MemoryCache(), Clock()
    assert isinstance(run(listing, cache, clock), Listing)
    clock.advance(300)
    before = (list(listing.reads), listing.quota_reads)
    done = run(listing, cache, clock)
    assert (listing.reads, listing.quota_reads) == before
    assert isinstance(done, Listing) and sorted(done.items) == list(range(1, 251))
    assert done.from_cache == 3


def test_a_run_stopped_by_the_reserve_on_page_3_reruns_with_no_call_for_pages_1_and_2():
    cache, clock = MemoryCache(), Clock()
    first = CountingIssueListing(
        range(1, 251), quotas=[QuotaReading(5000), QuotaReading(5000), QuotaReading(900)]
    )
    assert run(first, cache, clock) == Stopped("reserve", Spend(5, 2), 900, 2)
    assert sorted(cache.pages) == [1, 2]
    second = CountingIssueListing(range(1, 251))
    done = run(second, cache, clock)
    assert second.reads == [3] and second.quota_reads == 1
    assert isinstance(done, Listing) and sorted(done.items) == list(range(1, 251))
    assert done.from_cache == 2


def test_each_page_is_saved_as_soon_as_it_is_read():
    cache = MemoryCache()
    run(CountingIssueListing(range(1, 251)), cache)
    assert cache.saves == 3


def test_a_page_with_an_open_pull_request_is_read_again_after_ten_minutes_and_shows_it_merged():
    listing, cache, clock = CountingIssueListing(range(1, 251)), MemoryCache(), Clock()
    listing.items[5] = item(5, "open", pr=True)
    run(listing, cache, clock)
    listing.items[5] = item(5, "closed", pr=True, merged="2026-11-16T00:09:00Z")
    clock.advance(599)
    again = run(listing, cache, clock)
    assert isinstance(again, Listing) and again.items[5].pr_merged_at is None
    assert listing.reads == [1, 2, 3]
    clock.advance(1)
    later = run(listing, cache, clock)
    assert isinstance(later, Listing) and later.items[5].pr_merged_at == "2026-11-16T00:09:00Z"
    assert listing.reads == [1, 2, 3, 1, 3]  # page 1 (open PR) and the last page, not page 2


def test_an_open_issue_does_not_shorten_the_window():
    listing, cache, clock = CountingIssueListing(range(1, 251)), MemoryCache(), Clock()
    listing.items[5] = item(5, "open")
    run(listing, cache, clock)
    clock.advance(700)
    run(listing, cache, clock)
    assert listing.reads == [1, 2, 3, 3]  # only the last page expired


def test_a_page_read_in_the_future_is_never_served():
    clock = Clock()
    cache = MemoryCache({1: PageRecord(START + timedelta(hours=1), False, (item(1),))})
    listing = CountingIssueListing([1])
    run(listing, cache, clock)
    assert listing.reads == [1]


def numbers_cached(cache):
    return [i.number for _, record in sorted(cache.pages.items()) for i in record.items]


def test_an_issue_deleted_between_two_reads_leaves_each_number_exactly_once():
    listing, cache, clock = CountingIssueListing(range(1, 251)), MemoryCache(), Clock()
    listing.items[150] = item(150, "open", pr=True)  # page 2 expires after ten minutes
    run(listing, cache, clock)
    listing.numbers.remove(50)  # 101 slides onto page 1, which stays cached
    clock.advance(700)
    done = run(listing, cache, clock)
    assert listing.reads == [1, 2, 3, 2, 3]
    assert isinstance(done, Listing) and 101 not in done.items
    cached = numbers_cached(cache)
    assert cached == sorted(set(cached)) and cached[:100] == list(range(1, 101))


def test_a_deleted_issue_is_gone_once_every_page_has_been_read_again():
    listing, cache, clock = CountingIssueListing(range(1, 201)), MemoryCache(), Clock()
    run(listing, cache, clock)
    listing.numbers.remove(7)
    clock.advance(3 * 3600)
    done = run(listing, cache, clock)
    assert isinstance(done, Listing)
    assert sorted(done.items) == [n for n in range(1, 201) if n != 7]
    assert sorted(cache.pages) == [1, 2]  # the empty page 3 is no longer there


@pytest.mark.parametrize("count", [PAGE_SIZE, 2 * PAGE_SIZE])
def test_a_listing_ending_on_a_full_page_ends_with_an_empty_one_and_costs_nothing_again(count):
    listing, cache, clock = CountingIssueListing(range(1, count + 1)), MemoryCache(), Clock()
    run(listing, cache, clock)
    assert listing.reads[-1] == count // PAGE_SIZE + 1
    before = (len(listing.reads), listing.quota_reads)
    assert isinstance(run(listing, cache, clock), Listing)
    assert (len(listing.reads), listing.quota_reads) == before


def test_a_missing_high_number_with_a_fresh_cache_makes_no_call_on_the_second_run():
    listing, cache, clock = CountingIssueListing(range(1, 51)), MemoryCache(), Clock()
    first = run(listing, cache, clock, frozenset({99}))
    assert isinstance(first, Listing) and listing.reads == [1]  # read this run: no re-read
    clock.advance(30)
    assert isinstance(run(listing, cache, clock, frozenset({99})), Listing)
    assert listing.reads == [1]


def test_a_number_above_the_range_is_read_again_from_the_last_page_once_it_is_a_minute_old():
    listing, cache, clock = CountingIssueListing(range(1, 51)), MemoryCache(), Clock()
    run(listing, cache, clock, frozenset({60}))
    listing.numbers.append(60)
    clock.advance(60)
    done = run(listing, cache, clock, frozenset({60}))
    assert isinstance(done, Listing) and 60 in done.items
    assert listing.reads == [1, 1]


def test_a_number_above_the_range_that_is_still_missing_is_read_for_once_only():
    listing, cache, clock = CountingIssueListing(range(1, 51)), MemoryCache(), Clock()
    run(listing, cache, clock, frozenset({60}))
    clock.advance(120)
    done = run(listing, cache, clock, frozenset({60, 70}))
    assert isinstance(done, Listing) and 60 not in done.items
    assert listing.reads == [1, 1]


def test_a_number_missing_inside_a_fresh_complete_listing_costs_no_extra_call():
    listing, cache, clock = CountingIssueListing([1, 2, 4, 5]), MemoryCache(), Clock()
    run(listing, cache, clock, frozenset({3}))
    clock.advance(120)
    done = run(listing, cache, clock, frozenset({3}))
    assert isinstance(done, Listing) and 3 not in done.items
    assert listing.reads == [1]


def test_a_stop_reports_the_saved_pages_still_fresh_and_a_run_the_pages_served():
    cache, clock = MemoryCache(), Clock()
    run(CountingIssueListing(range(1, 251)), cache, clock)
    clock.advance(700)  # the last page expired
    listing = CountingIssueListing(range(1, 251), quotas=[QuotaReading(10)])
    assert run(listing, cache, clock) == Stopped("reserve", Spend(1, 0), 10, 2)
    done = run(CountingIssueListing(range(1, 251)), cache, clock)
    assert isinstance(done, Listing) and done.from_cache == 2


class TickingClock(Clock):
    """A clock that moves on `step` seconds at every reading, as a real one does."""

    def __init__(self, step):
        super().__init__()
        self.step = step

    def __call__(self):
        moment = self.now
        self.advance(self.step)
        return moment


def test_a_stop_counts_cached_pages_fresh_at_the_start_not_at_the_moment_of_the_stop():
    expiring = PageRecord(
        START - timedelta(seconds=3 * 3600 - 2),
        False,
        tuple(item(i) for i in range(1, PAGE_SIZE + 1)),
    )
    cache = MemoryCache({1: expiring})  # fresh at the start, expired 2 s later
    listing = CountingIssueListing(range(1, 251), quotas=[QuotaReading(10)])
    stopped = run(listing, cache, TickingClock(5))
    assert isinstance(stopped, Stopped) and stopped.cause == "reserve" and stopped.cached == 1


def test_a_stop_counts_the_pages_read_in_this_run_though_they_are_newer_than_the_start():
    listing = CountingIssueListing(
        range(1, 351), quotas=[QuotaReading(5000), QuotaReading(5000), QuotaReading(10)]
    )
    stopped = run(listing, MemoryCache(), TickingClock(5))
    assert isinstance(stopped, Stopped) and stopped.cached == 2
