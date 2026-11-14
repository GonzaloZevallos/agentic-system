"""`CommitCiReader` over `gh api graphql` (GH-704, GH-632 part 2c): the CI of one commit read by
its sha, for the stage0 `MasterWindow`.

One call per commit, through the tracker's own `graphql`, so a failed, stalled or missing `gh` is
already a `CiUnreadable`. The report is built by `tracker.commit_report`, the function a branch
read builds its own with, so the two reads cannot drift apart. No failed-step lookup, no retry and
no cache: the window asks for ten commits, each once."""

from __future__ import annotations

import re

from ...domain.ci import CiReport, CiUnreadable
from ...domain.rules import same_commit
from .tracker import COMMIT_CI_QUERY, GhTracker, _Unreadable, commit_report

# A full lower-case sha, nothing else: the oid is handed to `gh` as a string and the query asks
# for exactly that object, so a short, upper-case or odd one is refused before any call.
_OID = re.compile(r"[0-9a-f]{40}")


class GhCommitCi:
    """The CI report of a commit by its oid. `gh` resolves the repository from the tracker's
    `product_dir`. Never raises: every failure is `CiUnreadable`."""

    def __init__(self, tracker: GhTracker) -> None:
        self._tracker = tracker

    def read(self, oid: str) -> CiReport | CiUnreadable:
        """See `CommitCiReader.read`. `gh_unreadable` for an oid that is not 40 lower-case hex
        characters (no call is made), an answer without that commit, a cut one, or a commit that
        is not the one asked for."""
        if not _OID.fullmatch(oid):
            return CiUnreadable("gh_unreadable")
        # `-f`, never `-F`: `-F` turns a number into one and reads `@file`, and the oid is a string.
        answer = self._tracker.graphql(
            COMMIT_CI_QUERY,
            ("-F", "owner={owner}", "-F", "repo={repo}", "-f", f"oid={oid}"),
        )
        if isinstance(answer, CiUnreadable):
            return answer
        try:
            report = commit_report(answer["data"]["repository"]["object"])
        except (_Unreadable, KeyError, TypeError):
            return CiUnreadable("gh_unreadable")
        if not same_commit(report.head_sha, oid):
            return CiUnreadable("gh_unreadable")
        return report
