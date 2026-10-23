"""`PerfShellRunner` against small fake commands in `tmp_path` checkouts under a worktrees
root, with the real `ShellReaper` (GH-318, ADR-0059): what the command gets in its
environment, where it runs, what it leaves, and what the runner leaves of itself."""

import os
import select
import shutil
import signal
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.adapters.shell import ShellReaper
from agentic.core.process.adapters.shell import perf_runner as perf_runner_module
from agentic.core.process.adapters.shell.perf_runner import PerfShellRunner
from agentic.core.process.domain import reaping
from agentic.core.process.domain.reaping import ReapedProcess

PRODUCT = "tperf"
SLEEP = shutil.which("sleep") or "sleep"
SETSID = shutil.which("setsid") or "setsid"
ENV = shutil.which("env") or "env"
VOLUME = {"PERF_VOLUME": "1000", "PERF_VOLUME_UNIT": "rows"}
LINE = '{"volume": 1000, "volume_unit": "rows", "metrics": {"list_ms": 12.5}}'
# What bash adds to every environment it starts, not what the runner passes.
BASH_OWN = {"PWD", "OLDPWD", "SHLVL", "_"}


@pytest.fixture
def worktrees(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "worktrees"
    (root / "perf-1").mkdir(parents=True)
    yield root
    reaper = ShellReaper(PRODUCT, root)
    reaper.reap_run(reaping.PERF_TASK, os.getpid())
    reaper.reap_within([root / "perf-1"])


@pytest.fixture
def checkout(worktrees: Path) -> Path:
    return worktrees / "perf-1"


class Recording(ShellReaper):
    """The real reaper, noting every `reap_attempt` and `reap_within` it was asked for."""

    def __init__(self, worktrees: Path) -> None:
        super().__init__(PRODUCT, worktrees)
        self.attempts: list[tuple[str, int, str]] = []
        self.within: list[list[Path]] = []

    def reap_attempt(self, task_id: str, run: int, token: str) -> tuple[ReapedProcess, ...]:
        self.attempts.append((task_id, run, token))
        return super().reap_attempt(task_id, run, token)

    def reap_within(self, paths: Sequence[Path]) -> tuple[ReapedProcess, ...]:
        self.within.append(list(paths))
        return super().reap_within(paths)


@pytest.fixture
def reaper(worktrees: Path) -> Recording:
    return Recording(worktrees)


@pytest.fixture
def runner(reaper: Recording) -> PerfShellRunner:
    return PerfShellRunner(PRODUCT, reaper)


def _gone(fd: int, within_s: float = 0.0) -> bool:
    return bool(select.select([fd], [], [], within_s)[0])


def _read_pid(pidfile: Path) -> int:
    return int(pidfile.read_text().strip())


def _wait_for(pidfile: str) -> str:
    return f"while [ ! -s {pidfile} ]; do {SLEEP} 0.05; done"


def _exited(pid: int) -> bool:
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True  # the reaper took it and it is already reaped
    try:
        return _gone(fd, 5)
    finally:
        os.close(fd)


def _alive_in_proc(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] != "Z"
    except OSError:
        return False


def _survivors(checkout: Path) -> list[int]:
    """Live processes that carry a perf mark of this test process, or have a cwd in the
    checkout, read from `/proc`."""
    prefix = f"{PRODUCT}/{reaping.PERF_TASK}/{os.getpid()}/".encode()
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        pid = int(entry.name)
        if not _alive_in_proc(pid):
            continue
        try:
            marked = prefix in (entry / "environ").read_bytes()
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        if marked or reaping.cwd_within(cwd, [str(checkout)]):
            found.append(pid)
    return found


def test_a_measure_line_comes_back_parsed_with_its_exit_and_load(runner, checkout):
    run = runner.run(checkout, f"echo noise; echo '{LINE}'", VOLUME, timeout_s=10)
    assert run.exit_code == 0 and not run.timed_out
    assert run.measurement is not None
    assert run.measurement.metrics == {"list_ms": 12.5}
    assert run.measurement.volume == 1000 and run.measurement.volume_unit == "rows"
    assert run.load_before is not None and run.load_after is not None


def test_a_non_zero_exit_is_a_run_not_an_exception(runner, checkout):
    run = runner.run(checkout, f"echo '{LINE}'; exit 3", VOLUME, timeout_s=10)
    assert run.exit_code == 3 and not run.timed_out
    assert run.measurement is not None  # the verdict reads the exit code, not the parser


def test_output_that_is_not_a_line_has_no_measurement(runner, checkout):
    assert runner.run(checkout, "echo not json", VOLUME, timeout_s=10).measurement is None
    assert runner.run(checkout, "true", VOLUME, timeout_s=10).measurement is None


def test_a_load_average_that_cannot_be_read_is_none(runner, checkout, monkeypatch):
    def unavailable() -> tuple[float, float, float]:
        raise OSError("no loadavg")

    monkeypatch.setattr(os, "getloadavg", unavailable)
    run = runner.run(checkout, "true", VOLUME, timeout_s=10)
    assert run.load_before is None and run.load_after is None


def test_the_environment_is_exactly_the_allowlist_whatever_the_system_has(
    runner, checkout, monkeypatch
):
    for name, value in {
        "GH_TOKEN": "t",
        "GITHUB_X": "x",
        "ANTHROPIC_API_KEY": "k",
        "LANGFUSE_X": "l",
        "AGENTIC_X": "a",
        "SECRET": "s",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        reaping.MARK_NAME: "someone/else/1/token",
    }.items():
        monkeypatch.setenv(name, value)
    run = runner.run(checkout, f"{ENV} > {checkout}/envout", VOLUME, timeout_s=10)
    assert run.exit_code == 0
    names = {line.split("=", 1)[0] for line in (checkout / "envout").read_text().splitlines()}
    assert names - BASH_OWN == {
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "HOME",
        "TMPDIR",
        "PERF_VOLUME",
        "PERF_VOLUME_UNIT",
        reaping.MARK_NAME,
    }


def test_the_environment_carries_the_volume_and_a_perf_mark_of_this_process(
    runner, reaper, checkout
):
    command = f"echo $PERF_VOLUME $PERF_VOLUME_UNIT $ROLE_PROCESS_OWNER > {checkout}/out"
    runner.run(checkout, command, VOLUME, timeout_s=10)
    volume, unit, mark = (checkout / "out").read_text().split()
    assert (volume, unit) == ("1000", "rows")
    parsed = reaping.parse_mark(mark)
    assert parsed is not None
    assert (parsed.product, parsed.task, parsed.run) == (PRODUCT, "perf", os.getpid())
    assert reaper.attempts == [("perf", os.getpid(), parsed.token)]


def test_two_runs_carry_two_tokens(runner, reaper, checkout):
    runner.run(checkout, "true", VOLUME, timeout_s=10)
    runner.run(checkout, "true", VOLUME, timeout_s=10)
    assert len({token for _, _, token in reaper.attempts}) == 2


def test_the_venv_of_the_checkout_leads_the_path_when_it_has_one(runner, checkout):
    command = f"echo ${{VIRTUAL_ENV-unset}} > {checkout}/venv; echo $PATH > {checkout}/path"
    runner.run(checkout, command, VOLUME, timeout_s=10)
    assert (checkout / "venv").read_text().strip() == "unset"
    (checkout / ".venv" / "bin").mkdir(parents=True)
    runner.run(checkout, command, VOLUME, timeout_s=10)
    assert (checkout / "venv").read_text().strip() == str(checkout / ".venv")
    assert (checkout / "path").read_text().startswith(f"{checkout}/.venv/bin:")


def test_the_command_runs_in_the_checkout_with_no_stdin(runner, checkout):
    runner.run(checkout, f"pwd > {checkout}/pwd; cat > {checkout}/stdin", VOLUME, timeout_s=10)
    assert (checkout / "pwd").read_text().strip() == str(checkout)
    assert (checkout / "stdin").read_text() == ""


def test_an_env_the_allowlist_does_not_name_is_refused_before_anything_starts(
    runner, reaper, checkout
):
    for name in ("GH_TOKEN", "PATH", "HOME", reaping.MARK_NAME):
        with pytest.raises(ValueError, match=name):
            runner.run(checkout, f"touch {checkout}/started", {name: "x"}, timeout_s=10)
    assert not (checkout / "started").exists()
    assert reaper.attempts == []


@pytest.mark.parametrize(
    ("command", "timeout_s"),
    [("true", 10), ("exit 7", 10), (f"{SLEEP} 300", 0.5)],
    ids=["success", "non-zero exit", "timeout"],
)
def test_the_scratch_home_is_gone_however_the_command_ends(
    runner, checkout, command, timeout_s, monkeypatch
):
    made: list[Path] = []
    real = tempfile.mkdtemp

    def recording(*args: Any, **kwargs: Any) -> str:
        path = real(*args, **kwargs)
        made.append(Path(path))
        return str(path)

    monkeypatch.setattr(perf_runner_module.tempfile, "mkdtemp", recording)
    seen = checkout / "home"
    runner.run(
        checkout, f"echo $HOME $TMPDIR > {seen}; touch $HOME/scratch; {command}", {}, timeout_s
    )
    assert len(made) == 1
    home, tmpdir = seen.read_text().split()
    assert Path(home) == made[0] and Path(tmpdir).is_relative_to(made[0])
    assert not made[0].exists()


def test_the_scratch_home_is_gone_when_the_command_cannot_even_start(
    runner, reaper, worktrees, monkeypatch
):
    made: list[Path] = []
    real = tempfile.mkdtemp

    def recording(*args: Any, **kwargs: Any) -> str:
        path = real(*args, **kwargs)
        made.append(Path(path))
        return str(path)

    monkeypatch.setattr(perf_runner_module.tempfile, "mkdtemp", recording)
    with pytest.raises(OSError, match="missing"):
        runner.run(worktrees / "missing", "true", {}, timeout_s=10)
    assert len(made) == 1 and not made[0].exists()
    assert len(reaper.attempts) == 1  # the reap is in `finally` too


def test_a_timeout_is_a_run_and_stops_the_group(runner, reaper, checkout):
    pidfile = checkout / "pid"
    command = f"{SLEEP} 300 & echo $! > {pidfile}; wait"
    run = runner.run(checkout, command, VOLUME, timeout_s=1.0)
    assert run.timed_out and run.exit_code != 0 and run.measurement is None
    assert not _alive_in_proc(_read_pid(pidfile))
    assert reaper.within == [[checkout], [checkout]]  # on the timeout and again in `finally`


def test_a_setsid_double_fork_is_gone_when_run_returns(runner, checkout):
    pidfile = checkout / "pid"
    command = (
        f"({SETSID} sh -c 'echo $$ > {pidfile}; exec {SLEEP} 300' >/dev/null 2>&1 &); "
        f"{_wait_for(str(pidfile))}; echo '{LINE}'"
    )
    run = runner.run(checkout, command, VOLUME, timeout_s=10)
    assert run.exit_code == 0 and run.measurement is not None
    assert _exited(_read_pid(pidfile))
    assert _survivors(checkout) == []


def test_a_child_that_cds_away_and_drops_the_environment_survives_the_known_gap(runner, checkout):
    """Pinned (ADR-0059): what is stopped is what carries the mark or has its cwd in the
    checkout. This child has neither, and the runner cannot find it. The test kills it."""
    pidfile = checkout / "pid"
    command = (
        f"(cd / && {ENV} -i {SETSID} sh -c 'echo $$ > {pidfile}; exec {SLEEP} 300') "
        f">/dev/null 2>&1 & {_wait_for(str(pidfile))}"
    )
    run = runner.run(checkout, command, VOLUME, timeout_s=10)
    assert run.exit_code == 0
    fd = os.pidfd_open(_read_pid(pidfile))
    try:
        assert not _gone(fd, 0.5)
        assert _survivors(checkout) == []  # nothing the reaper could name either
    finally:
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        assert _gone(fd, 5)
        os.close(fd)
