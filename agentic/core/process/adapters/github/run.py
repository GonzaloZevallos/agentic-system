"""The only place that starts `gh` (GH-266, ADR-0050).

`run_gh` is what the tracker and the inbox call; `bounded_run` is their default `run`: a
`subprocess.run` that cannot wait forever. A `gh` call that stalls (the network, GitHub,
a credential helper nobody answers) used to hold the poller's round, and the registry
lock, for good; now it ends as `TrackerTimedOut` and the next round retries.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ...application.ports import (
    StopFlag,
    TrackerCallFailed,
    TrackerRateLimited,
    TrackerRefused,
    TrackerTimedOut,
    TrackerTransient,
    TrackerUnreachable,
)

# Declared, not measured: a healthy call takes about a second. One constant to change if a
# real incident shows 60 s too long (no Configuration field, like FETCH_TIMEOUT_S).
GH_TIMEOUT_S: float = 60
# How long the wait for a killed `gh` to close its pipes and exit may take.
GH_DRAIN_S: float = 2

# What `gh` prints when it cannot reach GitHub, matched case-insensitively against its
# stderr (GH-290, ADR-0050 addendum). The only module that compares text from `gh` to
# classify. Captured from gh 2.100.0 on exit 1 (`GH_HOST=nonexistent.invalid gh api /`):
# "error connecting to nonexistent.invalid\ncheck your internet connection or
# https://githubstatus.com". A marker is added only with a stderr captured from the pinned
# `gh`: a missing one costs a poller restart, an extra one hides a real error.
GH_UNREACHABLE_MARKERS: tuple[str, ...] = ("error connecting to",)
# What `gh` prints when GitHub answered with a gateway error (GH-389), matched
# case-insensitively against its stderr. Captured from gh 2.100.0 on exit 1 in the GH-374 run
# (`.agentic/logs/GH-374-20261027T143450Z.log`, `gh issue comment`): "non-200 OK status code:
# 502 Bad Gateway body: ...". Since GH-438 the status classifier below (`http_status`) decides
# the retry; this marker stays as the 502 evidence the GH-389 tests pin. "connection reset by
# peer" and "secondary rate limit" are NOT here: no line of the pinned `gh`'s source or
# captured stderr is cited for them, and an unrecognised stderr stays an immediate failure.
GH_TRANSIENT_MARKERS: tuple[str, ...] = ("non-200 ok status code: 502",)
# The HTTP status `gh` prints (GH-438), read by `http_status` and nothing else: the one place
# that compares text from `gh` to classify. Three forms, each with its evidence.
#   (a) The pinned gh's source, `pkg/cmd/api/api.go`: `serverError = fmt.Sprintf("HTTP %d",
#       resp.StatusCode)` is the whole error line, printed as "gh: HTTP 502"; with a message
#       from the response body it is "gh: <message> (HTTP %d)".
#   (b) Captured from gh 2.100.0 on exit 1 (`gh api /repos/<owner>/<repo>/issues/0`):
#       "gh: Not Found (HTTP 404)".
#   (c) The GH-374 stderr cited above: "non-200 OK status code: 502 Bad Gateway body: ...".
# Exactly three digits, so "(HTTP 4040)" or a stray number is no status.
_HTTP_STATUS = re.compile(
    r"(?:\bgh: HTTP |\(HTTP |non-200 OK status code: )(\d{3})(?!\d)", re.IGNORECASE
)
# The statuses that are a gateway failing, not an answer to the question: retried under a
# policy (GH-438). 503 and 504 are backed by the pinned source's `HTTP %d` form, not by a
# captured failure; if `gh`'s wording changes the classifier does not retry, the safe side.
GH_TRANSIENT_STATUSES: frozenset[int] = frozenset({502, 503, 504})
# What `gh` prints when the primary rate limit is spent ("API rate limit exceeded ... (HTTP
# 403)"); the wait for it comes from `gh api rate_limit`, never from this text.
GH_RATE_LIMIT_MARKER = "api rate limit exceeded"
# The longest one retry wait or rate-limit wait may be: for `run`/`resume`, and for the
# `merge`/`land` children, which must not hold the lane of the runs exempt from the cap for
# more than a minute (GH-389, ADR-0050 addendum).
GH_RATE_WAIT_MAX_S: float = 900
GH_EXEMPT_WAIT_MAX_S: float = 60
# A wait sleeps in slices of at most this long, checking the `StopFlag` between them.
_SLICE_S: float = 1
# `gh`'s exit code for an authentication failure: never a connectivity failure.
GH_EXIT_AUTH = 4

Run = Callable[..., subprocess.CompletedProcess[str]]


def print_line(line: str) -> None:
    """The default `log`: stdout, flushed, the stream the poller's round lines use, so a
    `--detach` log file gets it too."""
    print(line, flush=True)


def _kill_group(proc: subprocess.Popen[str]) -> None:
    # `start_new_session` made the child its own group leader: `gh` and whatever it
    # started (a credential helper, a pager) die together.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


def bounded_run(
    args: Sequence[str],
    *,
    cwd: Path | str | None,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    timeout_s: float = GH_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run` that gives up after `timeout_s` seconds by raising the
    `TimeoutExpired` of that wait, having killed the child's whole process group first.
    Writes nothing: the line belongs to `run_gh`. Its own session and no stdin, so a
    prompt never waits on a terminal nobody is at."""
    pipe = subprocess.PIPE if capture_output else None
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=pipe,
        stderr=pipe,
        text=text,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as expired:
        _kill_group(proc)
        try:
            expired.output, expired.stderr = proc.communicate(timeout=GH_DRAIN_S)
        except subprocess.TimeoutExpired:
            # Something that left the group still holds the pipes: stop reading them.
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=GH_DRAIN_S)
        raise expired from None
    except BaseException:
        _kill_group(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=GH_DRAIN_S)
        raise
    returncode = proc.returncode
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def http_status(stderr: str) -> int | None:
    """The HTTP status `gh` printed in `stderr`, in one of the three forms `_HTTP_STATUS`
    cites; `None` for any other text."""
    found = _HTTP_STATUS.search(stderr)
    return None if found is None else int(found.group(1))


def _with_status(message: str, status: int | None) -> str:
    return message if status is None else f"{message} (HTTP {status})"


def _unreachable_marker(returncode: int, stderr: str) -> str | None:
    if returncode == GH_EXIT_AUTH:
        return None
    lowered = stderr.lower()
    return next((marker for marker in GH_UNREACHABLE_MARKERS if marker in lowered), None)


@dataclass(frozen=True)
class GhRetry:
    """The opt-in policy of a process that owns a task (GH-389, ADR-0050 addendum): up to
    `attempts` calls in all, `backoff_s[n]` seconds before call n + 2, no single wait longer
    than `max_wait_s`. `sleep` and `now` are injected so a test never waits for real; the
    wait sleeps in slices of at most a second and stops as soon as `stop` is set."""

    max_wait_s: float
    attempts: int = 3
    backoff_s: tuple[float, ...] = (2, 8, 30)
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time
    stop: StopFlag | None = None

    def wait(self, seconds: float) -> bool:
        """Sleeps `seconds`; `False` when the stop flag ended the wait early."""
        remaining = seconds
        while remaining > 0:
            if self.stop is not None and self.stop.is_set():
                return False
            step = min(_SLICE_S, remaining)
            self.sleep(step)
            remaining -= step
        return True

    def backoff(self, attempt: int) -> float:
        index = min(attempt - 1, len(self.backoff_s) - 1)
        return min(self.backoff_s[index], self.max_wait_s)


_Kind = Literal["timed_out", "unreachable", "transient", "rate_limited"]
_ERRORS: dict[_Kind, type[TrackerCallFailed]] = {
    "timed_out": TrackerTimedOut,
    "unreachable": TrackerUnreachable,
    "transient": TrackerTransient,
    "rate_limited": TrackerRateLimited,
}
_PHRASES: dict[_Kind, str] = {
    "timed_out": "timed out",
    "unreachable": "could not reach GitHub",
    "transient": "met a transient GitHub error",
    "rate_limited": "met the GitHub rate limit",
}


def _retryable_kind(returncode: int, stderr: str) -> _Kind | None:
    """The class of a non-zero exit a retry may change; `None` for anything else,
    authentication failures among them."""
    if returncode == GH_EXIT_AUTH:
        return None
    lowered = stderr.lower()
    if GH_RATE_LIMIT_MARKER in lowered:
        return "rate_limited"
    if http_status(stderr) in GH_TRANSIENT_STATUSES or any(
        marker in lowered for marker in GH_TRANSIENT_MARKERS
    ):
        return "transient"
    if any(marker in lowered for marker in GH_UNREACHABLE_MARKERS):
        return "unreachable"
    return None


def _rate_limit_reset(run: Run, cwd: Path) -> float | None:
    """The largest `reset` (epoch seconds) among the resources `gh api rate_limit` says are
    spent; `None` when the read fails or none is. Called with no policy: a rate-limited read
    must not wait on itself."""
    try:
        raw = run_gh(
            run,
            ("api", "rate_limit"),
            cwd=cwd,
            not_installed="gh is not installed",
            failed=lambda stderr: "gh api rate_limit failed",
            log=lambda line: None,
        )
        resources = json.loads(raw)["resources"]
        resets = [
            float(resource["reset"])
            for resource in resources.values()
            if resource["remaining"] == 0
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return max(resets) if resets else None


def _retry_wait(
    kind: _Kind, attempt: int, retry: GhRetry, run: Run, cwd: Path, call: str
) -> tuple[float, str | None]:
    """Seconds to wait before the next attempt, or the reason to give up instead."""
    if attempt >= retry.attempts:
        return 0, f"gh {call} {_PHRASES[kind]} on each of {retry.attempts} attempts"
    if kind != "rate_limited":
        return retry.backoff(attempt), None
    reset = _rate_limit_reset(run, cwd)
    if reset is None:
        return retry.backoff(attempt), None
    seconds = max(reset - retry.now(), 0)
    if seconds > retry.max_wait_s:
        return 0, (
            f"gh {call} {_PHRASES[kind]}; its reset is {seconds:g} s away, "
            f"over the {retry.max_wait_s:g} s bound"
        )
    return seconds, None


def run_gh(
    run: Run,
    args: Sequence[str],
    *,
    cwd: Path,
    not_installed: str,
    failed: Callable[[str], str],
    log: Callable[[str], None] = print_line,
    retry: GhRetry | None = None,
    retry_timeouts: bool = False,
) -> str:
    """One `gh <args>` call through `run`, its stdout stripped. Every failure is a
    `ValueError`: `not_installed` when there is no `gh`, `failed(stderr)` when it exits
    non-zero (each caller keeps its own wording; a `TrackerRefused` under a policy),
    `TrackerTimedOut` when `run` raised `TimeoutExpired`, `TrackerUnreachable` when `gh`
    exited saying it could not reach GitHub (`GH_UNREACHABLE_MARKERS`, never exit 4). Each
    of the last two writes exactly one line through `log` that names the call and nothing
    `gh` printed.

    With no `retry` that is all (ADR-0050). With one (GH-389), a gateway error
    (`GH_TRANSIENT_STATUSES`, 502/503/504), an unreachable GitHub, the primary rate limit and, when
    `retry_timeouts` (a read), a timeout are retried up to `retry.attempts` calls: the rate
    limit waits until the reset `gh api rate_limit` states, the rest after the backoff. One
    `log` line per wait, none with stderr. What runs out ends as `TrackerTransient`,
    `TrackerUnreachable`, `TrackerTimedOut` or `TrackerRateLimited` (also when the reset is
    over `retry.max_wait_s`, with no sleep, or the stop flag ends the wait).

    When `gh` printed an HTTP status (`http_status`), the refusal and the exhaustion message
    end with ` (HTTP nnn)` and the exception carries it as `status` (GH-438); nothing else
    `gh` printed is added."""
    call = " ".join(args[:2])
    attempt = 1
    while True:
        try:
            result = run(["gh", *args], cwd=cwd, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise ValueError(not_installed) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip()
            status = http_status(stderr)
            kind = None if retry is None else _retryable_kind(exc.returncode, stderr)
            if kind is None:
                marker = _unreachable_marker(exc.returncode, stderr)
                if marker is not None and retry is None:
                    message = f"gh {call} could not reach GitHub ({marker})"
                    log(f"agentic: {message}; this read fails and the next round retries")
                    raise TrackerUnreachable(message) from exc
                # A plain `ValueError` with no policy: ADR-0050, byte for byte as before.
                message = _with_status(failed(stderr), status)
                if retry is None:
                    raise ValueError(message) from exc
                raise TrackerRefused(message, status=status) from exc
            cause: BaseException = exc
        except subprocess.TimeoutExpired as exc:
            if retry is None or not retry_timeouts:
                message = f"gh {call} timed out after {exc.timeout:g} s"
                log(f"agentic: {message}; this read fails and the next round retries")
                raise TrackerTimedOut(message) from exc
            kind = "timed_out"
            status = None
            cause = exc
        else:
            return result.stdout.strip()
        assert retry is not None  # only a policy reaches a retryable kind
        seconds, giving_up = _retry_wait(kind, attempt, retry, run, cwd, call)
        if giving_up is None:
            log(
                f"agentic: gh {call} {_PHRASES[kind]}; "
                f"retrying in {seconds:g} s (attempt {attempt}/{retry.attempts})"
            )
            if retry.wait(seconds):
                attempt += 1
                continue
            giving_up = f"gh {call} {_PHRASES[kind]}; the wait was stopped"
        raise _ERRORS[kind](_with_status(giving_up, status), status=status) from cause


__all__ = [
    "GH_DRAIN_S",
    "GH_EXIT_AUTH",
    "GH_EXEMPT_WAIT_MAX_S",
    "GH_RATE_WAIT_MAX_S",
    "GH_TIMEOUT_S",
    "GH_TRANSIENT_MARKERS",
    "GH_TRANSIENT_STATUSES",
    "GH_UNREACHABLE_MARKERS",
    "GhRetry",
    "bounded_run",
    "http_status",
    "print_line",
    "run_gh",
]
