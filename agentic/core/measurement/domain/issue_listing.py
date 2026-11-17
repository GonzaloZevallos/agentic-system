"""The repository's issues and pull requests as one listing (pure): what a task document's
times are looked up in, with no GitHub call per document."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from agentic.core.process.domain.tracker_refs import issue_number, pull_request_number

PAGE_SIZE = 100
# Core points the listing leaves in the account's quota: the pollers draw on it too.
QUOTA_RESERVE = 1000
# How long a cached page is trusted: a page that can still change (an open pull request, or
# the last page, where new items land) for a short window, a settled one for a long one.
OPEN_PAGE_WINDOW_S = 600
CLOSED_PAGE_WINDOW_S = 3 * 3600
# A number above the listed range is looked for again on the last page only if that page is
# at least this old: a number created just now may not be listed yet, and that is no failure.
ABOVE_RANGE_FLOOR_S = 60


@dataclass(frozen=True)
class ListedIssue:
    """One item of GitHub's issues listing, which also lists pull requests."""

    number: int
    created_at: str
    is_pr: bool
    pr_merged_at: str | None
    state: Literal["open", "closed"]


@dataclass(frozen=True)
class PageRecord:
    """One page as it was read: raw items, when, and whether it can still change."""

    read_at: datetime
    open: bool
    items: tuple[ListedIssue, ...]


@dataclass(frozen=True)
class TaskTimes:
    issue_created_at: str | None
    pr_created_at: str | None
    pr_merged_at: str | None


@dataclass(frozen=True)
class Missing:
    """The listing has no usable item for this number (`None`: the reference had none)."""

    number: int | None


def reserve_reached(remaining: int, reserve: int) -> bool:
    """Whether a read would leave the core quota under the reserve: stop before it."""
    return remaining <= reserve


def cost_line(product: str, calls: int, points: int, remaining: int | None) -> str:
    """What the backfill spent on GitHub; `remaining` is the last quota reading, if any."""
    left = "unknown" if remaining is None else str(remaining)
    return (
        f"agentic: github reads for {product}: {calls} calls ({points} core points), "
        f"core remaining {left}"
    )


def merge(items: Mapping[int, ListedIssue], page: Iterable[ListedIssue]) -> dict[int, ListedIssue]:
    """`items` plus `page`, keyed by number; a later read wins."""
    return {**items, **{item.number: item for item in page}}


def page_open(items: Iterable[ListedIssue], is_last: bool) -> bool:
    """Whether a page can still change: it is the last one, or holds a pull request that is
    open and unmerged. An open issue, a closed pull request or a merged one never do."""
    return is_last or any(i.is_pr and i.state == "open" and i.pr_merged_at is None for i in items)


def fresh(record: PageRecord, now: datetime) -> bool:
    """Whether the page may be served from the cache at `now`: read no later than `now` and
    younger than its window. Nothing else decides it."""
    age = (now - record.read_at).total_seconds()
    window = OPEN_PAGE_WINDOW_S if record.open else CLOSED_PAGE_WINDOW_S
    return 0 <= age < window


def complete(pages: Mapping[int, PageRecord]) -> bool:
    """Whether pages 1..L are all present and page L is short (an empty page is short)."""
    last = max(pages, default=0)
    if last == 0 or any(n not in pages for n in range(1, last)):
        return False
    return len(pages[last].items) < PAGE_SIZE


def listing_of(pages: Mapping[int, PageRecord]) -> dict[int, ListedIssue]:
    """The pages merged by number, the page read last winning over an earlier read."""
    items: dict[int, ListedIssue] = {}
    for _, record in sorted(pages.items(), key=lambda entry: (entry[1].read_at, entry[0])):
        items = merge(items, record.items)
    return items


def needed_numbers(documents: Iterable[tuple[str, str | None]]) -> frozenset[int]:
    """The issue and pull request numbers the `(task id, pull request reference)` pairs look
    up; a reference that names no number contributes nothing."""
    numbers: set[int] = set()
    for task_id, reference in documents:
        number = issue_number(task_id)
        if number is not None:
            numbers.add(number)
        if reference:
            try:
                numbers.add(pull_request_number(reference))
            except ValueError:
                pass
    return frozenset(numbers)


_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([a-z0-9-]+)/([a-z0-9._-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def repo_key(url: str | None) -> str | None:
    """`owner/repo`, lowercased, off a GitHub remote URL; `None` for anything that is not
    one, so a name that could escape a path never becomes a key. Never raises."""
    match = _REMOTE.match(url.strip()) if url else None
    if match is None:
        return None
    owner, repo = match.group(1).lower(), match.group(2).lower()
    return None if repo in {".", ".."} else f"{owner}/{repo}"


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
