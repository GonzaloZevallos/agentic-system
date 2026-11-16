"""Read the whole listing, a page at a time, never under the quota reserve, serving from the
read cache each page whose window has not run out."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..domain.issue_listing import (
    ABOVE_RANGE_FLOOR_S,
    PAGE_SIZE,
    QUOTA_RESERVE,
    ListedIssue,
    PageRecord,
    fresh,
    listing_of,
    page_open,
    reserve_reached,
)
from .ports import (
    IssueListing,
    PageUnreadable,
    QuotaUnreadable,
    ReadCache,
    Spend,
    StopCause,
)


@dataclass(frozen=True)
class Listing:
    """Every item of the repository, keyed by number: always complete. `remaining` is the
    last core quota reading (`None`: none was taken); `from_cache` the pages served with no
    read."""

    items: dict[int, ListedIssue]
    spend: Spend
    remaining: int | None
    from_cache: int


@dataclass(frozen=True)
class Stopped:
    """The reads stopped; nothing partial is offered. `cached` is the saved pages still
    fresh: what the next run need not read."""

    cause: StopCause
    spend: Spend
    remaining: int | None
    cached: int


class _Reader:
    def __init__(
        self,
        listing: IssueListing,
        cache: ReadCache,
        clock: Callable[[], datetime],
        reserve: int,
    ):
        self.listing = listing
        self.cache = cache
        self.clock = clock
        self.reserve = reserve
        self.now = clock()
        self.pages = cache.load()
        self.remaining: int | None = None
        self.from_cache = 0

    def walk(self, start: int, force: bool = False) -> StopCause | None:
        """Pages from `start` to the first short one, the cached fresh ones served and the
        rest read and saved at once; `force` reads `start` whatever the cache holds. Pages
        past the short one are dropped. The cause when a read stops it."""
        number = start
        while True:
            record = self.pages.get(number)
            if record is not None and not force and fresh(record, self.now):
                self.from_cache += 1
            else:
                cause = self._read(number)
                if cause is not None:
                    return cause
                record = self.pages[number]
            force = False
            if len(record.items) < PAGE_SIZE:
                break
            number += 1
        if any(n > number for n in self.pages):
            self.pages = {n: r for n, r in self.pages.items() if n <= number}
            self.cache.save(self.pages)
        return None

    def _read(self, number: int) -> StopCause | None:
        quota = self.listing.rate_limit()
        if isinstance(quota, QuotaUnreadable):
            return "quota_unreadable"
        self.remaining = quota.remaining
        if reserve_reached(quota.remaining, self.reserve):
            return "reserve"
        page = self.listing.page(number)
        if isinstance(page, PageUnreadable):
            return page.cause
        items = tuple(page.items)
        self.pages = {
            **self.pages,
            number: PageRecord(self.clock(), page_open(items, page.size < PAGE_SIZE), items),
        }
        self.cache.save(self.pages)
        return None

    def above_range(self, needed: frozenset[int]) -> bool:
        """Whether a needed number is past the highest listed one and the last page is old
        enough to be read again."""
        highest = max(listing_of(self.pages), default=0)
        last = self.pages[max(self.pages)]
        age = (self.now - last.read_at).total_seconds()
        return any(n > highest for n in needed) and age >= ABOVE_RANGE_FLOOR_S

    def stopped(self, cause: StopCause) -> Stopped:
        cached = sum(1 for record in self.pages.values() if fresh(record, self.clock()))
        return Stopped(cause, self.listing.spent(), self.remaining, cached)


def read_listing(
    listing: IssueListing,
    cache: ReadCache,
    clock: Callable[[], datetime],
    needed: frozenset[int],
    *,
    reserve: int = QUOTA_RESERVE,
) -> Listing | Stopped:
    """Pages 1, 2, ... merged until one is short. A cached page whose window has not run out
    is not read; the quota is read before each page that is: an unreadable one or one at or
    under `reserve` stops it, as does the first unreadable page. Each page read is saved at
    once, so a stop keeps what was read. A `needed` number above the listed range is looked
    for once more on the last page (and past it), if that page is a minute old or more."""
    reader = _Reader(listing, cache, clock, reserve)
    cause = reader.walk(1)
    if cause is None and reader.above_range(needed):
        cause = reader.walk(max(reader.pages), force=True)
    if cause is not None:
        return reader.stopped(cause)
    return Listing(listing_of(reader.pages), listing.spent(), reader.remaining, reader.from_cache)
