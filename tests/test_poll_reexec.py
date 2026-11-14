"""The poller's round heartbeats and the lock exit gated by `_PollHold` (GH-736, part 2a).

Seams: `cli._poll` with injected heartbeats, run_lock, launcher and sleep doubles, and
`cli._locked` with its `hold` keyword. Every double writes to one shared event log, so the
order of a `stop` and a `release` is a fact of the log."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from agentic import cli
from agentic.core.process.adapters.filesystem.heartbeats import FilesystemHeartbeats
from agentic.core.process.application import poll
from agentic.core.process.application.ports import LockHeld, LockRecord
from agentic.shared import stage0 as stage0_paths
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports


class Log:
    """The one event log every double appends to."""

    def __init__(self) -> None:
        self.events: list[object] = []


class LockDouble:
    def __init__(self, log: Log, refuse: bool = False) -> None:
        self.log = log
        self.refuse = refuse

    def acquire(self, task_id: str, command: str) -> None:
        if self.refuse:
            raise LockHeld(LockRecord(1, "h", "t", "agentic poll"), "held by another poller")
        self.log.events.append("acquire")

    def release(self, task_id: str) -> None:
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
    await cli._poll(
        Configuration(product_dir=tmp_path),
        memory_ports(),
        60.0,
        run_lock=lock or LockDouble(log),
        registry=MemoryRunRegistry(),
        launcher=FakeLauncher(),
        argv0="agentic",
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
    key = "unknown-poll"
    from datetime import UTC, datetime

    beats.write(key, tmp_path, datetime(2026, 11, 1, tzinfo=UTC), "end", 60.0)
    file = root / "heartbeats" / f"{key}.json"
    before = file.read_bytes()
    log = Log()
    with pytest.raises(cli.CannotStart):
        await run_poll(tmp_path, log, beats, sleeper(log, []), lock=LockDouble(log, refuse=True))
    assert file.read_bytes() == before
    assert json.loads(before)["phase"] == "end"
    assert log.events == []  # no release either: it never held the lock


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
