"""The two references a task document holds to GitHub, parsed (pure): its `GH-N` id and the
URL of its pull request. The one implementation; the tracker and the backfill share it."""

from __future__ import annotations

import re

_ISSUE = re.compile(r"^GH-(\d+)$")
_PULL_REQUEST_URL = re.compile(r"/pull/(\d+)$")


def issue_number(task_id: str) -> int | None:
    match = _ISSUE.match(task_id)
    return int(match.group(1)) if match else None


def pull_request_number(url: str) -> int:
    """The number `gh` needs, off the URL `deliver` stored in the document."""
    match = _PULL_REQUEST_URL.search(url)
    if not match:
        raise ValueError(f"not a pull request URL: {url}")
    return int(match.group(1))
