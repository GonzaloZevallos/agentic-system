"""`run.bounded_run` and `run.run_gh` (GH-266, ADR-0050)."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentic.core.process.adapters.github.run import GH_DRAIN_S, bounded_run, run_gh
from agentic.core.process.application.ports import TrackerTimedOut, TrackerUnreachable


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
