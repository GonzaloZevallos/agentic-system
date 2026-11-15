"""The repository's issues and pull requests as one listing (pure): what a task document's
times are looked up in, with no GitHub call per document."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from agentic.core.process.domain.tracker_refs import issue_number, pull_request_number

PAGE_SIZE = 100


@dataclass(frozen=True)
class ListedIssue:
    """One item of GitHub's issues listing, which also lists pull requests."""

    number: int
    created_at: str
    is_pr: bool
    pr_merged_at: str | None


@dataclass(frozen=True)
class TaskTimes:
    issue_created_at: str | None
    pr_created_at: str | None
    pr_merged_at: str | None


@dataclass(frozen=True)
class Missing:
    """The listing has no usable item for this number (`None`: the reference had none)."""

    number: int | None


def merge(items: Mapping[int, ListedIssue], page: Iterable[ListedIssue]) -> dict[int, ListedIssue]:
    """`items` plus `page`, keyed by number; a later read wins."""
    return {**items, **{item.number: item for item in page}}


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
