"""`run.bounded_run` and `run.run_gh` (GH-266, ADR-0050)."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentic.core.process.adapters.github.run import GH_DRAIN_S, bounded_run, run_gh
from agentic.core.process.application.ports import (
    TrackerCallFailed,
    TrackerTimedOut,
    TrackerUnreachable,
)


def _gh(run, *args, log=None, cwd=Path(".")):
    lines: list[str] = []
    return (
        lambda: run_gh(
            run,
            args or ("issue", "view", "7"),
            cwd=cwd,
            not_installed="no gh",
            failed=lambda stderr: f"failed: {stderr}",
            log=log or lines.append,
        ),
        lines,
    )


def _raising(error):
    def run(argv, **kwargs):
        raise error

    return run


# --- run_gh with a fake run ------------------------------------------------------------


def test_run_gh_passes_the_argv_and_never_a_timeout(tmp_path):
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=" out\n", stderr="")

    call, lines = _gh(run, "issue", "list", cwd=tmp_path)
    assert call() == "out"
    assert seen["argv"] == ["gh", "issue", "list"]
    assert seen["kwargs"] == {
        "cwd": tmp_path,
        "capture_output": True,
        "text": True,
        "check": True,
    }
    assert lines == []


def test_run_gh_a_timeout_writes_one_line_and_raises_tracker_timed_out():
    expired = subprocess.TimeoutExpired(["gh"], 60, output="secret body", stderr="secret stderr")
    call, lines = _gh(_raising(expired), "issue", "view", "229", "--json", "body")
    with pytest.raises(TrackerTimedOut) as raised:
        call()
    assert str(raised.value) == "gh issue view timed out after 60 s"
    assert raised.value.__cause__ is expired
    assert isinstance(raised.value, ValueError)
    assert lines == [
        "agentic: gh issue view timed out after 60 s; this read fails and the next round retries"
    ]
    assert "secret" not in lines[0] + str(raised.value)


def test_run_gh_a_timeout_is_never_the_failed_wording():
    call, _ = _gh(_raising(subprocess.TimeoutExpired(["gh"], 1.5)))
    with pytest.raises(TrackerTimedOut, match="after 1.5 s") as raised:
        call()
    assert "failed" not in str(raised.value)


def test_run_gh_default_log_is_flushed_stdout(capsys):
    with pytest.raises(TrackerTimedOut):
        run_gh(
            _raising(subprocess.TimeoutExpired(["gh"], 60)),
            ("pr", "list"),
            cwd=Path("."),
            not_installed="x",
            failed=str,
        )
    captured = capsys.readouterr()
    assert captured.out.startswith("agentic: gh pr list timed out after 60 s")
    assert captured.err == ""


# Captured from the pinned gh 2.100.0 on exit 1: `GH_HOST=nonexistent.invalid gh api /`
# prints the same two lines for `api.github.com` (the incident, GH-290).
INCIDENT_STDERR = (
    "error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com"
)
CAPTURED_STDERR = (
    "error connecting to nonexistent.invalid\n"
    "check your internet connection or https://githubstatus.com\n"
)


@pytest.mark.parametrize("stderr", [INCIDENT_STDERR, CAPTURED_STDERR, INCIDENT_STDERR.upper()])
def test_run_gh_a_connect_failure_writes_one_line_and_raises_unreachable(stderr):
    error = subprocess.CalledProcessError(1, ["gh"], stderr=stderr)
    call, lines = _gh(_raising(error), "issue", "view", "7", "--json", "body")
    with pytest.raises(TrackerUnreachable) as raised:
        call()
    assert isinstance(raised.value, ValueError)
    assert raised.value.__cause__ is error
    assert lines == [
        "agentic: gh issue view could not reach GitHub (error connecting to); "
        "this read fails and the next round retries"
    ]
    assert "api.github.com" not in lines[0] + str(raised.value)
    assert "githubstatus" not in lines[0] + str(raised.value)


@pytest.mark.parametrize(
    ("returncode", "stderr"),
    [
        (1, "HTTP 401: Bad credentials"),
        (1, "Could not resolve to an Issue with the number of 9"),
        (4, INCIDENT_STDERR),
        (1, ""),
    ],
)
def test_run_gh_other_failures_stay_the_plain_failed_wording(returncode, stderr):
    error = subprocess.CalledProcessError(returncode, ["gh"], stderr=stderr)
    call, lines = _gh(_raising(error))
    with pytest.raises(ValueError) as raised:
        call()
    assert type(raised.value) is ValueError
    assert str(raised.value) == f"failed: {stderr.strip()}"
    assert lines == []


def test_run_gh_translates_the_other_failures_with_each_callers_wording():
    call, _ = _gh(_raising(FileNotFoundError("gh")))
    with pytest.raises(ValueError, match="^no gh$") as missing:
        call()
    assert isinstance(missing.value.__cause__, FileNotFoundError)
    call, _ = _gh(_raising(subprocess.CalledProcessError(1, ["gh"], stderr=" boom \n")))
    with pytest.raises(ValueError, match="^failed: boom$") as failed:
        call()
    assert not isinstance(failed.value, TrackerTimedOut)


# --- bounded_run keeps the subprocess.run contract -------------------------------------


@pytest.mark.parametrize(
    "script",
    [
        "print('hello')",
        "import sys; print('out'); print('err', file=sys.stderr); sys.exit(0)",
    ],
)
def test_bounded_run_returns_what_subprocess_run_returns(script, tmp_path):
    argv = [sys.executable, "-c", script]
    kwargs = {"cwd": tmp_path, "capture_output": True, "text": True, "check": True}
    ours, theirs = bounded_run(argv, **kwargs), subprocess.run(argv, **kwargs)
    assert (ours.args, ours.returncode, ours.stdout, ours.stderr) == (
        theirs.args,
        theirs.returncode,
        theirs.stdout,
        theirs.stderr,
    )


def test_bounded_run_raises_called_process_error_with_stdout_and_stderr(tmp_path):
    argv = [
        sys.executable,
        "-c",
        "import sys; print('o'); print('e', file=sys.stderr); sys.exit(3)",
    ]
    with pytest.raises(subprocess.CalledProcessError) as raised:
        bounded_run(argv, cwd=tmp_path, capture_output=True, text=True, check=True)
    assert (raised.value.returncode, raised.value.stdout, raised.value.stderr) == (3, "o\n", "e\n")
    unchecked = bounded_run(argv, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert unchecked.returncode == 3


def test_bounded_run_lets_a_missing_binary_through(tmp_path):
    with pytest.raises(FileNotFoundError):
        bounded_run(
            ["no-such-binary-gh266"], cwd=tmp_path, capture_output=True, text=True, check=True
        )


def test_bounded_run_non_utf8_output_fails_as_subprocess_run_does(tmp_path):
    argv = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')"]
    kwargs = {"cwd": tmp_path, "capture_output": True, "text": True, "check": True}
    with pytest.raises(UnicodeDecodeError):
        subprocess.run(argv, **kwargs)
    with pytest.raises(UnicodeDecodeError):
        bounded_run(argv, **kwargs)


# --- bounded_run against a real stall --------------------------------------------------


def _alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_stalled_call_raises_and_its_whole_process_group_dies(tmp_path):
    pidfile = tmp_path / "pgid"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        bounded_run(
            ["sh", "-c", 'echo $$ > "$1"; sleep 30 & wait', "_", str(pidfile)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout_s=0.2,
        )
    assert raised.value.timeout == 0.2
    assert time.monotonic() - started < 0.2 + 2 * GH_DRAIN_S + 2
    pgid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while _alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pgid)


def test_a_stalled_call_whose_child_left_the_group_is_still_bounded(tmp_path):
    pidfile = tmp_path / "stray"
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            bounded_run(
                ["sh", "-c", 'setsid sleep 30 & echo $! > "$1"; wait', "_", str(pidfile)],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=True,
                timeout_s=0.2,
            )
        assert time.monotonic() - started < 0.2 + 2 * GH_DRAIN_S + 2
    finally:
        if pidfile.exists() and pidfile.read_text().strip():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


# --- run_gh with a retry policy (GH-389) -------------------------------------------------

BAD_GATEWAY = 'gh issue comment failed: non-200 OK status code: 502 Bad Gateway body: "<html>"'
RATE_LIMITED = "gh: API rate limit exceeded for user ID 1. (HTTP 403)"


class _Clock:
    """A clock the injected `sleep` moves, so no test waits for real."""

    def __init__(self, start: float = 1_000.0):
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class _Stop:
    def __init__(self, after: int | None = None, clock: _Clock | None = None):
        self.after, self.clock = after, clock

    def is_set(self) -> bool:
        return self.after is not None and len(self.clock.sleeps) >= self.after

    def set(self) -> None:  # pragma: no cover - the policy only reads it
        raise AssertionError


def _script(*outcomes):
    """A fake `run`: each call plays the next outcome (an exception is raised, a string is
    the stdout); the argv of every call is kept."""
    calls: list[list[str]] = []
    queue = list(outcomes)

    def run(argv, **kwargs):
        calls.append(list(argv))
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(argv, 0, stdout=outcome, stderr="")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _failed(stderr, code=1):
    return subprocess.CalledProcessError(code, ["gh"], output="", stderr=stderr)


def _policy(clock, *, cap=900, stop=None):
    from agentic.core.process.adapters.github.run import GhRetry

    return GhRetry(max_wait_s=cap, sleep=clock.sleep, now=clock.now, stop=stop)


def _retrying(run, clock, *, cap=900, stop=None, read=False, args=("issue", "comment", "7")):
    lines: list[str] = []
    call = lambda: run_gh(  # noqa: E731
        run,
        args,
        cwd=Path("."),
        not_installed="no gh",
        failed=lambda stderr: f"failed: {stderr}",
        log=lines.append,
        retry=_policy(clock, cap=cap, stop=stop),
        retry_timeouts=read,
    )
    return call, lines


def test_a_502_then_success_returns_the_stdout_after_the_backoff():
    clock = _Clock()
    run = _script(_failed(BAD_GATEWAY), " ok\n")
    call, lines = _retrying(run, clock)
    assert call() == "ok"
    assert len(run.calls) == 2
    assert sum(clock.sleeps) == 2
    assert len(lines) == 1 and "gh issue comment" in lines[0] and "502" not in lines[0]


def test_a_persistent_502_is_transient_after_three_attempts_and_names_no_stderr():
    from agentic.core.process.application.ports import TrackerTransient

    clock = _Clock()
    run = _script(*[_failed(BAD_GATEWAY)] * 3)
    call, lines = _retrying(run, clock)
    with pytest.raises(TrackerTransient) as raised:
        call()
    assert len(run.calls) == 3
    assert sum(clock.sleeps) == 2 + 8
    assert "Bad Gateway" not in str(raised.value) and "3 attempts" in str(raised.value)
    assert isinstance(raised.value, ValueError)
    assert len(lines) == 2 and all("Bad Gateway" not in line for line in lines)


def test_an_unrecognised_failure_is_refused_at_once_and_never_retried():
    from agentic.core.process.application.ports import TrackerRefused

    clock = _Clock()
    run = _script(_failed("HTTP 404: Not Found"))
    call, lines = _retrying(run, clock)
    with pytest.raises(TrackerRefused, match="failed: HTTP 404"):
        call()
    assert len(run.calls) == 1 and clock.sleeps == [] and lines == []


def test_an_auth_failure_is_never_retried_even_with_a_marker_in_it():
    clock = _Clock()
    run = _script(_failed("error connecting to api.github.com", code=4))
    call, _ = _retrying(run, clock)
    with pytest.raises(ValueError, match="failed:"):
        call()
    assert len(run.calls) == 1 and clock.sleeps == []


def test_an_unreachable_github_retries_under_the_policy_and_ends_unreachable():
    clock = _Clock()
    run = _script(*[_failed("error connecting to api.github.com")] * 3)
    call, _ = _retrying(run, clock)
    with pytest.raises(TrackerUnreachable):
        call()
    assert len(run.calls) == 3


def test_a_missing_gh_is_never_retried():
    clock = _Clock()
    run = _script(FileNotFoundError("gh"))
    call, _ = _retrying(run, clock)
    with pytest.raises(ValueError, match="no gh") as raised:
        call()
    assert type(raised.value) is ValueError and clock.sleeps == []


def test_a_read_timeout_is_retried_and_a_write_timeout_is_not():
    expired = subprocess.TimeoutExpired(["gh"], 60)
    clock = _Clock()
    read = _script(expired, "[]")
    call, _ = _retrying(read, clock, read=True, args=("issue", "list"))
    assert call() == "[]" and len(read.calls) == 2

    write = _script(expired, "never")
    call, lines = _retrying(write, _Clock(), read=False)
    with pytest.raises(TrackerTimedOut, match="timed out after 60 s"):
        call()
    assert len(write.calls) == 1
    assert lines == [
        "agentic: gh issue comment timed out after 60 s; this read fails and the next round retries"
    ]


def test_a_rate_limit_waits_until_the_reset_the_api_gives():
    clock = _Clock(1_000)
    limits = json.dumps(
        {
            "resources": {
                "core": {"remaining": 0, "reset": 1_040},
                "graphql": {"remaining": 0, "reset": 1_090},
                "search": {"remaining": 30, "reset": 5_000},
            }
        }
    )
    run = _script(_failed(RATE_LIMITED), limits, "done")
    call, lines = _retrying(run, clock)
    assert call() == "done"
    assert run.calls[1] == ["gh", "api", "rate_limit"]
    assert sum(clock.sleeps) == pytest.approx(90)
    assert all(step <= 1 for step in clock.sleeps)
    assert len(lines) == 1 and "rate limit" in lines[0]


def test_the_rate_limit_read_has_no_policy_of_its_own():
    # The read answers a rate limit too: it must fall back to the backoff, not recurse.
    clock = _Clock()
    run = _script(_failed(RATE_LIMITED), _failed(RATE_LIMITED), "done")
    call, _ = _retrying(run, clock)
    assert call() == "done"
    assert [c for c in run.calls if c[1:] == ["api", "rate_limit"]] == [["gh", "api", "rate_limit"]]
    assert sum(clock.sleeps) == 2


def test_a_rate_limit_with_no_resource_at_zero_uses_the_backoff():
    clock = _Clock()
    run = _script(_failed(RATE_LIMITED), '{"resources": {"core": {"remaining": 5, "reset": 1}}}', "x")
    call, _ = _retrying(run, clock)
    assert call() == "x" and sum(clock.sleeps) == 2


def test_a_reset_over_the_cap_raises_rate_limited_without_sleeping():
    from agentic.core.process.application.ports import TrackerRateLimited

    clock = _Clock(1_000)
    run = _script(_failed(RATE_LIMITED), '{"resources": {"core": {"remaining": 0, "reset": 1100}}}')
    call, _ = _retrying(run, clock, cap=60)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert clock.sleeps == [] and "60 s" in str(raised.value)
    assert "API rate limit" not in str(raised.value)


def test_a_stop_flag_set_during_a_wait_ends_it_early_with_the_original_class():
    from agentic.core.process.application.ports import TrackerTransient

    clock = _Clock()
    stop = _Stop(after=1, clock=clock)
    run = _script(_failed(BAD_GATEWAY), "never")
    call, _ = _retrying(run, clock, stop=stop)
    with pytest.raises(TrackerTransient):
        call()
    assert len(run.calls) == 1
    assert sum(clock.sleeps) == 1


def test_a_wait_never_exceeds_the_cap():
    clock = _Clock()
    run = _script(*[_failed(BAD_GATEWAY)] * 3)
    call, _ = _retrying(run, clock, cap=1.5)
    with pytest.raises(ValueError):
        call()
    assert sum(clock.sleeps) == pytest.approx(3.0)  # two waits of at most the cap


def test_without_a_policy_a_502_is_the_same_value_error_and_writes_no_line():
    call, lines = _gh(_raising(_failed(BAD_GATEWAY)), "issue", "comment", "7")
    with pytest.raises(ValueError) as raised:
        call()
    assert str(raised.value) == f"failed: {BAD_GATEWAY}"
    assert not isinstance(raised.value, TrackerCallFailed)
    assert lines == []
