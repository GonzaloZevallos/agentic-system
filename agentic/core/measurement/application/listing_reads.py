"""Read the whole listing, a page at a time, each page once, never under the quota reserve."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.issue_listing import (
    PAGE_SIZE,
    QUOTA_RESERVE,
    ListedIssue,
    merge,
    reserve_reached,
)
from .ports import IssueListing, PageUnreadable, QuotaUnreadable, Spend, StopCause


@dataclass(frozen=True)
class Listing:
    """Every item of the repository, keyed by number: always complete. `remaining` is the
    last core quota reading (`None`: none was taken)."""

    items: dict[int, ListedIssue]
    spend: Spend
    remaining: int | None


@dataclass(frozen=True)
class Stopped:
    """The reads stopped; nothing partial is offered."""

    cause: StopCause
    spend: Spend
    remaining: int | None


def read_listing(listing: IssueListing, reserve: int = QUOTA_RESERVE) -> Listing | Stopped:
    """Pages 1, 2, ... merged, until one is short. The quota is read before each page: an
    unreadable one or one at or under `reserve` stops it, as does the first unreadable page."""
    items: dict[int, ListedIssue] = {}
    remaining: int | None = None
    number = 1
    while True:
        quota = listing.rate_limit()
        if isinstance(quota, QuotaUnreadable):
            return Stopped("quota_unreadable", listing.spent(), remaining)
        remaining = quota.remaining
        if reserve_reached(remaining, reserve):
            return Stopped("reserve", listing.spent(), remaining)
        page = listing.page(number)
        if isinstance(page, PageUnreadable):
            return Stopped(page.cause, listing.spent(), remaining)
        items = merge(items, page.items)
        if page.size < PAGE_SIZE:
            return Listing(items, listing.spent(), remaining)
        number += 1
