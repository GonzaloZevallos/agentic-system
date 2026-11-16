"""The repository's issues listing through `gh api`: one REST page per call, raw JSON in."""

from __future__ import annotations

import json
from typing import Any

from agentic.core.process.application.ports import (
    TrackerRateLimited,
    TrackerTimedOut,
    TrackerTransient,
    TrackerUnreachable,
)

from ...application.ports import GhRead, Page, PageCause, PageUnreadable
from ...domain.issue_listing import PAGE_SIZE, ListedIssue

# `gh` resolves the placeholders against the checkout's repository, as `gh issue view` does.
_PATH = f"repos/{{owner}}/{{repo}}/issues?state=all&per_page={PAGE_SIZE}&sort=created&direction=asc"
_CAUSES: tuple[tuple[type[Exception], PageCause], ...] = (
    (TrackerRateLimited, "rate_limited"),
    (TrackerTimedOut, "timed_out"),
    (TrackerUnreachable, "unreachable"),
    (TrackerTransient, "transient"),
)


class GhIssueListing:
    def __init__(self, gh: GhRead):
        self._gh = gh

    def page(self, n: int) -> Page | PageUnreadable:
        try:
            raw = self._gh(("api", f"{_PATH}&page={n}"))
        except (ValueError, OSError) as exc:
            return PageUnreadable(next((c for t, c in _CAUSES if isinstance(exc, t)), "refused"))
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("not a list")
            items = [_item(entry) for entry in payload]
        except (ValueError, TypeError, KeyError):
            return PageUnreadable("refused")
        return Page(items, len(items))


def _item(entry: Any) -> ListedIssue:
    """One listed item; a `pull_request` object without an explicit `merged_at` is malformed."""
    number, created_at = entry["number"], entry["created_at"]
    if type(number) is not int or not isinstance(created_at, str):
        raise ValueError("malformed item")
    if "pull_request" not in entry:
        return ListedIssue(number, created_at, False, None)
    merged_at = entry["pull_request"]["merged_at"]
    if merged_at is not None and not isinstance(merged_at, str):
        raise ValueError("malformed merged_at")
    return ListedIssue(number, created_at, True, merged_at)
