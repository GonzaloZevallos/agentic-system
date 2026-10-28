"""`FilesystemPerfLock`: the machine-wide lock two measurements take (GH-318). No second
process: a live pid is this test's own, a dead one is a number past what Linux hands out,
and time is a fake clock the fake sleep moves."""

import json
import os
import socket

import pytest

from agentic.core.process.adapters.filesystem.perf_lock import POLL_INTERVAL_S, FilesystemPerfLock
from agentic.core.process.adapters.shell import EventStopFlag, NullStopFlag
from agentic.core.process.application.ports import RunStopped, StopFlag
from tests.conformance import perf_lock_stops_with_the_flag

DEAD_PID = 999_999_999
BOOT = "boot-a"


class Time:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def lock_at(
    path, time: Time | None = None, boot: str | None = BOOT, stop: StopFlag | None = None
) -> FilesystemPerfLock:
    time = time or Time()
    return FilesystemPerfLock(
        path,
        stop or NullStopFlag(),
        boot_id=lambda: boot,
        clock=time.clock,
        sleep=time.sleep,
    )


def write_record(path, pid: int, boot: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "boot_id": boot, "host": "h", "started_at": "t"}))


def test_acquire_writes_our_record_and_release_removes_it(tmp_path):
    path = tmp_path / "state" / "perf.lock"  # the parent directory is not there yet
    lock = lock_at(path)
    assert lock.acquire(0) is True
    record = json.loads(path.read_text())
    assert record["pid"] == os.getpid()
    assert record["boot_id"] == BOOT
    assert record["host"] == socket.gethostname()
    assert record["started_at"]
    lock.release()
    assert not path.exists()


def test_a_live_record_makes_acquire_wait_the_whole_time_then_return_false(tmp_path):
    path = tmp_path / "perf.lock"
    write_record(path, os.getpid(), BOOT)
    time = Time()
    assert lock_at(path, time).acquire(10) is False
    assert sum(time.slept) >= 10
    assert set(time.slept) == {POLL_INTERVAL_S}
    assert json.loads(path.read_text())["pid"] == os.getpid()  # the holder's record stays


def test_a_holder_that_releases_while_we_wait_hands_it_over(tmp_path):
    path = tmp_path / "perf.lock"
    write_record(path, os.getpid(), BOOT)
    time = Time()

    def sleep(seconds: float) -> None:
        time.sleep(seconds)
        path.unlink()

    lock = FilesystemPerfLock(
        path, NullStopFlag(), boot_id=lambda: BOOT, clock=time.clock, sleep=sleep
    )
    assert lock.acquire(60) is True
    assert len(time.slept) == 1


def test_a_dead_pid_is_taken_over_without_waiting(tmp_path):
    path = tmp_path / "perf.lock"
    write_record(path, DEAD_PID, BOOT)
    time = Time()
    assert lock_at(path, time).acquire(0) is True
    assert time.slept == []
    assert json.loads(path.read_text())["pid"] == os.getpid()


def test_a_record_from_another_boot_is_dead_though_its_pid_is_alive(tmp_path):
    path = tmp_path / "perf.lock"
    write_record(path, os.getpid(), "boot-before")
    assert lock_at(path).acquire(0) is True
    assert json.loads(path.read_text())["boot_id"] == BOOT


def test_an_unreadable_record_is_taken_over(tmp_path):
    for content in ("", "{not json", "[]", '{"pid": "x"}', '{"boot_id": "b"}'):
        path = tmp_path / "perf.lock"
        path.write_text(content)
        assert lock_at(path).acquire(0) is True, content
        assert json.loads(path.read_text())["pid"] == os.getpid()
        path.unlink()


def test_release_leaves_a_foreign_record_alone(tmp_path):
    path = tmp_path / "perf.lock"
    write_record(path, DEAD_PID, BOOT)
    lock_at(path).release()
    assert json.loads(path.read_text())["pid"] == DEAD_PID
    write_record(path, os.getpid(), "boot-before")  # our pid, but not our boot
    lock_at(path).release()
    assert path.exists()


def test_release_with_nothing_taken_is_harmless(tmp_path):
    lock_at(tmp_path / "perf.lock").release()


def test_the_lock_path_never_shows_a_record_that_is_not_whole(tmp_path, monkeypatch):
    path = tmp_path / "perf.lock"
    seen = []
    real_link = os.link

    def checking_link(source, destination, *args, **kwargs):
        seen.append(json.loads(open(source, encoding="utf-8").read())["pid"])  # whole at link time
        real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", checking_link)
    assert lock_at(path).acquire(0) is True
    assert seen == [os.getpid()]
    assert json.loads(path.read_text())["pid"] == os.getpid()
    assert list(tmp_path.iterdir()) == [path]  # the private file is gone


def test_a_refused_acquire_leaves_the_live_holders_record_and_no_private_file_behind(tmp_path):
    path = tmp_path / "perf.lock"
    write_record(path, os.getpid(), BOOT)
    before = path.read_text()
    assert lock_at(path).acquire(0) is False
    assert path.read_text() == before
    assert list(tmp_path.iterdir()) == [path]


def test_a_stop_before_acquire_raises_and_takes_no_lock(tmp_path):
    path = tmp_path / "perf.lock"
    stop = EventStopFlag()
    stop.set()
    with pytest.raises(RunStopped):
        lock_at(path, stop=stop).acquire(10)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []  # no private file either


def test_a_stop_while_waiting_for_a_live_holder_raises_without_taking_the_lock(tmp_path):
    """The case that cost 2 x 1800 s: a live holder, a waiter, and the run told to stop. The
    waiter leaves at its next poll, takes nothing and leaves the holder's record as it was."""
    path = tmp_path / "perf.lock"
    write_record(path, os.getpid(), BOOT)
    before = path.read_text()
    time, stop = Time(), EventStopFlag()

    def sleep(seconds: float) -> None:
        time.sleep(seconds)
        stop.set()  # the stop arrives while the waiter sleeps

    lock = FilesystemPerfLock(path, stop, boot_id=lambda: BOOT, clock=time.clock, sleep=sleep)
    with pytest.raises(RunStopped):
        lock.acquire(1800)
    assert time.slept == [POLL_INTERVAL_S]  # one poll, not the 1800 s
    assert path.read_text() == before
    assert list(tmp_path.iterdir()) == [path]


def test_a_stop_while_the_holder_is_dead_does_not_take_the_lock_over(tmp_path):
    path = tmp_path / "perf.lock"
    write_record(path, DEAD_PID, BOOT)
    before = path.read_text()
    stop = EventStopFlag()
    stop.set()
    with pytest.raises(RunStopped):
        lock_at(path, stop=stop).acquire(10)
    assert path.read_text() == before


def test_a_stop_that_arrives_after_the_lock_was_taken_leaves_it_to_release(tmp_path):
    path = tmp_path / "perf.lock"
    stop = EventStopFlag()
    lock = lock_at(path, stop=stop)
    assert lock.acquire(0) is True
    stop.set()
    lock.release()
    assert not path.exists()


class _SetFromTheSecondLook:
    """A flag that is clear on the first look (the top of the loop) and set from the second
    (the one before the sleep): the stop that arrives while the waiter checks the holder."""

    def __init__(self) -> None:
        self.looks = 0

    def is_set(self) -> bool:
        self.looks += 1
        return self.looks >= 2

    def set(self) -> None:
        raise AssertionError("the lock never sets the flag")


def test_a_stop_that_arrives_while_the_holder_is_read_does_not_sleep_a_poll_first(tmp_path):
    path = tmp_path / "perf.lock"
    write_record(path, os.getpid(), BOOT)
    time = Time()
    with pytest.raises(RunStopped):
        lock_at(path, time, stop=_SetFromTheSecondLook()).acquire(1800)
    assert time.slept == []


def test_the_real_lock_keeps_the_perf_lock_contract_of_the_stop(tmp_path):
    stop = EventStopFlag()
    perf_lock_stops_with_the_flag(lock_at(tmp_path / "perf.lock", stop=stop), stop)
    assert not (tmp_path / "perf.lock").exists()
