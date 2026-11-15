"""Read the whole listing, a page at a time, each page once."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agentic.core.process.domain.tracker_refs import issue_number, pull_request_number

from ..domain.issue_listing import PAGE_SIZE, ListedIssue, Missing, TaskTimes, merge
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


def times_for(
    items: Mapping[int, ListedIssue], task_id: str, pr_reference: str | None
) -> TaskTimes | Missing:
    """A task's times: an id that is not `GH-N` has no issue time (not a failure); a number
    the listing lacks, an unparsable reference or one naming a plain issue is `Missing`."""
    number = issue_number(task_id)
    issue_time = None
    if number is not None:
        issue = items.get(number)
        if issue is None:
            return Missing(number)
        issue_time = issue.created_at
    if pr_reference is None:
        return TaskTimes(issue_time, None, None)
    try:
        pr_number = pull_request_number(pr_reference)
    except ValueError:
        return Missing(number)
    pull = items.get(pr_number)
    if pull is None or not pull.is_pr:
        return Missing(pr_number)
    return TaskTimes(issue_time, pull.created_at, pull.pr_merged_at)
