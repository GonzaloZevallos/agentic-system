"""`CiLogs` over the `gh` CLI (GH-449): the logs of the failed jobs of a pull request head.

The red jobs are the ones `ci_report` already resolves (their `detailsUrl` names the run and
the job), so no second query decides which jobs are red. Each log is read as a stream with a
byte cap and a timeout, never raises, and answers every failure with a closed cause: the text
of a log is for one prompt and is neither classified nor stored here.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from ...domain import rules
from ...domain.ci import (
    FLAKE_BLOCK_PREFIX,
    CiCheck,
    CiReport,
    CiUnreadable,
    JobLog,
    JobLogCause,
    current_runs,
    red_checks,
    runs_of,
)
from ...domain.landing import CI_LOG_JOBS
from .run import StreamRead, bounded_stream

# Declared, not measured: a failed step's log is a few hundred KB at most on this product's CI.
CI_LOG_JOB_BYTES = 1024 * 1024
# What `flake_block` keeps of one green job's whole log (GH-464): the LAST 4 MiB, where the block
# is printed. Declared, not measured; the ADR of the flake block says so.
CI_FLAKE_LOG_BYTES = 4 * 1024 * 1024
# One job's wait; the longest a landing waits on this is `CI_LOG_JOBS` times it.
CI_LOG_TIMEOUT_S = 30.0
# `https://github.com/<owner>/<repo>/actions/runs/<run>/job/<job>`: what a check run of Actions
# links to. Anything else (a commit status of another service) has no log to read here.
_JOB_LINK = re.compile(r"/actions/runs/(\d+)/job/(\d+)(?:[/?#]|$)")
# What `gh run view --log` prints per line: `<job>\t<step>\t<timestamp> <text>`, the first line of
# a log with a byte-order mark before the timestamp. Only a line with all three columns has a
# `<text>` to look at.
_LOG_TIMESTAMP = re.compile(r"\ufeff?\d{4}-\d\d-\d\dT[\d:.]+Z ")


class _Stream(Protocol):
    def __call__(
        self, args: Sequence[str], *, cwd: Path, max_bytes: int, timeout_s: float
    ) -> StreamRead: ...


def _tail_stream(args: Sequence[str], *, cwd: Path, max_bytes: int, timeout_s: float) -> StreamRead:
    return bounded_stream(args, cwd=cwd, max_bytes=max_bytes, timeout_s=timeout_s, tail=True)


class GhCiLogs:
    """`ci_report` is the tracker's own read of the pull request's CI, passed as a function so
    this class widens neither `Tracker` nor its fakes."""

    def __init__(
        self,
        product_dir: Path,
        ci_report: Callable[[str], CiReport | CiUnreadable],
        *,
        stream: _Stream = bounded_stream,
        max_bytes: int = CI_LOG_JOB_BYTES,
        timeout_s: float = CI_LOG_TIMEOUT_S,
        flake_stream: _Stream = _tail_stream,
        flake_max_bytes: int = CI_FLAKE_LOG_BYTES,
    ) -> None:
        self._product_dir = product_dir
        self._ci_report = ci_report
        self._stream = stream
        self._max_bytes = max_bytes
        self._timeout_s = timeout_s
        self._flake_stream = flake_stream
        self._flake_max_bytes = flake_max_bytes

    def failed_job_logs(self, reference: str, head: str) -> list[JobLog]:
        """See `CiLogs.failed_job_logs`: at most `CI_LOG_JOBS` entries, one per red job, none
        when nothing is red; a report that cannot be read is one `unreadable` entry."""
        try:
            report = self._ci_report(reference)
        except (OSError, ValueError, subprocess.SubprocessError):
            return [_unread("ci", "", "unreadable")]
        if isinstance(report, CiUnreadable):
            return [_unread("ci", "", "unreadable")]
        red = red_checks(report)[:CI_LOG_JOBS]
        if not rules.same_commit(report.head_sha, head):
            return [_unread(check.name, check.link, "other_head") for check in red]
        return [self._read(check) for check in red]

    def _read(self, check: CiCheck) -> JobLog:
        ids = _JOB_LINK.search(check.link)
        if ids is None:
            return _unread(check.name, check.link, "unreadable")
        args = ["gh", "run", "view", ids.group(1), "--job", ids.group(2), "--log-failed"]
        try:
            read = self._stream(
                args, cwd=self._product_dir, max_bytes=self._max_bytes, timeout_s=self._timeout_s
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return _unread(check.name, check.link, "unreadable")
        # A stream that filled the cap kept the whole of what the excerpt can use: a wait that
        # ran out while the rest was being counted and dropped (and the kill that followed) is
        # not a log that was not read.
        capped = len(read.data) >= self._max_bytes
        if read.timed_out and not capped:
            return _unread(check.name, check.link, "timed_out")
        if read.returncode != 0 and not capped:
            return _unread(check.name, check.link, "unreadable")
        text = read.data.decode("utf-8", errors="replace")
        if not text.strip():
            return _unread(check.name, check.link, "empty")
        return JobLog(check.name, check.link, text, len(read.data), "read", read.truncated)

    def flake_block(
        self, reference: str, head: str, jobs: Sequence[str]
    ) -> list[tuple[str, list[str]]] | CiUnreadable:
        """See `CiLogs.flake_block`: one `(job, lines)` per completed check of the report named
        in `jobs`; the whole answer is `CiUnreadable` when the report is not `head`'s or any
        read fails, because a log that was not read may hold the block."""
        try:
            report = self._ci_report(reference)
        except (OSError, ValueError, subprocess.SubprocessError):
            return CiUnreadable("gh_unavailable")
        if isinstance(report, CiUnreadable):
            return report
        if not rules.same_commit(report.head_sha, head):
            return CiUnreadable("gh_unreadable")
        current = current_runs(report)
        claimed = {c for runs in runs_of(current, jobs).values() for c in runs}
        wanted = [c for c in current if c in claimed and c.status == "COMPLETED"]
        found: list[tuple[str, list[str]]] = []
        for check in wanted:
            lines = self._read_flake_lines(check)
            if lines is None:
                return CiUnreadable("gh_unreadable")
            found.append((check.name, lines))
        return found

    def _read_flake_lines(self, check: CiCheck) -> list[str] | None:
        ids = _JOB_LINK.search(check.link)
        if ids is None:
            return None
        args = ["gh", "run", "view", ids.group(1), "--job", ids.group(2), "--log"]
        try:
            read = self._flake_stream(
                args,
                cwd=self._product_dir,
                max_bytes=self._flake_max_bytes,
                timeout_s=self._timeout_s,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        # The tail cap is no failure (the block is printed last); a wait that ran out or a
        # failing exit leaves a log whose end may be missing.
        if read.timed_out or read.returncode != 0:
            return None
        return _flake_lines(read.data.decode("utf-8", errors="replace"))


def _flake_lines(text: str) -> list[str]:
    """The lines whose text, after the `<job>\t<step>\t<timestamp> ` columns of `gh`, starts
    with `FLAKE_BLOCK_PREFIX`; a line cut by the tail cap has lost its columns and drops."""
    kept: list[str] = []
    for line in text.split("\n"):
        columns = line.rstrip("\r").split("\t", 2)
        if len(columns) != 3:
            continue
        stamp = _LOG_TIMESTAMP.match(columns[2])
        if stamp is not None and columns[2].startswith(FLAKE_BLOCK_PREFIX, stamp.end()):
            kept.append(columns[2][stamp.end() :])
    return kept


def _unread(job: str, link: str, cause: JobLogCause) -> JobLog:
    return JobLog(job, link, "", 0, cause, 0)
