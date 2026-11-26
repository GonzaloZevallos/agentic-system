"""The two references a task document holds to GitHub, parsed (pure): its `GH-N` id and the
URL of its pull request. The one implementation; the tracker and the backfill share it."""

from __future__ import annotations

import re

_ISSUE = re.compile(r"^GH-(\d+)$")
_PULL_REQUEST_URL = re.compile(r"/pull/(\d+)$")
# A segment of a GitHub owner or repository name; `.` and `..` match it and are refused apart.
_SEGMENT = r"[A-Za-z0-9_.-]+"
_ISSUE_URL = re.compile(rf"https://github\.com/({_SEGMENT})/({_SEGMENT})/issues/([1-9][0-9]*)")
_ISSUE_NUMBER = re.compile(r"[1-9][0-9]*")


def issue_number(task_id: str) -> int | None:
    match = _ISSUE.match(task_id)
    return int(match.group(1)) if match else None


def pull_request_number(url: str) -> int:
    """The number `gh` needs, off the URL `deliver` stored in the document."""
    match = _PULL_REQUEST_URL.search(url)
    if not match:
        raise ValueError(f"not a pull request URL: {url}")
    return int(match.group(1))


def issue_target(reference: str) -> str | None:
    """The REST path of the issue `reference` names: `repos/<owner>/<repo>/issues/<n>` for
    `https://github.com/<owner>/<repo>/issues/<n>`, `repos/{owner}/{repo}/issues/<n>` (the
    placeholders `gh api` fills from the product's repository) for a bare positive integer;
    `None` for anything else. The whole reference must match: no other host, scheme, port,
    userinfo, query, fragment or trailing slash, no `.` or `..` segment, no leading zero. The
    only way a reference reaches a REST path (GH-1089)."""
    if (found := _ISSUE_URL.fullmatch(reference)) is not None:
        owner, repo, number = found.groups()
        if {owner, repo} & {".", ".."}:
            return None
        return f"repos/{owner}/{repo}/issues/{number}"
    if _ISSUE_NUMBER.fullmatch(reference) is not None:
        return f"repos/{{owner}}/{{repo}}/issues/{reference}"
    return None
