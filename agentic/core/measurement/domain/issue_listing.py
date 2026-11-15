"""The repository's issues and pull requests as one listing (pure): what a task document's
times are looked up in, with no GitHub call per document."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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
