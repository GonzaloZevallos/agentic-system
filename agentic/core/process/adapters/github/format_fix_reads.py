"""`FormatFixReads` over the tracker and a git ancestry read (GH-1067).

`closing` reads one issue of the system's own repo and no other: the URL must be exactly
`https://github.com/<owner>/<repo>/issues/<n>` (an optional trailing `/`, no query, no fragment)
and its owner and repo must equal the system repo's slug, case-insensitively, before the tracker
is called, because an issue number alone does not say which repo it belongs to. Every failure is
`None`. `contains` is the injected ancestry read, unchanged: this package never imports the git
adapter's."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from ...application.ports import GhCallLog, IssueState, Tracker, TrackerCallFailed
from .run import Run, bounded_run, run_gh

_ISSUE_URL = re.compile(r"https://github\.com/([^/?#\s]+)/([^/?#\s]+)/issues/(\d+)/?")
_SLUG = re.compile(r"[^/\s]+/[^/\s]+")


class GhFormatFixReads:
    """`closing` through `tracker`, for the repo `slug()` names; `contains` through `ancestry`."""

    def __init__(
        self,
        tracker: Tracker,
        slug: Callable[[], str | None],
        ancestry: Callable[[str], bool | None],
        calls: GhCallLog | None = None,
    ) -> None:
        # The log of the `gh` launches behind `tracker` and `slug`, which are built with it. This
        # class launches none itself; it holds the log so a construction names it (GH-990).
        self._calls = calls
        self._tracker = tracker
        self._slug = slug
        self._ancestry = ancestry

    def closing(self, issue_url: str) -> IssueState | None:
        found = _ISSUE_URL.fullmatch(issue_url)
        if found is None:
            return None
        owner, repo, number = found.groups()
        slug = self._slug()
        if slug is None or slug.lower() != f"{owner}/{repo}".lower():
            return None
        try:
            return self._tracker.issue_state(int(number))
        except (TrackerCallFailed, ValueError, KeyError):
            return None

    def contains(self, sha: str) -> bool | None:
        return self._ancestry(sha)


class GhRepoSlug:
    """The `owner/repo` that `gh repo view` names in `cwd`, the way `gh issue view` resolves its
    repo there. Remembers a successful read only: a failure is `None` and the next call retries.
    Not thread-safe: two racing first calls each read once."""

    def __init__(self, cwd: Path, run: Run = bounded_run, calls: GhCallLog | None = None) -> None:
        self._cwd = cwd
        self._calls = calls
        self._run = run
        self._slug: str | None = None

    def __call__(self) -> str | None:
        if self._slug is None:
            self._slug = self._read()
        return self._slug

    def _read(self) -> str | None:
        try:
            raw = run_gh(
                self._run,
                ("repo", "view", "--json", "nameWithOwner"),
                cwd=self._cwd,
                not_installed="gh is not installed",
                failed=lambda stderr: "gh repo view failed",
                log=lambda _line: None,
                calls=self._calls,
            )
            name = json.loads(raw)["nameWithOwner"]
        except (ValueError, KeyError, TypeError):
            return None
        return name if isinstance(name, str) and _SLUG.fullmatch(name) else None
