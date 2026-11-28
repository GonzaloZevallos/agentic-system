"""`run.bounded_run` and `run.run_gh` (GH-266, ADR-0050)."""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import run as run_module
from agentic.core.process.adapters.github.run import (
    GH_DRAIN_S,
    GH_TRANSIENT_STATUSES,
    bounded_run,
    http_status,
    run_gh,
)
from agentic.core.process.application.ports import (
    TrackerCallFailed,
    TrackerRateLimited,
    TrackerRefused,
    TrackerTimedOut,
    TrackerTransient,
    TrackerTurnRefused,
    TrackerUnreachable,
    Turn,
    WriteGate,
)
from agentic.core.process.domain.github_access import GithubAccess
from tests.core.process.adapters.github.gh_fake import StubGate, gh_response


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
    run = _script(
        _failed(RATE_LIMITED), '{"resources": {"core": {"remaining": 5, "reset": 1}}}', "x"
    )
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
    assert str(raised.value) == f"failed: {BAD_GATEWAY} (HTTP 502)"
    assert not isinstance(raised.value, TrackerCallFailed)
    assert lines == []


# --- the HTTP status gh prints (GH-438) ---------------------------------------------------


@pytest.mark.parametrize(
    ("stderr", "status"),
    [
        ("gh: HTTP 502", 502),
        ("gh: Not Found (HTTP 404)", 404),
        ("gh: Server Error (HTTP 503)\n", 503),
        (BAD_GATEWAY, 502),
        ("non-200 OK status code: 504 Gateway Timeout body: x", 504),
        ("gh: API rate limit exceeded for user ID 1. (HTTP 403)", 403),
        ("", None),
        ("HTTP 401: Bad credentials", None),
        ("error connecting to api.github.com", None),
        ("gh: Not Found (HTTP 4040)", None),
        ("gh: Not Found (HTTP 40)", None),
    ],
)
def test_http_status_reads_the_three_forms_gh_prints_and_nothing_else(stderr, status):
    assert http_status(stderr) == status


def test_the_transient_statuses_are_the_gateway_errors():
    assert GH_TRANSIENT_STATUSES == frozenset({502, 503, 504})


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_status_is_retried_and_ends_transient_with_the_status(status):
    clock = _Clock()
    run = _script(*[_failed(f"gh: Server Error (HTTP {status})")] * 3)
    call, lines = _retrying(run, clock)
    with pytest.raises(TrackerTransient) as raised:
        call()
    assert len(run.calls) == 3
    assert str(raised.value).endswith(f"on each of 3 attempts (HTTP {status})")
    assert raised.value.status == status
    assert all("Server Error" not in line for line in lines)


def test_a_gateway_status_then_success_is_retried():
    clock = _Clock()
    run = _script(_failed("gh: Service Unavailable (HTTP 503)"), "ok")
    call, _ = _retrying(run, clock)
    assert call() == "ok" and len(run.calls) == 2


@pytest.mark.parametrize("status", [503, 504])
def test_without_a_policy_a_gateway_status_fails_at_once_with_the_suffix(status):
    stderr = f"gh: Unavailable (HTTP {status})"
    call, lines = _gh(_raising(_failed(stderr)))
    with pytest.raises(ValueError) as raised:
        call()
    assert type(raised.value) is ValueError
    assert str(raised.value) == f"failed: {stderr} (HTTP {status})"
    assert lines == []


def test_a_refusal_under_a_policy_carries_its_status():
    clock = _Clock()
    run = _script(_failed("gh: Not Found (HTTP 404)"))
    call, lines = _retrying(run, clock)
    with pytest.raises(TrackerRefused) as raised:
        call()
    assert raised.value.status == 404
    assert str(raised.value) == "failed: gh: Not Found (HTTP 404) (HTTP 404)"
    assert len(run.calls) == 1 and lines == []


def test_a_refusal_without_a_status_has_none_and_no_suffix():
    clock = _Clock()
    run = _script(_failed("HTTP 404: Not Found"))
    call, _ = _retrying(run, clock)
    with pytest.raises(TrackerRefused) as raised:
        call()
    assert raised.value.status is None
    assert str(raised.value) == "failed: HTTP 404: Not Found"


def test_a_rate_limit_that_runs_out_carries_the_status_and_a_timeout_none():
    from agentic.core.process.application.ports import TrackerRateLimited

    clock = _Clock(1_000)
    run = _script(_failed(RATE_LIMITED), '{"resources": {"core": {"remaining": 0, "reset": 1100}}}')
    call, _ = _retrying(run, clock, cap=60)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.status == 403 and str(raised.value).endswith("(HTTP 403)")

    timed = _script(*[subprocess.TimeoutExpired(["gh"], 60)] * 3)
    call, _ = _retrying(timed, _Clock(), read=True, args=("issue", "list"))
    with pytest.raises(TrackerTimedOut) as raised:
        call()
    assert raised.value.status is None and "(HTTP" not in str(raised.value)


def test_the_status_exceptions_still_build_from_a_message_alone():
    assert TrackerTransient("m").status is None
    assert TrackerRefused("m").status is None
    assert TrackerRefused("m", status=404).status == 404


# --- the reset a rate limit carries (GH-1031) ----------------------------------------------


def _reset_json(reset):
    return json.dumps({"resources": {"core": {"remaining": 0, "reset": reset}}})


def test_a_reset_over_the_cap_carries_the_instant_it_read():
    from datetime import UTC, datetime

    from agentic.core.process.application.ports import TrackerRateLimited

    clock = _Clock(1_000)
    run = _script(_failed(RATE_LIMITED), _reset_json(1100))
    call, _ = _retrying(run, clock, cap=60)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at == datetime.fromtimestamp(1100, UTC)
    assert raised.value.resets_at.utcoffset().total_seconds() == 0
    assert clock.sleeps == []


def test_exhausted_rate_limited_attempts_carry_the_last_reset_read():
    from datetime import UTC, datetime

    from agentic.core.process.application.ports import TrackerRateLimited

    clock = _Clock(1_000)
    run = _script(
        _failed(RATE_LIMITED),
        _reset_json(1010),
        _failed(RATE_LIMITED),
        _reset_json(1030),
        _failed(RATE_LIMITED),
    )
    call, _ = _retrying(run, clock)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at == datetime.fromtimestamp(1030, UTC)
    reads = [c for c in run.calls if c[1:] == ["api", "rate_limit"]]
    assert len(reads) == 2
    assert len(run.calls) == 5


def test_a_stopped_rate_limit_wait_carries_no_reset():
    from agentic.core.process.application.ports import TrackerRateLimited

    clock = _Clock(1_000)
    stop = _Stop(after=1, clock=clock)
    run = _script(_failed(RATE_LIMITED), _reset_json(1050), "never")
    call, _ = _retrying(run, clock, stop=stop)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at is None


@pytest.mark.parametrize("read", ["rate limited", "bad json"])
def test_a_failed_rate_limit_read_carries_no_reset(read):
    from agentic.core.process.application.ports import TrackerRateLimited

    unreadable = _failed(RATE_LIMITED) if read == "rate limited" else "not json"
    run = _script(*[_failed(RATE_LIMITED), unreadable] * 2, _failed(RATE_LIMITED))
    call, _ = _retrying(run, _Clock())
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at is None


def test_rate_limited_builds_from_a_message_alone_and_keeps_status():
    from agentic.core.process.application.ports import TrackerRateLimited

    assert TrackerRateLimited("m").resets_at is None
    assert TrackerRateLimited("m", status=403).status == 403
    assert TrackerRateLimited("m", status=403).resets_at is None


# --- the access keyword and the header-wait path (GH-1091, ADR-0107) ---------------------


def _http_failed(status, headers, stderr, code=1):
    """A failed `gh api -i`: the exception carries what `gh_response` prints as its stdout."""
    stdout = gh_response(status, headers, "{}").stdout
    return subprocess.CalledProcessError(code, ["gh"], output=stdout, stderr=stderr)


BAD_GATEWAY = "gh: HTTP 502"
SECONDARY = "gh: You have exceeded a secondary rate limit. (HTTP 403)"


def _accessing(run, clock, *, cap=900, read=False, access=None):
    lines: list[str] = []
    call = lambda: run_gh(  # noqa: E731
        run,
        ("api", "-i", "/x"),
        cwd=Path("."),
        not_installed="no gh",
        failed=lambda stderr: f"failed: {stderr}",
        log=lines.append,
        retry=_policy(clock, cap=cap),
        retry_timeouts=read,
        access=GithubAccess() if access is None else access,
    )
    return call, lines


def test_a_502_with_retry_after_waits_the_header_value():
    clock = _Clock()
    run = _script(_http_failed(502, [("Retry-After", "7")], BAD_GATEWAY), "done")
    call, lines = _accessing(run, clock)
    assert call() == "done"
    assert sum(clock.sleeps) == pytest.approx(7)
    assert lines == [
        "agentic: gh api -i met a transient GitHub error; retrying in 7 s (attempt 1/3)"
    ]


def test_a_502_with_no_header_wait_uses_the_backoff_under_access():
    clock = _Clock()
    run = _script(_http_failed(502, [("Content-Type", "text/plain")], BAD_GATEWAY), "done")
    call, _ = _accessing(run, clock)
    assert call() == "done" and sum(clock.sleeps) == 2


def test_a_502_with_a_reset_header_waits_until_it():
    clock = _Clock(1_000)
    run = _script(_http_failed(502, [("X-RateLimit-Reset", "1005")], BAD_GATEWAY), "done")
    call, _ = _accessing(run, clock)
    assert call() == "done" and sum(clock.sleeps) == pytest.approx(5)


def test_a_primary_limit_403_with_a_successful_read_keeps_resets_at_from_the_read():
    clock = _Clock(1_000)
    failure = _http_failed(403, [("Retry-After", "5")], RATE_LIMITED)
    run = _script(failure, _reset_json(1_050), failure, _reset_json(1_050), failure)
    call, _ = _accessing(run, clock)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at == datetime.fromtimestamp(1_050, UTC)
    assert sum(clock.sleeps) == pytest.approx(50)  # the second reset has passed: no wait


def test_a_primary_limit_403_with_a_successful_read_waits_the_read_not_the_header():
    clock = _Clock(1_000)
    run = _script(
        _http_failed(403, [("Retry-After", "5")], RATE_LIMITED), _reset_json(1_040), "done"
    )
    call, _ = _accessing(run, clock)
    assert call() == "done" and sum(clock.sleeps) == pytest.approx(40)


def test_a_primary_limit_403_whose_read_failed_falls_to_the_header_and_sets_no_reset():
    clock = _Clock(1_000)
    failure = _http_failed(403, [("Retry-After", "5")], RATE_LIMITED)
    run = _script(failure, _failed(RATE_LIMITED), failure, _failed(RATE_LIMITED), failure)
    call, _ = _accessing(run, clock)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert sum(clock.sleeps) == pytest.approx(10)
    assert raised.value.resets_at is None


def test_a_secondary_403_with_retry_after_stays_refused():
    clock = _Clock()
    run = _script(_http_failed(403, [("Retry-After", "5")], SECONDARY))
    call, lines = _accessing(run, clock)
    with pytest.raises(TrackerRefused) as raised:
        call()
    assert raised.value.status == 403 and clock.sleeps == [] and lines == []
    assert len(run.calls) == 1


def test_a_transient_header_wait_over_the_bound_gives_up_without_sleeping():
    clock = _Clock()
    run = _script(_http_failed(502, [("Retry-After", "120")], BAD_GATEWAY), "never")
    call, lines = _accessing(run, clock, cap=60)
    with pytest.raises(TrackerTransient) as raised:
        call()
    assert str(raised.value) == (
        "gh api -i met a transient GitHub error; GitHub asked to wait 120 s, "
        "over the 60 s bound (HTTP 502)"
    )
    assert raised.value.status == 502
    assert clock.sleeps == [] and lines == [] and len(run.calls) == 1


def test_a_rate_limit_whose_read_failed_and_header_is_over_the_bound_gives_up_with_no_reset():
    clock = _Clock()
    run = _script(
        _http_failed(403, [("Retry-After", "120")], RATE_LIMITED), _failed(RATE_LIMITED), "never"
    )
    call, lines = _accessing(run, clock, cap=60)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert "GitHub asked to wait 120 s, over the 60 s bound" in str(raised.value)
    assert raised.value.resets_at is None and clock.sleeps == [] and lines == []


def test_a_rate_limit_whose_read_failed_and_headers_are_unreadable_runs_out_with_no_reset():
    clock = _Clock()
    failure = _failed(RATE_LIMITED)
    run = _script(failure, _failed(RATE_LIMITED), failure, _failed(RATE_LIMITED), failure)
    call, _ = _accessing(run, clock)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at is None and sum(clock.sleeps) == 2 + 8


def test_a_timeout_uses_the_backoff_even_with_access():
    clock = _Clock()
    run = _script(subprocess.TimeoutExpired(["gh"], 60), "done")
    call, _ = _accessing(run, clock, read=True)
    assert call() == "done" and sum(clock.sleeps) == 2


def test_the_nested_rate_limit_read_runs_with_access_none(monkeypatch):
    seen: list[GithubAccess | None] = []
    real = run_module.run_gh

    def spy(*args, **kwargs):
        seen.append(kwargs.get("access", "absent"))
        return real(*args, **kwargs)

    monkeypatch.setattr(run_module, "run_gh", spy)
    clock = _Clock(1_000)
    run = _script(_http_failed(403, [], RATE_LIMITED), _reset_json(1_001), "done")
    call, _ = _accessing(
        run, clock, access=GithubAccess(gate=StubGate(), cache=object(), budget=object())
    )
    assert call() == "done"
    assert seen == [None]


def _plain(stderr, code=1, stdout="not http"):
    return subprocess.CalledProcessError(code, ["gh"], output=stdout, stderr=stderr)


UNREACHABLE = "error connecting to api.github.com"
_PARITY = {
    "success": (lambda: ["ok"], False),
    "transient": (lambda: [_plain(BAD_GATEWAY), "ok"], False),
    "transient exhausted": (lambda: [_plain(BAD_GATEWAY)] * 3, False),
    "transient with no stdout": (lambda: [_plain(BAD_GATEWAY, stdout=None), "ok"], False),
    "rate limited, reset read": (
        lambda: [_plain(RATE_LIMITED), _reset_json(1_030), "ok"],
        False,
    ),
    "rate limited, reset not read": (
        lambda: [_plain(RATE_LIMITED), _failed(RATE_LIMITED), "ok"],
        False,
    ),
    "rate limited exhausted": (
        lambda: [_plain(RATE_LIMITED), _reset_json(1_010)] * 2 + [_plain(RATE_LIMITED)],
        False,
    ),
    "rate limited with no stdout": (
        lambda: [_plain(RATE_LIMITED, stdout=None), _failed(RATE_LIMITED), "ok"],
        False,
    ),
    "timeout retried": (lambda: [subprocess.TimeoutExpired(["gh"], 60), "ok"], True),
    "timeout not retried": (lambda: [subprocess.TimeoutExpired(["gh"], 60)], False),
    "unreachable": (lambda: [_plain(UNREACHABLE)] * 3, False),
    "auth exit 4": (lambda: [_plain(UNREACHABLE, code=4)], False),
    "plain refusal": (lambda: [_plain("gh: Not Found (HTTP 404)")], False),
}


def _observed(outcomes, read, access):
    """Everything an outside observer sees of one `run_gh` call under a policy."""
    queue = list(outcomes)
    calls: list[tuple[list[str], dict]] = []

    def run(argv, **kwargs):
        calls.append(
            (list(argv), {k: kwargs[k] for k in ("cwd", "capture_output", "text", "check")})
        )
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(argv, 0, stdout=outcome, stderr="")

    clock = _Clock(1_000)
    lines: list[str] = []
    try:
        result: object = run_gh(
            run,
            ("api", "/x"),
            cwd=Path("."),
            not_installed="no gh",
            failed=lambda stderr: f"failed: {stderr}",
            log=lines.append,
            retry=_policy(clock),
            retry_timeouts=read,
            access=access,
        )
    except Exception as exc:
        result = (
            type(exc),
            str(exc),
            getattr(exc, "status", None),
            getattr(exc, "resets_at", None),
        )
    return calls, lines, clock.sleeps, result


@pytest.mark.parametrize("case", list(_PARITY))
def test_access_on_stdout_that_is_not_http_changes_nothing(case):
    outcomes, read = _PARITY[case]
    assert _observed(outcomes(), read, None) == _observed(outcomes(), read, GithubAccess())


# --- the write turn (GH-1107, ADR-0107) ---------------------------------------------------


class _FakeGate:
    """A `WriteGate` that plays a scripted list of turns and records what each `take` got.
    Its `take` moves the shared clock by the turn's wait, as a real gate's sleep would."""

    def __init__(self, turns, cap_s=30.0, clock=None):
        self._cap_s = cap_s
        self.turns = list(turns)
        self.remaining: list[float] = []
        self.clock = clock

    @property
    def cap_s(self) -> float:
        return self._cap_s

    def take(self, remaining_s: float) -> Turn:
        self.remaining.append(remaining_s)
        turn = self.turns.pop(0) if self.turns else Turn("granted", 0.0)
        if self.clock is not None:
            self.clock.t += turn.waited_s
        return turn


def _gated(run, gate, *, args=("issue", "comment", "7"), write=None, cap=900, clock=None):
    clock = _Clock() if clock is None else clock
    lines: list[str] = []
    writer: WriteGate = gate
    call = lambda: run_gh(  # noqa: E731
        run,
        args,
        cwd=Path("."),
        not_installed="no gh",
        failed=lambda stderr: f"failed: {stderr}",
        log=lines.append,
        retry=_policy(clock, cap=cap),
        access=GithubAccess(gate=writer),
        write=write,
    )
    return call, lines, clock


def test_a_write_takes_a_turn_and_a_read_does_not():
    gate = _FakeGate([])
    call, _, _ = _gated(_script("ok"), gate)
    assert call() == "ok" and gate.remaining == [30.0]
    gate = _FakeGate([])
    call, _, _ = _gated(_script("ok"), gate, args=("issue", "view", "7"))
    assert call() == "ok" and gate.remaining == []


def test_write_overrides_is_write_in_both_directions():
    gate = _FakeGate([])
    call, _, _ = _gated(_script("ok"), gate, args=("issue", "view", "7"), write=True)
    call()
    assert gate.remaining == [30.0]
    gate = _FakeGate([])
    call, _, _ = _gated(_script("ok"), gate, write=False)
    call()
    assert gate.remaining == []


def test_a_retry_takes_a_new_turn_on_the_second_attempt():
    gate = _FakeGate([])
    run = _script(_failed(BAD_GATEWAY), "ok")
    call, _, clock = _gated(run, gate)
    assert call() == "ok"
    assert gate.remaining == [30.0, 28.0] and len(run.calls) == 2 and sum(clock.sleeps) == 2


@pytest.mark.parametrize("outcome", ["refused", "stopped"])
def test_a_refused_or_stopped_turn_raises_without_calling_run(outcome):
    gate = _FakeGate([Turn(outcome, 0.0)])
    run = _script("never")
    call, _, _ = _gated(run, gate)
    with pytest.raises(TrackerTurnRefused) as raised:
        call()
    assert isinstance(raised.value, TrackerTransient)
    assert run.calls == [] and raised.value.status is None


def test_the_turn_log_lines_are_exact_and_a_zero_wait_logs_nothing():
    call, lines, _ = _gated(_script("ok"), _FakeGate([Turn("granted", 0.0)]))
    call()
    assert lines == []
    call, lines, _ = _gated(_script("ok"), _FakeGate([Turn("granted", 2.5)]))
    call()
    assert lines == ["agentic: gh issue comment waiting 2.5 s for its write turn"]
    call, lines, _ = _gated(_script("never"), _FakeGate([Turn("refused", 0.0)], cap_s=30))
    with pytest.raises(TrackerTurnRefused) as refused:
        call()
    assert lines == ["agentic: gh issue comment write turn refused: over the 30 s bound"]
    assert str(refused.value) == "gh issue comment write turn refused: over the 30 s bound"
    call, lines, _ = _gated(_script("never"), _FakeGate([Turn("stopped", 1.0)]))
    with pytest.raises(TrackerTurnRefused):
        call()
    assert lines == ["agentic: gh issue comment write turn refused: the wait was stopped"]


def test_one_calls_turn_waits_and_sleeps_stay_within_the_cap():
    clock = _Clock()
    gate = _FakeGate([Turn("granted", 10.0), Turn("granted", 3.0)], cap_s=30, clock=clock)
    run = _script(_failed(BAD_GATEWAY), "ok")
    call, _, _ = _gated(run, gate, clock=clock)
    assert call() == "ok"
    assert gate.remaining == [30.0, 18.0]  # 30, then 30 - 10 - 2
    assert 10 + sum(clock.sleeps) + 3 <= gate.cap_s


def test_a_backoff_over_the_remaining_time_raises_the_kinds_error_without_sleeping():
    clock = _Clock()
    gate = _FakeGate([Turn("granted", 25.0)], cap_s=30, clock=clock)
    run = _script(_failed(BAD_GATEWAY), _failed(BAD_GATEWAY), "never")
    # Attempt 1 waits 2 s; attempt 2 would wait 8 s with 30 - 25 - 2 = 3 s left.
    call, lines, _ = _gated(run, gate, clock=clock)
    with pytest.raises(TrackerTransient) as raised:
        call()
    assert str(raised.value) == (
        "gh issue comment met a transient GitHub error; its wait is 8 s, "
        "over the 3 s left of its 30 s write bound (HTTP 502)"
    )
    assert raised.value.status == 502 and not isinstance(raised.value, TrackerTurnRefused)
    assert clock.sleeps == [1, 1] and len(run.calls) == 2
    assert not any("retrying in 8" in line for line in lines)


def test_a_backoff_with_nothing_left_refuses_before_any_sleep():
    clock = _Clock()
    gate = _FakeGate([Turn("granted", 30.0)], cap_s=30, clock=clock)
    run = _script(_failed(BAD_GATEWAY), "never")
    call, lines, _ = _gated(run, gate, clock=clock)
    with pytest.raises(TrackerTransient) as raised:
        call()
    assert "over the 0 s left of its 30 s write bound" in str(raised.value)
    assert clock.sleeps == [] and len(run.calls) == 1
    assert not any("retrying" in line for line in lines)


def test_a_rate_limit_over_the_remaining_time_carries_the_reset_it_read():
    clock = _Clock(1_000)
    gate = _FakeGate([], cap_s=30, clock=clock)
    run = _script(_failed(RATE_LIMITED), _reset_json(1_100), "never")
    call, lines, _ = _gated(run, gate, clock=clock)
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at == datetime.fromtimestamp(1_100, UTC)
    assert "its wait is 100 s, over the 30 s left of its 30 s write bound" in str(raised.value)
    assert clock.sleeps == [] and len(run.calls) == 2
    assert not any("retrying" in line for line in lines)


def test_the_rate_limit_read_never_takes_a_turn():
    clock = _Clock(1_000)
    gate = _FakeGate([], cap_s=30, clock=clock)
    run = _script(_failed(RATE_LIMITED), _reset_json(1_005), "ok")
    call, _, _ = _gated(run, gate, clock=clock)
    assert call() == "ok"
    assert len(gate.remaining) == 2 and len(run.calls) == 3  # the read is the middle call


def _observed_turnless(outcomes, read, access):
    queue = list(outcomes)
    calls: list[tuple[list[str], dict]] = []

    def run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(argv, 0, stdout=outcome, stderr="")

    clock = _Clock(1_000)
    lines: list[str] = []
    try:
        result: object = run_gh(
            run,
            ("issue", "comment", "7"),
            cwd=Path("."),
            not_installed="no gh",
            failed=lambda stderr: f"failed: {stderr}",
            log=lines.append,
            retry=_policy(clock),
            retry_timeouts=read,
            access=access,
        )
    except Exception as exc:
        result = (
            type(exc),
            str(exc),
            getattr(exc, "status", None),
            getattr(exc, "resets_at", None),
        )
    return calls, lines, clock.sleeps, result


_TURNLESS = {
    "success": (lambda: ["ok"], False),
    "refusal": (lambda: [_plain("gh: Not Found (HTTP 404)")], False),
    "timeout": (lambda: [subprocess.TimeoutExpired(["gh"], 60)], False),
    "exhausted retry": (lambda: [_plain(BAD_GATEWAY)] * 3, False),
}


@pytest.mark.parametrize("case", list(_TURNLESS))
def test_without_a_gate_a_write_is_the_same_as_without_access(case):
    outcomes, read = _TURNLESS[case]
    assert _observed_turnless(outcomes(), read, None) == _observed_turnless(
        outcomes(), read, GithubAccess()
    )


# --- the `call` label (GH-1089) --------------------------------------------------------


def _labelled(run, args, *, call, retry=None, retry_timeouts=False, access=None):
    lines: list[str] = []
    runner = lambda: run_gh(  # noqa: E731
        run,
        args,
        cwd=Path("."),
        not_installed="no gh",
        failed=lambda stderr: f"failed: {stderr}",
        log=lines.append,
        retry=retry,
        retry_timeouts=retry_timeouts,
        access=access,
        call=call,
    )
    return runner, lines


_API_ARGS = ("api", "-i", "-X", "POST", "repos/{owner}/{repo}/pulls", "-f", "title=t")


def test_a_call_label_names_the_timeout_and_the_unreachable_message_and_line():
    runner, lines = _labelled(
        _raising(subprocess.TimeoutExpired(["gh"], 60)), _API_ARGS, call="pr create"
    )
    with pytest.raises(TrackerTimedOut) as timed_out:
        runner()
    assert str(timed_out.value) == "gh pr create timed out after 60 s"
    assert lines == [
        "agentic: gh pr create timed out after 60 s; this read fails and the next round retries"
    ]
    runner, lines = _labelled(
        _raising(_failed("error connecting to api.github.com")), _API_ARGS, call="pr create"
    )
    with pytest.raises(TrackerUnreachable) as unreachable:
        runner()
    assert str(unreachable.value) == "gh pr create could not reach GitHub (error connecting to)"
    assert lines == [
        "agentic: gh pr create could not reach GitHub (error connecting to); "
        "this read fails and the next round retries"
    ]


def test_a_call_label_names_the_retry_lines_and_the_exhaustion_message():
    clock = _Clock()
    run = _script(*[_failed(BAD_GATEWAY)] * 3)
    runner, lines = _labelled(run, _API_ARGS, call="issue comment", retry=_policy(clock))
    with pytest.raises(TrackerTransient) as raised:
        runner()
    assert str(raised.value).startswith("gh issue comment met a transient GitHub error on each of")
    assert all(line.startswith("agentic: gh issue comment ") for line in lines)


def test_a_call_label_names_the_write_turn_lines():
    call, lines = _labelled(
        _script("ok"),
        _API_ARGS,
        call="pr create",
        access=GithubAccess(gate=_FakeGate([Turn("granted", 2.5)])),
    )
    call()
    assert lines == ["agentic: gh pr create waiting 2.5 s for its write turn"]


def test_a_call_label_does_not_change_how_the_failure_text_is_built():
    runner, _ = _labelled(_raising(_failed("boom")), _API_ARGS, call="pr create")
    with pytest.raises(ValueError, match="^failed: boom$"):
        runner()


def test_without_a_call_label_the_wording_is_the_first_two_arguments():
    runner, lines = _labelled(_raising(subprocess.TimeoutExpired(["gh"], 60)), _API_ARGS, call=None)
    with pytest.raises(TrackerTimedOut, match="^gh api -i timed out after 60 s$"):
        runner()
    assert lines == [
        "agentic: gh api -i timed out after 60 s; this read fails and the next round retries"
    ]


# --- the GraphQL rate-limit message (GH-1214) -------------------------------------------

GRAPHQL_RATE_LIMITED = "GraphQL: API rate limit already exceeded for user ID 123."


@pytest.mark.parametrize(
    "stderr", [RATE_LIMITED, GRAPHQL_RATE_LIMITED, GRAPHQL_RATE_LIMITED.upper()]
)
def test_both_rate_limit_messages_classify_as_rate_limited(stderr):
    from agentic.core.process.adapters.github.run import _retryable_kind

    assert _retryable_kind(1, stderr) == "rate_limited"


def test_an_unrelated_message_is_not_rate_limited():
    from agentic.core.process.adapters.github.run import _retryable_kind

    assert _retryable_kind(1, "GraphQL: Could not resolve to a Repository") is None


def test_the_graphql_message_waits_for_the_graphql_reset_alone():
    from datetime import UTC, datetime

    from agentic.core.process.application.ports import TrackerRateLimited

    clock = _Clock(1_000)
    only_graphql = json.dumps(
        {
            "resources": {
                "core": {"remaining": 4_000, "reset": 1_050},
                "graphql": {"remaining": 0, "reset": 1_200},
            }
        }
    )
    run = _script(_failed(GRAPHQL_RATE_LIMITED), only_graphql)
    call, _ = _retrying(run, clock, cap=60, args=("issue", "list"))
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at == datetime.fromtimestamp(1_200, UTC)


def test_the_graphql_message_with_no_resource_at_zero_ends_with_no_reset():
    from agentic.core.process.application.ports import TrackerRateLimited

    clock = _Clock(1_000)
    spent_nowhere = json.dumps({"resources": {"graphql": {"remaining": 9, "reset": 1_200}}})
    run = _script(
        *[_failed(GRAPHQL_RATE_LIMITED), spent_nowhere] * 2, _failed(GRAPHQL_RATE_LIMITED)
    )
    call, _ = _retrying(run, clock, args=("issue", "list"))
    with pytest.raises(TrackerRateLimited) as raised:
        call()
    assert raised.value.resets_at is None
