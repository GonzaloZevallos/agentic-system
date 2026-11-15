"""Read the whole listing, a page at a time, each page once."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.issue_listing import PAGE_SIZE, ListedIssue, merge
from .ports import IssueListing, PageCause, PageUnreadable


@dataclass(frozen=True)
class Listing:
    """Every item of the repository, keyed by number: always complete."""

    items: dict[int, ListedIssue]


@dataclass(frozen=True)
class Stopped:
    """A page could not be read; nothing partial is offered."""

    cause: PageCause


def read_listing(listing: IssueListing) -> Listing | Stopped:
    """Pages 1, 2, ... merged, until one is short; the first unreadable page stops it."""
    items: dict[int, ListedIssue] = {}
    number = 1
    while True:
        page = listing.page(number)
        if isinstance(page, PageUnreadable):
            return Stopped(page.cause)
        items = merge(items, page.items)
        if page.size < PAGE_SIZE:
            return Listing(items)
        number += 1
