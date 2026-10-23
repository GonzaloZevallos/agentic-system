"""`FilesystemPerfLock`: the machine-wide lock two measurements take (GH-318). No second
process: a live pid is this test's own, a dead one is a number past what Linux hands out,
and time is a fake clock the fake sleep moves."""

import json
import os
import socket

from agentic.core.process.adapters.filesystem.perf_lock import POLL_INTERVAL_S, FilesystemPerfLock

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


def lock_at(path, time: Time | None = None, boot: str | None = BOOT) -> FilesystemPerfLock:
    time = time or Time()
    return FilesystemPerfLock(path, boot_id=lambda: boot, clock=time.clock, sleep=time.sleep)


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

    lock = FilesystemPerfLock(path, boot_id=lambda: BOOT, clock=time.clock, sleep=sleep)
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
