from agentic.core.measurement.application.listing_reads import (
    Listing,
    Stopped,
    read_listing,
)
from agentic.core.measurement.application.ports import Page, PageUnreadable
from agentic.core.measurement.domain.issue_listing import (
    PAGE_SIZE,
    ListedIssue,
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
