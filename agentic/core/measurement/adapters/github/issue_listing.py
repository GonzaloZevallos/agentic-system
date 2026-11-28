"""The repository's issues listing through `gh api`: one REST page per call, raw JSON in."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agentic.core.process.application.ports import (
    TrackerRateLimited,
    TrackerTimedOut,
    TrackerTransient,
    TrackerUnreachable,
)

from ...application.ports import (
    GhRead,
    Page,
    PageCause,
    PageUnreadable,
    QuotaReading,
    QuotaUnreadable,
    Spend,
)
from ...domain.issue_listing import PAGE_SIZE, ListedIssue

# With no repo key, `gh` resolves the placeholders against the checkout's repository.
_QUERY = f"issues?state=all&per_page={PAGE_SIZE}&sort=created&direction=asc"
_CAUSES: tuple[tuple[type[Exception], PageCause], ...] = (
    (TrackerRateLimited, "rate_limited"),
    (TrackerTimedOut, "timed_out"),
    (TrackerUnreachable, "unreachable"),
    (TrackerTransient, "transient"),
)


class GhIssueListing:
    """`quota` is the `gh api rate_limit` document or `None`: the composition builds it from
    `read_rate_limit_document`, the one launcher of that read (GH-1157), so this adapter never
    spells the call out."""

    def __init__(self, gh: GhRead, repo: str | None, quota: Callable[[], object | None]):
        self._gh = gh
        self._quota = quota
        self._path = f"repos/{repo or '{owner}/{repo}'}/{_QUERY}"
        self._calls = 0
        self._points = 0

    def spent(self) -> Spend:
        return Spend(self._calls, self._points)

    def rate_limit(self) -> QuotaReading | QuotaUnreadable:
        """The core quota left; reading it costs no point but is a call."""
        self._calls += 1
        try:
            payload: Any = self._quota()
            remaining = payload["resources"]["core"]["remaining"]
        except (ValueError, OSError, TypeError, KeyError):
            return QuotaUnreadable()
        if type(remaining) is not int:
            return QuotaUnreadable()
        return QuotaReading(remaining)

    def page(self, n: int) -> Page | PageUnreadable:
        self._calls += 1
        self._points += 1
        try:
            raw = self._gh(("api", f"{self._path}&page={n}"))
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
    number, created_at, state = entry["number"], entry["created_at"], entry["state"]
    if (
        type(number) is not int
        or not isinstance(created_at, str)
        or state not in ("open", "closed")
    ):
        raise ValueError("malformed item")
    if "pull_request" not in entry:
        return ListedIssue(number, created_at, False, None, state)
    merged_at = entry["pull_request"]["merged_at"]
    if merged_at is not None and not isinstance(merged_at, str):
        raise ValueError("malformed merged_at")
    return ListedIssue(number, created_at, True, merged_at, state)
