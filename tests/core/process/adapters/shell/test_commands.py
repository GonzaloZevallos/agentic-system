"""The shell adapter against real processes: `ShellCommands.run` and what a check leaves."""

import os
import select
import shutil
import signal
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.scratch import FilesystemScratch
from agentic.core.process.adapters.shell import (
    EventStopFlag,
    NullStopFlag,
    ShellCommands,
    ShellReaper,
)
from agentic.core.process.adapters.shell import commands as commands_module
from agentic.core.process.application.ports import CommandResult, RunStopped, StopFlag
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
    return ShellCommands(PRODUCT, ShellReaper(PRODUCT, worktrees), NullStopFlag())


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
    commands = ShellCommands(PRODUCT, _NoopReaper(), NullStopFlag())
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


def test_the_env_given_reaches_the_command_over_what_it_inherited(commands, checkout, monkeypatch):
    monkeypatch.setenv("CHECK_WORKERS", "99")
    env = {"CHECK_WORKERS": "6", "PYTEST_XDIST_AUTO_NUM_WORKERS": "6"}
    result = commands.run(
        checkout, 'echo "w=$CHECK_WORKERS x=$PYTEST_XDIST_AUTO_NUM_WORKERS"', 10, env=env
    )
    assert "w=6 x=6" in result.output


def test_a_command_run_without_env_sees_none_of_the_grants_variables(
    commands, checkout, monkeypatch
):
    # A gate that runs this suite exports the grant itself; the command must add none of its own.
    monkeypatch.delenv("CHECK_WORKERS", raising=False)
    result = commands.run(checkout, 'echo "w=[$CHECK_WORKERS]"', 10)
    assert "w=[]" in result.output


def test_the_reaping_mark_wins_over_a_mark_in_the_env(commands, checkout):
    forged = {reaping.MARK_NAME: "other/check/1/forged"}
    result = commands.run(checkout, f'echo "m=${reaping.MARK_NAME}"', 10, env=forged)
    assert f"m={PRODUCT}/check/{os.getpid()}/" in result.output
    assert "forged" not in result.output


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


class _FlipsAfterFirstLook:
    """A flag that is clear the first time it is read and set from then on: the stop that
    lands between the check's pre-launch look and the launch itself."""

    def __init__(self) -> None:
        self.looks = 0

    def is_set(self) -> bool:
        self.looks += 1
        return self.looks > 1

    def set(self) -> None:
        self.looks = 2


def _in_thread(commands: ShellCommands, cwd: Path, command: str, timeout_s: float):
    """Runs `commands.run` on a thread; returns the thread and a box with what it ended in."""
    box: list[object] = []

    def target() -> None:
        try:
            box.append(commands.run(cwd, command, timeout_s))
        except BaseException as exc:  # the test reads what escaped, RunStopped included
            box.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    return thread, box


def _pid_of(pidfile: Path) -> int:
    deadline = time.monotonic() + 10
    while not (pidfile.exists() and pidfile.read_text().strip()):
        assert time.monotonic() < deadline, "the check never wrote its pid"
        time.sleep(0.02)
    return int(pidfile.read_text())


def _stoppable(worktrees: Path, stop: StopFlag) -> ShellCommands:
    return ShellCommands(PRODUCT, ShellReaper(PRODUCT, worktrees), stop)


def test_a_stop_during_a_check_raises_run_stopped_and_leaves_nothing_alive(worktrees, checkout):
    """The stop handler's whole move: mark the flag, reap the checks' scope. The run raises
    `RunStopped` instead of returning the -15 result of the killed command."""
    stop = EventStopFlag()
    commands = _stoppable(worktrees, stop)
    pidfile = checkout / "pid"
    thread, box = _in_thread(commands, checkout, f"echo $$ > {pidfile}; exec {SLEEP} 300", 60)
    fd = os.pidfd_open(_pid_of(pidfile))
    try:
        assert not _gone(fd)
        stop.set()
        ShellReaper(PRODUCT, worktrees).reap_run(reaping.CHECK_TASK, os.getpid())
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert len(box) == 1 and isinstance(box[0], RunStopped)
        assert _gone(fd, 5)
    finally:
        _kill(fd)
        os.close(fd)


def test_a_stop_flagged_before_the_call_launches_nothing(worktrees, checkout):
    stop = EventStopFlag()
    stop.set()
    with pytest.raises(RunStopped):
        _stoppable(worktrees, stop).run(checkout, f"touch {checkout}/launched", timeout_s=10)
    assert not (checkout / "launched").exists()


def test_a_stop_between_the_look_and_the_launch_reaps_the_check_it_just_started(
    worktrees, checkout
):
    """The race the second look closes: the signal's reap ran before the `Popen`, found
    nothing, and the command would otherwise run on."""
    pidfile = checkout / "pid"
    commands = _stoppable(worktrees, _FlipsAfterFirstLook())
    # The timeout is far longer than the wait below: only the second look can end the call
    # in time. Without it `communicate` would sit on the `sleep` until the timeout.
    thread, box = _in_thread(commands, checkout, f"echo $$ > {pidfile}; exec {SLEEP} 300", 120)
    try:
        thread.join(timeout=15)
        assert not thread.is_alive(), "the call went on past the stop: no look after the Popen"
        assert len(box) == 1 and isinstance(box[0], RunStopped)
        if pidfile.exists() and pidfile.read_text().strip():  # bash may be killed before it wrote
            try:
                fd = os.pidfd_open(int(pidfile.read_text()))
            except ProcessLookupError:
                return
            try:
                assert _gone(fd, 5)
            finally:
                _kill(fd)
                os.close(fd)
    finally:
        if thread.is_alive() and pidfile.exists() and pidfile.read_text().strip():
            os.kill(int(pidfile.read_text()), signal.SIGKILL)  # let the failing test end
            thread.join(timeout=10)


def test_a_stop_that_meets_the_timeout_is_a_stop_not_a_timed_out_result(worktrees, checkout):
    stop = EventStopFlag()
    commands = _stoppable(worktrees, stop)
    pidfile = checkout / "pid"
    thread, box = _in_thread(commands, checkout, f"echo $$ > {pidfile}; exec {SLEEP} 300", 1.0)
    fd = os.pidfd_open(_pid_of(pidfile))
    try:
        stop.set()  # no reap: the timeout kills the check and the flag is read after
        thread.join(timeout=30)
        assert len(box) == 1 and isinstance(box[0], RunStopped)
        assert _gone(fd, 5)
    finally:
        _kill(fd)
        os.close(fd)


def test_a_check_that_ends_by_itself_after_the_stop_is_still_a_stop(worktrees, checkout):
    """No `CommandResult` is returned once the flag is set, whatever the command answered."""
    stop = EventStopFlag()
    commands = _stoppable(worktrees, stop)
    pidfile = checkout / "pid"
    thread, box = _in_thread(commands, checkout, f"echo $$ > {pidfile}; {SLEEP} 1; exit 0", 60)
    _pid_of(pidfile)
    stop.set()
    thread.join(timeout=15)
    assert len(box) == 1 and isinstance(box[0], RunStopped)


def test_the_null_flag_never_stops_a_check_even_when_set(worktrees, checkout):
    flag = NullStopFlag()
    flag.set()
    assert not flag.is_set()
    result = _stoppable(worktrees, flag).run(checkout, "echo hi; exit 3", timeout_s=10)
    assert result == CommandResult(3, "hi\n", timed_out=False)
    timed_out = _stoppable(worktrees, flag).run(checkout, f"{SLEEP} 30", timeout_s=0.5)
    assert timed_out.timed_out


# GH-391: every run has its own TMPDIR on the disk, under the product, and takes it with it.

PRINT_TMPDIR = "python3 -c 'import tempfile; print(tempfile.gettempdir())'"


@pytest.fixture
def product(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    return root


def _with_scratch(worktrees: Path, product: Path, task_key: str | None = None) -> ShellCommands:
    return ShellCommands(
        PRODUCT,
        ShellReaper(PRODUCT, worktrees),
        NullStopFlag(),
        scratch=FilesystemScratch(product),
        task_key=task_key,
    )


def _left_in(product: Path) -> list[str]:
    """Every entry under the product's scratch root, directories and owner records."""
    root = product / ".agentic" / "tmp"
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


def _seen_tmpdir(result: CommandResult, product: Path) -> Path:
    seen = Path(result.output.strip().splitlines()[-1])
    assert seen.is_relative_to((product / ".agentic" / "tmp").resolve()), seen
    return seen


@pytest.mark.parametrize(
    ("command", "passes"), [(PRINT_TMPDIR, True), (f"{PRINT_TMPDIR}; exit 1", False)]
)
def test_a_check_sees_a_tmpdir_on_the_disk_that_is_gone_when_it_returns(
    worktrees, checkout, product, command, passes
):
    result = _with_scratch(worktrees, product).run(checkout, command, timeout_s=30)
    seen = _seen_tmpdir(result, product)
    assert result.passed is passes
    assert seen.parent.name == "_untasked"
    assert not seen.exists()
    assert _left_in(product) == ["_untasked"]  # the task's folder, empty


def test_the_tmpdir_of_a_check_that_times_out_is_gone_too(worktrees, checkout, product):
    command = f"{PRINT_TMPDIR}; exec {SLEEP} 300"
    result = _with_scratch(worktrees, product).run(checkout, command, timeout_s=3)
    assert result.timed_out
    assert not _seen_tmpdir(result, product).exists()
    assert _left_in(product) == ["_untasked"]


def test_the_scratch_lives_under_the_task_the_command_is_composed_with(
    worktrees, checkout, product
):
    result = _with_scratch(worktrees, product, "GH-7").run(checkout, PRINT_TMPDIR, timeout_s=30)
    assert _seen_tmpdir(result, product).parent.name == "GH-7"


def test_two_runs_get_two_directories(worktrees, checkout, product):
    commands = _with_scratch(worktrees, product)
    first = _seen_tmpdir(commands.run(checkout, PRINT_TMPDIR, timeout_s=30), product)
    second = _seen_tmpdir(commands.run(checkout, PRINT_TMPDIR, timeout_s=30), product)
    assert first != second


def test_a_pytest_tmp_path_resolves_under_the_checks_directory(worktrees, checkout, product):
    (checkout / "test_tmp.py").write_text(
        "def test_it(tmp_path):\n    print('TMP', tmp_path)\n    assert tmp_path.is_dir()\n"
    )
    command = f"{sys.executable} -m pytest -q -s -p no:cacheprovider test_tmp.py"
    result = _with_scratch(worktrees, product).run(checkout, command, timeout_s=60)
    assert result.passed, result.output
    printed = next(line for line in result.output.splitlines() if "TMP " in line)
    scratch_root = (product / ".agentic" / "tmp").resolve()
    assert Path(printed.split("TMP ", 1)[1]).is_relative_to(scratch_root)
    assert _left_in(product) == ["_untasked"]


class _SeesTheDirectory(_NoopReaper):
    """Notes, when the reaper is asked to stop the check's marked processes, whether the
    scratch directory and its record are still there."""

    def __init__(self, product: Path) -> None:
        self._product = product
        self.at_reap: list[list[str]] = []

    def reap_attempt(self, task_id: str, run: int, token: str) -> tuple[ReapedProcess, ...]:
        self.at_reap.append(_left_in(self._product))
        return ()


def test_the_marked_processes_are_reaped_before_the_directory_is_removed(
    worktrees, checkout, product
):
    reaper = _SeesTheDirectory(product)
    commands = ShellCommands(
        PRODUCT, reaper, NullStopFlag(), scratch=FilesystemScratch(product), task_key="GH-7"
    )
    commands.run(checkout, "true", timeout_s=30)
    assert reaper.at_reap, "the check was never reaped"
    assert all(len(seen) == 3 for seen in reaper.at_reap)  # the task dir, the owner dir, its record
    assert _left_in(product) == ["GH-7"]


def test_a_run_stopped_after_the_launch_removes_its_directory(worktrees, checkout, product):
    commands = ShellCommands(
        PRODUCT,
        ShellReaper(PRODUCT, worktrees),
        _FlipsAfterFirstLook(),
        scratch=FilesystemScratch(product),
    )
    with pytest.raises(RunStopped):
        commands.run(checkout, f"exec {SLEEP} 300", timeout_s=120)
    assert _left_in(product) == ["_untasked"]
