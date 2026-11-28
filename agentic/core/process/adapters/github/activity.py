"""`GhActivity`: the read of what merged and closed in the product's repository, over `gh`
(GH-1058, part 3a of GH-1055). The adapter of the `Activity` port the email digest reads.

One `gh pr list` or `gh issue list` search per read, run in the product's checkout, asking for
everything from `since` on (floored to the second, so the query never excludes a row the exact
`since` keeps) up to `RAW_LIMIT` rows. The rest is client side: rows with no instant are dropped,
instants are parsed to aware UTC, `since` is applied exactly (exclusive), rows are ordered by
`(instant, number)` and `limit` of them returned; `cut_at` is the instant of row `limit + 1`.

A raw answer of `RAW_LIMIT` rows may be missing the oldest ones, and a page that skipped them
would let the digest advance past a candidate it never saw, so it fails as `ActivityCeiling`.

Only `ActivityFailed` leaves, carrying the type name of what went wrong: nothing `gh` printed,
no message and no HTTP status travels, and the original is not chained.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ...application.ports import (
    ActivityFailed,
    ActivityPage,
    ActivityRow,
    GhCallLog,
    TrackerCallFailed,
)
from ...domain.github_access import GithubAccess
from .run import GhRetry, RateLimitLatch, Run, bounded_run, run_gh

ACTIVITY_READ_TIMEOUT_S: float = 60
"""One read's own bound: up to ten pages of a hundred rows in a single `gh` call."""
RAW_LIMIT = 1000
"""Rows asked of `gh`. A raw answer this long may have lost the oldest ones."""

_FIELD: dict[str, str] = {"pr": "mergedAt", "issue": "closedAt"}
_SEARCH: dict[str, str] = {"pr": "merged", "issue": "closed"}
_STATE: dict[str, str] = {"pr": "merged", "issue": "closed"}


class GhActivity:
    def __init__(
        self,
        product_dir: Path,
        *,
        retry: GhRetry | None,
        access: GithubAccess | None,
        calls: GhCallLog | None,
        run: Run = bounded_run,
    ):
        self._cwd = product_dir
        self._retry = retry
        self._access = access
        self._calls = calls
        self._run = run
        self._latch = RateLimitLatch()  # nothing reads it: a failed page is `ActivityFailed`

    def merged_prs(self, since: datetime, limit: int) -> ActivityPage:
        return self._page("pr", since, limit)

    def closed_issues(self, since: datetime, limit: int) -> ActivityPage:
        return self._page("issue", since, limit)

    def _page(self, kind: Literal["pr", "issue"], since: datetime, limit: int) -> ActivityPage:
        try:
            return self._read(kind, since, limit)
        except (TrackerCallFailed, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ActivityFailed(type(exc).__name__) from None
        except OSError as exc:
            raise ActivityFailed(type(exc).__name__) from None

    def _read(self, kind: Literal["pr", "issue"], since: datetime, limit: int) -> ActivityPage:
        field = _FIELD[kind]
        floored = _utc(since).replace(microsecond=0)
        raw = self._gh(
            kind,
            "list",
            "--state",
            _STATE[kind],
            "--search",
            f"{_SEARCH[kind]}:>={floored.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "--json",
            f"number,title,url,{field}",
            "--limit",
            str(RAW_LIMIT),
        )
        body = json.loads(raw)
        if not isinstance(body, list):
            raise TypeError("a list was expected")
        if len(body) >= RAW_LIMIT:
            raise ActivityFailed("ActivityCeiling")
        rows = [row for item in body if (row := _row(item, field)) is not None]
        rows = sorted((row for row in rows if row.instant > since), key=_key)
        cut_at = rows[limit].instant if len(rows) > limit else None
        return ActivityPage(tuple(rows[:limit]), cut_at)

    def _gh(self, *args: str) -> str:
        return run_gh(
            self._timed,
            args,
            cwd=self._cwd,
            not_installed="gh is not installed",
            failed=lambda _stderr: f"gh {' '.join(args[:2])} failed",
            log=lambda _line: None,
            latch=self._latch,
            retry=self._retry,
            retry_timeouts=True,
            access=self._access,
            write=False,
            calls=self._calls,
        )

    def _timed(self, args: Sequence[str], **kwargs: Any) -> Any:
        return self._run(args, **kwargs, timeout_s=ACTIVITY_READ_TIMEOUT_S)


def _key(row: ActivityRow) -> tuple[datetime, int]:
    return row.instant, row.number


def _utc(instant: datetime) -> datetime:
    """`instant` as aware UTC; a naive one is a `ValueError`."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("an instant with no time zone")
    return instant.astimezone(UTC)


def _row(item: object, field: str) -> ActivityRow | None:
    """One answer item as a row; `None` when it has no instant (an unmerged or reopened one)."""
    if not isinstance(item, dict):
        raise TypeError("an object was expected")
    stamp = item[field]
    if stamp is None:
        return None
    number, title, url = item["number"], item["title"], item["url"]
    if not isinstance(number, int) or not isinstance(title, str) or not isinstance(url, str):
        raise TypeError("a row with a field of the wrong type")
    if not isinstance(stamp, str):
        raise TypeError("an instant that is not text")
    return ActivityRow(number, title, url, _utc(datetime.fromisoformat(stamp)))
