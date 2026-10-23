"""The shell adapter against real processes: `ShellCommands.run` and what a check leaves."""

import os
import select
import shutil
import signal
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from agentic.core.process.adapters.shell import ShellCommands, ShellReaper
from agentic.core.process.adapters.shell import commands as commands_module
from agentic.core.process.application.ports import CommandResult
from agentic.core.process.domain import reaping
from agentic.core.process.domain.reaping import ReapedProcess
from agentic.core.process.domain.stall import ChildActivity

PRODUCT = "tcommands"
SLEEP = shutil.which("sleep") or "sleep"
SETSID = shutil.which("setsid") or "setsid"
ENV = shutil.which("env") or "env"


@pytest.fixture
def worktrees(tmp_path: Path) -> Iterator[Path]:
    """A `tmp_path` worktrees root; its teardown reaps the checks this process marked and
    whatever else has a cwd in it (the escapees of the tests), by the production rules."""
    root = tmp_path / "worktrees"
    (root / "GH-1.api").mkdir(parents=True)
    yield root
    reaper = ShellReaper(PRODUCT, root)
    reaper.reap_run(reaping.CHECK_TASK, os.getpid())
    reaper.reap_within([root / "GH-1.api"])


@pytest.fixture
def checkout(worktrees: Path) -> Path:
    return worktrees / "GH-1.api"


@pytest.fixture
def commands(worktrees: Path) -> ShellCommands:
    return ShellCommands(PRODUCT, ShellReaper(PRODUCT, worktrees))


class _NoopReaper:
    """A reaper that stops nothing: the control of what the real one does."""

    def reap_task(self, task_id: str) -> tuple[ReapedProcess, ...]:
        return ()

    def reap_run(self, task_id: str, run: int) -> tuple[ReapedProcess, ...]:
        return ()

    def reap_attempt(self, task_id: str, run: int, token: str) -> tuple[ReapedProcess, ...]:
        return ()

    def reap_within(self, paths: Sequence[Path]) -> tuple[ReapedProcess, ...]:
        return ()

    def child_activity(
        self, task_id: str, run: int, token: str, parent_pid: int
    ) -> ChildActivity | None:
        return None


def _escapee(pidfile: Path, launcher: str) -> str:
    """A check that starts a long `sleep` through `launcher`, unredirected (it holds the
    check's pipe), waits for its pid, notes that it is alive and blocks."""
    return (
        f"{launcher} sh -c 'echo $$ > {pidfile}; exec {SLEEP} 300' & "
        f"while [ ! -s {pidfile} ]; do {SLEEP} 0.05; done; {SLEEP} 60"
    )


def _gone(fd: int, within_s: float = 0.0) -> bool:
    """Whether the process the pidfd pins has exited."""
    return bool(select.select([fd], [], [], within_s)[0])


def _run_watching(
    commands: ShellCommands, cwd: Path, command: str, pidfile: Path, timeout_s: float = 1.0
) -> tuple[CommandResult, bool, int]:
    """Runs the check on a thread; once the escapee wrote its pid, pins it with a pidfd and
    notes it was alive. Returns the result, whether it was alive, and the pidfd (the
    caller closes it)."""
    box: list[CommandResult] = []
    thread = threading.Thread(target=lambda: box.append(commands.run(cwd, command, timeout_s)))
    thread.start()
    deadline = time.monotonic() + 10
    while not (pidfile.exists() and pidfile.read_text().strip()):
        assert time.monotonic() < deadline, "the check never started its child"
        time.sleep(0.02)
    fd = os.pidfd_open(int(pidfile.read_text()))
    alive = not _gone(fd)
    thread.join(timeout=30)
    assert not thread.is_alive()
    return box[0], alive, fd


def _kill(fd: int) -> None:
    if not _gone(fd):
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        _gone(fd, 5)


def test_a_plain_check_returns_what_it_always_did(commands, checkout):
    result = commands.run(checkout, "echo hi; exit 3", timeout_s=10)
    assert result == CommandResult(3, "hi\n", timed_out=False)


def test_a_pipe_holder_is_stopped_with_the_check_that_timed_out(commands, checkout):
    pidfile = checkout / "pid"
    result, alive, fd = _run_watching(commands, checkout, _escapee(pidfile, ""), pidfile)
    try:
        assert alive and _gone(fd, 5)
    finally:
        _kill(fd)
        os.close(fd)
    assert result.timed_out and not result.passed


def test_a_setsid_escapee_is_stopped_by_the_mark(commands, checkout, capsys):
    pidfile = checkout / "pid"
    result, alive, fd = _run_watching(commands, checkout, _escapee(pidfile, SETSID), pidfile)
    try:
        assert alive and _gone(fd, 5)
    finally:
        _kill(fd)
        os.close(fd)
    assert result.timed_out
    stopped = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith(f"reaper: stopped {pidfile.read_text().strip()} ")
    ]
    assert len(stopped) == 1  # one line per process, from one source


def test_an_unmarked_escapee_in_the_checkout_is_stopped_by_its_cwd(commands, checkout):
    pidfile = checkout / "pid"
    command = _escapee(pidfile, f"{ENV} -i {SETSID}")
    result, alive, fd = _run_watching(commands, checkout, command, pidfile)
    try:
        assert alive and _gone(fd, 5)
    finally:
        _kill(fd)
        os.close(fd)
    assert result.timed_out


def test_an_unmarked_escapee_outside_the_worktrees_root_is_left_alone(
    commands, tmp_path, worktrees, monkeypatch
):
    """The rejection side: the cwd reap never reaches a process outside the root, so a
    check run elsewhere (the product's own server) keeps what it started unmarked."""
    monkeypatch.setattr(commands_module, "_DRAIN_S", 0.5)  # the escapee holds the pipe
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    pidfile = elsewhere / "pid"
    command = _escapee(pidfile, f"{ENV} -i {SETSID}")
    result, alive, fd = _run_watching(commands, elsewhere, command, pidfile)
    try:
        assert result.timed_out and alive
        assert not _gone(fd)
    finally:
        _kill(fd)
        os.close(fd)


def test_without_a_reaper_the_setsid_escapee_outlives_the_check(worktrees, checkout, monkeypatch):
    """Control: what the reaper changes. It is reaped by the fixture teardown."""
    monkeypatch.setattr(commands_module, "_DRAIN_S", 0.5)  # the escapee holds the pipe
    commands = ShellCommands(PRODUCT, _NoopReaper())
    pidfile = checkout / "pid"
    result, alive, fd = _run_watching(commands, checkout, _escapee(pidfile, SETSID), pidfile)
    try:
        assert result.timed_out and alive
        assert not _gone(fd)
    finally:
        _kill(fd)  # the teardown's reap would also find it by its mark
        os.close(fd)


def test_a_check_that_ends_normally_leaves_no_redirected_background_process(commands, checkout):
    pidfile = checkout / "pid"
    command = (
        f"{SLEEP} 300 >/dev/null 2>&1 & echo $! > {pidfile}; kill -0 $! && touch {checkout}/alive"
    )
    result = commands.run(checkout, command, timeout_s=10)
    assert result.passed and (checkout / "alive").exists()
    pid = int(pidfile.read_text())
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        assert _gone(fd, 5)
    finally:
        _kill(fd)
        os.close(fd)


def test_the_systems_own_virtualenv_does_not_reach_the_command(commands, checkout, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/system/.venv")
    result = commands.run(checkout, 'echo "venv=[$VIRTUAL_ENV]"', timeout_s=10)
    assert "venv=[]" in result.output


def test_a_command_runs_in_the_checkout_and_keeps_both_streams(commands, checkout):
    (checkout / "marker").write_text("")
    result = commands.run(checkout, "ls && echo oops >&2 && exit 3", timeout_s=10)
    assert result.exit_code == 3 and not result.passed and not result.timed_out
    assert "marker" in result.output and "oops" in result.output


def test_a_green_command_passes(commands, checkout):
    assert commands.run(checkout, "true", timeout_s=10).passed


def test_a_command_past_its_time_is_stopped_with_its_children(commands, checkout):
    started = time.monotonic()
    result = commands.run(checkout, "echo started; sleep 30 & sleep 30", timeout_s=0.5)
    assert result.timed_out and not result.passed and "started" in result.output
    assert time.monotonic() - started < 5


def test_output_that_is_not_utf8_is_kept_readable(commands, checkout):
    result = commands.run(checkout, "printf 'bad \\377 byte'", timeout_s=10)
    assert result.passed and result.output.startswith("bad ")
