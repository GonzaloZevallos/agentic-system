"""The poller's round heartbeats, the lock exit gated by `_PollHold` and the re-exec onto the
real stage0 (GH-736).

Seams: `cli._poll` with injected stage0 store, heartbeats, execve, run_lock, launcher, sleep and
/proc doubles, `cli._locked` with its `hold` keyword, the one real-exec subprocess test and the
ADR existence assertion. Every double writes to one shared event log, so the order of a `stop`,
a `release` and an `execve` is a fact of the log."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest

from agentic import cli
from agentic.core.process.adapters.filesystem.heartbeats import FilesystemHeartbeats
from agentic.core.process.adapters.filesystem.stage0_store import FilesystemStage0Store
from agentic.core.process.application import poll
from agentic.core.process.application.ports import LockHeld, LockRecord
from agentic.core.process.application.preflight import product_name
from agentic.core.process.domain.stage0_rotation import REEXEC_MARKER, heartbeat_key
from agentic.shared import stage0 as stage0_paths
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports


class Log:
    """The one event log every double appends to."""

    def __init__(self) -> None:
        self.events: list[object] = []


class LockDouble:
    """`refuse` refuses every acquire, `refuse_after` every acquire after that many,
    `fail_release` makes the first release raise."""

    def __init__(
        self,
        log: Log,
        refuse: bool = False,
        refuse_after: int | None = None,
        fail_release: bool = False,
    ) -> None:
        self.log = log
        self.refuse = refuse
        self.refuse_after = refuse_after
        self.fail_release = fail_release
        self.acquired = 0

    def acquire(self, task_id: str, command: str) -> None:
        if self.refuse or (self.refuse_after is not None and self.acquired >= self.refuse_after):
            raise LockHeld(LockRecord(1, "h", "t", "agentic poll"), "held by another poller")
        self.acquired += 1
        self.log.events.append("acquire")

    def release(self, task_id: str) -> None:
        if self.fail_release:
            self.fail_release = False
            self.log.events.append("release-failed")
            raise OSError("the lock cannot be released")
        self.log.events.append("release")

    def read(self, task_id: str) -> None:
        return None

    def refusal(self, task_id: str) -> None:
        return None


class BeatsDouble:
    def __init__(self, log: Log) -> None:
        self.log = log

    def write(self, key, checkout, at, phase, interval_s) -> None:
        self.log.events.append((phase, checkout))


class Stop(Exception):
    pass


def sleeper(log: Log, outcomes: list[BaseException | None]):
    """A `sleep` double: each call takes the next outcome (None returns, else raises it)."""

    async def sleep(seconds: float) -> None:
        log.events.append("sleep")
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    return sleep


async def run_poll(tmp_path: Path, log: Log, heartbeats, sleep, *, lock=None, **kwargs) -> None:
    kwargs.setdefault("argv0", "agentic")
    await cli._poll(
        Configuration(product_dir=tmp_path),
        memory_ports(),
        60.0,
        run_lock=lock or LockDouble(log),
        registry=MemoryRunRegistry(),
        launcher=FakeLauncher(),
        sleep=sleep,
        heartbeats=heartbeats,
        **kwargs,
    )


def stage0_beat(phase: str) -> tuple[str, Path]:
    return (phase, stage0_paths.CHECKOUT)


@pytest.mark.anyio
async def test_start_and_end_are_written_each_round(tmp_path):
    log = Log()
    sleep = sleeper(log, [None, Stop()])
    with pytest.raises(Stop):
        await run_poll(tmp_path, log, BeatsDouble(log), sleep)
    start, end = stage0_beat("start"), stage0_beat("end")
    assert log.events == ["acquire", start, end, "sleep", start, end, "sleep", "release"]


@pytest.mark.anyio
async def test_a_cancellation_writes_stop_before_the_lock_is_released(tmp_path):
    log = Log()
    sleep = sleeper(log, [anyio.get_cancelled_exc_class()()])
    with pytest.raises(anyio.get_cancelled_exc_class()):
        await run_poll(tmp_path, log, BeatsDouble(log), sleep)
    assert log.events[-2:] == [stage0_beat("stop"), "release"]


@pytest.mark.anyio
async def test_a_crashing_round_writes_no_stop(tmp_path, monkeypatch):
    log = Log()

    def broken(*args, **kwargs):
        raise RuntimeError("the round crashed")

    monkeypatch.setattr(poll, "run_once", broken)
    with pytest.raises(RuntimeError):
        await run_poll(tmp_path, log, BeatsDouble(log), sleeper(log, []))
    assert log.events == ["acquire", stage0_beat("start"), "release"]


@pytest.mark.anyio
async def test_without_heartbeats_nothing_is_written_and_the_lock_is_still_released(tmp_path):
    log = Log()
    with pytest.raises(Stop):
        await run_poll(tmp_path, log, None, sleeper(log, [Stop()]))
    assert log.events == ["acquire", "sleep", "release"]


@pytest.mark.anyio
async def test_a_failed_write_says_one_line_and_the_round_goes_on(tmp_path, capsys):
    log = Log()

    class Failing(BeatsDouble):
        def write(self, key, checkout, at, phase, interval_s) -> None:
            raise OSError("disk full")

    with pytest.raises(Stop):
        await run_poll(tmp_path, log, Failing(log), sleeper(log, [Stop()]))
    assert log.events == ["acquire", "sleep", "release"]
    out = capsys.readouterr().out
    assert out.count("heartbeat start not written: disk full") == 1
    assert out.count("heartbeat end not written: disk full") == 1


@pytest.mark.anyio
async def test_a_dry_round_writes_no_heartbeat(tmp_path):
    log = Log()
    await run_poll(tmp_path, log, BeatsDouble(log), sleeper(log, []), dry_run=True)
    assert log.events == []


@pytest.mark.anyio
async def test_a_second_poller_refused_at_the_lock_leaves_the_holders_heartbeat_alone(tmp_path):
    root = tmp_path / "rotation"
    beats = FilesystemHeartbeats(root)
    # The key the refused poller would write: the same one `_poll` builds from the same ports.
    key = heartbeat_key(product_name(memory_ports()), poll.LOCK_KEY)
    assert key is not None
    beats.write(key, tmp_path, datetime(2026, 11, 1, tzinfo=UTC), "end", 60.0)
    file = root / "heartbeats" / f"{key}.json"
    before = file.read_bytes()
    log = Log()
    with pytest.raises(cli.CannotStart):
        await run_poll(tmp_path, log, beats, sleeper(log, []), lock=LockDouble(log, refuse=True))
    assert file.read_bytes() == before
    assert json.loads(before)["phase"] == "end"
    assert log.events == []  # no release either: it never held the lock


@pytest.mark.anyio
async def test_the_refused_poller_test_would_notice_a_write_to_the_holders_key(tmp_path):
    """The same setup with a poller that did get the lock: it overwrites that very file."""
    root = tmp_path / "rotation"
    beats = FilesystemHeartbeats(root)
    key = heartbeat_key(product_name(memory_ports()), poll.LOCK_KEY)
    assert key is not None
    beats.write(key, tmp_path, datetime(2026, 11, 1, tzinfo=UTC), "end", 60.0)
    file = root / "heartbeats" / f"{key}.json"
    before = file.read_bytes()
    log = Log()
    with pytest.raises(Stop):
        await run_poll(tmp_path, log, beats, sleeper(log, [Stop()]))
    assert file.read_bytes() != before


@pytest.mark.parametrize(
    ("held", "released"), [(True, ["acquire", "release"]), (False, ["acquire"])]
)
def test_locked_releases_only_while_the_hold_says_held(held, released):
    log = Log()
    hold = cli._PollHold()
    with cli._locked(LockDouble(log), poll.LOCK_KEY, hold=hold):
        hold.held = held
    assert log.events == released


def test_locked_without_a_hold_always_releases():
    log = Log()
    with cli._locked(LockDouble(log), "GH-1"):
        pass
    assert log.events == ["acquire", "release"]


# --- the re-exec onto the real stage0 ---------------------------------------------------

SHA = "a" * 40


class Execd(BaseException):
    """What a successful `execve` double raises: the process image is gone."""


class StoreDouble:
    def __init__(self, sha: str | None) -> None:
        self.sha = sha

    def current(self) -> str | None:
        return self.sha


def make_stage(root: Path, sha: str = SHA, mode: int | None = 0o755) -> Path:
    """`<root>/<sha>` with a `.venv/bin/agentic` of the given mode (None: no executable)."""
    stage = root / sha
    stage.mkdir(parents=True)
    if mode is not None:
        exe = stage / ".venv" / "bin" / "agentic"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n")
        exe.chmod(mode)
    return stage.resolve()


def execve_double(log: Log, fail: BaseException | None = None):
    def execve(path, argv, env):
        log.events.append(("execve", path, os.getcwd()))
        execve.calls.append((path, argv, env))
        raise fail or Execd()

    execve.calls = []
    return execve


@pytest.fixture
def rounds(monkeypatch):
    """`poll.run_once` replaced by a recorder of the executable every round would spawn with."""

    def install(log: Log) -> None:
        def fake(cfg, ports, registry, launcher, now, executable, **kwargs):
            log.events.append(("run_once", executable))
            return []

        monkeypatch.setattr(poll, "run_once", fake)

    return install


def real_exe(stage: Path) -> str:
    return str(stage / ".venv" / "bin" / "agentic")


class ProcDouble:
    """A /proc double: `states_by_pid` maps each inherited pid to the states its rounds see."""

    def __init__(self, states_by_pid: dict[int, list[str]]) -> None:
        self.read = 0
        self.states_by_pid = states_by_pid
        self.reaped: list[int] = []

    def children(self) -> frozenset[int]:
        self.read += 1
        return frozenset(self.states_by_pid)

    def states(self, pids) -> dict[int, str]:
        return {pid: self.states_by_pid[pid].pop(0) for pid in pids if self.states_by_pid[pid]}

    def reap(self, pid: int) -> None:
        self.reaped.append(pid)


@pytest.mark.anyio
async def test_under_current_the_first_spawn_gets_the_resolved_path(tmp_path, rounds):
    root = stage0_paths.DEFAULT_ROOT
    stage = make_stage(root)
    (root / "current").symlink_to(stage)
    log = Log()
    rounds(log)
    execve = execve_double(log, fail=OSError("exec format error"))
    with pytest.raises(Stop):
        await run_poll(
            tmp_path,
            log,
            None,
            sleeper(log, [Stop()]),
            stage0=StoreDouble(SHA),
            execve=execve,
            argv0=str(root / "current" / ".venv" / "bin" / "agentic"),
        )
    kinds = [e if isinstance(e, str) else e[0] for e in log.events]
    assert kinds.index("execve") < kinds.index("run_once")  # no spawn before the re-exec
    spawned = [e[1] for e in log.events if isinstance(e, tuple) and e[0] == "run_once"]
    assert spawned == [real_exe(stage)]
    assert "current" not in Path(spawned[0]).parts


@pytest.mark.anyio
async def test_a_real_argv0_and_no_current_does_not_re_exec(tmp_path, rounds):
    log = Log()
    rounds(log)
    execve = execve_double(log)
    with pytest.raises(Stop):
        await run_poll(
            tmp_path,
            log,
            None,
            sleeper(log, [Stop()]),
            stage0=StoreDouble(None),
            execve=execve,
            argv0=str(stage0_paths.CHECKOUT / ".venv" / "bin" / "agentic"),
        )
    assert execve.calls == []
    assert [e for e in log.events if isinstance(e, str)] == ["acquire", "sleep", "release"]


@pytest.mark.anyio
async def test_current_elsewhere_re_execs_with_absolute_paths_and_a_clean_env(
    tmp_path, rounds, monkeypatch
):
    stage = make_stage(stage0_paths.DEFAULT_ROOT)
    monkeypatch.setenv("VIRTUAL_ENV", "/old/venv")
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}:/usr/bin")
    before = dict(os.environ)
    log = Log()
    rounds(log)
    execve = execve_double(log)
    with pytest.raises(Execd):
        await run_poll(
            tmp_path, log, None, sleeper(log, []), stage0=StoreDouble(SHA), execve=execve
        )
    assert log.events == ["acquire", "release", ("execve", real_exe(stage), str(stage))]
    path, argv, env = execve.calls[0]
    assert argv[0] == path == real_exe(stage)
    assert argv[1:] == ["poll", "--product-dir", str(tmp_path), "--interval", "60.0"]
    assert "VIRTUAL_ENV" not in env and str(Path(sys.executable).parent) not in env["PATH"]
    assert env[REEXEC_MARKER] == str(stage)
    assert os.environ == before


@pytest.mark.parametrize("why", ["missing", "not_executable", "getcwd", "builder"])
@pytest.mark.anyio
async def test_a_failed_pre_check_makes_no_lock_call_and_the_loop_goes_on(
    tmp_path, rounds, monkeypatch, capsys, why
):
    mode = {"missing": None, "not_executable": 0o644}.get(why, 0o755)
    make_stage(stage0_paths.DEFAULT_ROOT, mode=mode)
    if why == "getcwd":
        monkeypatch.setattr(cli.os, "getcwd", lambda: (_ for _ in ()).throw(OSError("gone")))
    if why == "builder":
        monkeypatch.setattr(cli, "reexec_argv", lambda *a: (_ for _ in ()).throw(ValueError("x")))
    log = Log()
    rounds(log)
    execve = execve_double(log)
    with pytest.raises(Stop):
        await run_poll(
            tmp_path,
            log,
            None,
            sleeper(log, [Stop()]),
            stage0=StoreDouble(SHA),
            execve=execve,
            argv0=str(stage0_paths.CHECKOUT / "agentic"),
        )
    assert execve.calls == []
    assert [e for e in log.events if isinstance(e, str)] == ["acquire", "sleep", "release"]
    assert capsys.readouterr().out.count("reexec_failed") == 1


@pytest.mark.anyio
async def test_a_failing_flush_does_not_stop_the_exec(tmp_path, rounds, monkeypatch, capsys):
    make_stage(stage0_paths.DEFAULT_ROOT)

    def broken() -> None:
        raise RuntimeError("collector down")

    monkeypatch.setattr(cli.telemetry, "flush", broken)
    log = Log()
    rounds(log)
    execve = execve_double(log)
    with pytest.raises(Execd):
        await run_poll(
            tmp_path, log, None, sleeper(log, []), stage0=StoreDouble(SHA), execve=execve
        )
    assert len(execve.calls) == 1
    assert "flush" in capsys.readouterr().out


@pytest.mark.anyio
async def test_a_failed_execve_leaves_cwd_env_and_lock_as_they_were_and_retries(
    tmp_path, rounds, capsys
):
    make_stage(stage0_paths.DEFAULT_ROOT)
    cwd, environ = os.getcwd(), dict(os.environ)
    log = Log()
    rounds(log)
    execve = execve_double(log, fail=OSError("boom"))
    with pytest.raises(Stop):
        await run_poll(
            tmp_path,
            log,
            BeatsDouble(log),
            sleeper(log, [None, Stop()]),
            stage0=StoreDouble(SHA),
            execve=execve,
        )
    assert len(execve.calls) == 2  # the next round retries
    assert os.getcwd() == cwd and dict(os.environ) == environ
    strings = [e for e in log.events if isinstance(e, str)]
    assert strings[:3] == ["acquire", "release", "acquire"]
    assert strings[-1] == "release" and strings.count("release") == 3
    assert capsys.readouterr().out.count("reexec_failed") == 2


@pytest.mark.anyio
async def test_a_refused_re_acquire_exits_without_release_or_heartbeat(tmp_path, rounds, capsys):
    stage = make_stage(stage0_paths.DEFAULT_ROOT)
    log = Log()
    rounds(log)
    execve = execve_double(log, fail=OSError("boom"))
    await run_poll(
        tmp_path,
        log,
        BeatsDouble(log),
        sleeper(log, []),
        lock=LockDouble(log, refuse_after=1),
        stage0=StoreDouble(SHA),
        execve=execve,
    )
    assert log.events == ["acquire", "release", ("execve", real_exe(stage), str(stage))]
    assert "held by another poller" in capsys.readouterr().out


@pytest.mark.anyio
async def test_a_release_that_raises_keeps_the_lock_and_does_not_exec(tmp_path, rounds, capsys):
    make_stage(stage0_paths.DEFAULT_ROOT)
    log = Log()
    rounds(log)
    execve = execve_double(log)
    with pytest.raises(Stop):
        await run_poll(
            tmp_path,
            log,
            BeatsDouble(log),
            sleeper(log, [Stop()]),
            lock=LockDouble(log, fail_release=True),
            stage0=StoreDouble(SHA),
            execve=execve,
        )
    assert execve.calls == []
    kinds = [e if isinstance(e, str) else e[0] for e in log.events]
    assert kinds == ["acquire", "release-failed", "start", "run_once", "end", "sleep", "release"]
    assert "reexec_failed" in capsys.readouterr().out


@pytest.mark.anyio
async def test_a_blocked_re_exec_says_so_once_and_does_not_exec(
    tmp_path, rounds, monkeypatch, capsys
):
    stage = make_stage(stage0_paths.DEFAULT_ROOT)
    monkeypatch.setenv(REEXEC_MARKER, str(stage))
    log = Log()
    rounds(log)
    execve = execve_double(log)
    with pytest.raises(Stop):
        await run_poll(
            tmp_path,
            log,
            None,
            sleeper(log, [None, Stop()]),
            stage0=StoreDouble(SHA),
            execve=execve,
            proc=ProcDouble({}),
        )
    assert execve.calls == [] and log.events.count("release") == 1  # only the final exit
    assert capsys.readouterr().out.count("reexec_failed") == 1


@pytest.mark.anyio
async def test_a_re_exec_ed_process_reaps_its_inherited_zombies_by_pid(
    tmp_path, rounds, monkeypatch
):
    monkeypatch.setenv(REEXEC_MARKER, str(tmp_path))
    log = Log()
    rounds(log)
    proc = ProcDouble({5: ["Z", "S"], 6: ["S", "Z"], 7: ["S", "S"]})
    with pytest.raises(Stop):
        await run_poll(
            tmp_path,
            log,
            None,
            sleeper(log, [None, Stop()]),
            stage0=StoreDouble(None),
            proc=proc,
        )
    assert proc.reaped == [5, 6]  # by pid, each once, never `-1`
    assert proc.read == 1  # the one read of the children


@pytest.mark.anyio
async def test_a_process_without_the_marker_reads_and_reaps_nothing(tmp_path, rounds):
    log = Log()
    rounds(log)
    proc = ProcDouble({5: ["Z"]})
    with pytest.raises(Stop):
        await run_poll(
            tmp_path, log, None, sleeper(log, [Stop()]), stage0=StoreDouble(None), proc=proc
        )
    assert proc.read == 0 and proc.reaped == []


def test_the_wiring_builds_the_real_store_and_heartbeats_on_the_root(tmp_path):
    store, beats = cli._stage0_wiring(tmp_path)
    assert isinstance(store, FilesystemStage0Store) and isinstance(beats, FilesystemHeartbeats)


STUB = """#!{python}
import json, os, sys
from pathlib import Path
print(json.dumps({{
    "argv": sys.argv,
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "path": os.environ["PATH"],
    "cwd": os.getcwd(),
    "product_dir": os.environ.get("AGENTIC_PRODUCT_DIR"),
    "lock_free": not Path({lock!r}).exists(),
}}))
"""

DRIVER = """
import sys
from pathlib import Path
import anyio
from agentic import cli
from agentic.shared import stage0 as stage0_paths
from agentic.core.process.adapters.filesystem.lock import FilesystemRunLock
from agentic.core.process.adapters.filesystem.stage0_store import FilesystemStage0Store
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports

root, runs, argv0 = (Path(a) for a in sys.argv[1:4])
stage0_paths.DEFAULT_ROOT = root
anyio.run(
    lambda: cli._poll(
        Configuration(product_dir=Path("rel-product")),
        memory_ports(),
        60.0,
        run_lock=FilesystemRunLock(runs),
        registry=MemoryRunRegistry(),
        launcher=FakeLauncher(),
        argv0=str(argv0),
        stage0=FilesystemStage0Store(root),
    )
)
"""


def test_a_real_exec_lands_in_the_stub_checkout_with_the_lock_free(tmp_path):
    root, runs = tmp_path / "root", tmp_path / "runs"
    stage = root / SHA
    exe = stage / ".venv" / "bin" / "agentic"
    exe.parent.mkdir(parents=True)
    exe.write_text(STUB.format(python=sys.executable, lock=str(runs / "poll.lock")))
    exe.chmod(0o755)
    FilesystemStage0Store(root).switch_current(SHA)
    through_current = root / "current" / ".venv" / "bin" / "agentic"
    work = tmp_path / "work"
    work.mkdir()
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "VIRTUAL_ENV": "/old/venv", "PYTHONPATH": str(repo)}
    done = subprocess.run(
        [sys.executable, "-c", DRIVER, str(root), str(runs), str(through_current)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen["argv"][0] == str(stage.resolve() / ".venv" / "bin" / "agentic")
    assert "current" not in Path(seen["argv"][0]).parts
    assert seen["cwd"] == str(stage.resolve())
    assert seen["virtual_env"] is None
    assert seen["lock_free"] is True
    assert Path(seen["argv"][seen["argv"].index("--product-dir") + 1]).is_absolute()
    assert Path(seen["product_dir"]).is_absolute()


def test_the_adr_of_the_re_exec_exists_once_with_its_known_risks():
    slug = (
        "el-poller-sigue-la-rotacion-de-stage0-con-un-re-exec-"
        "y-el-rotador-revierte-lo-que-nadie-adopto"
    )
    found = list(Path(cli.__file__).resolve().parents[1].glob(f"docs/adr/*-{slug}.md"))
    assert len(found) == 1
    assert "## Riesgos conocidos" in found[0].read_text()
