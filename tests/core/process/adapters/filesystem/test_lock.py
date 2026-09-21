"""`RunLock`, over one file per task: is a process holding this task's thread right
now (Tech Lead plan, subtask `run-lock`). Every case here runs with no real second
process: a live pid is this test's own (`os.getpid()`), a dead one is a pid number
past what Linux ever hands out."""

import os

import pytest

from agentic.core.process.adapters.filesystem.lock import FilesystemRunLock
from agentic.core.process.application.ports import LockHeld

DEAD_PID = 999_999_999


def test_read_is_none_with_no_lock_written(tmp_path):
    lock = FilesystemRunLock(tmp_path / "runs")
    assert lock.read("GH-1") is None


def test_acquire_writes_this_process_and_release_frees_it(tmp_path):
    lock = FilesystemRunLock(tmp_path / "runs")
    taken_over = lock.acquire("GH-1", "agentic run t d")
    assert taken_over is None
    record = lock.read("GH-1")
    assert record is not None
    assert record.pid == os.getpid()
    assert record.command == "agentic run t d"
    lock.release("GH-1")
    assert lock.read("GH-1") is None


def test_a_live_pid_on_this_host_refuses(tmp_path):
    lock = FilesystemRunLock(tmp_path / "runs")
    lock.acquire("GH-1", "agentic run t d")
    with pytest.raises(LockHeld) as info:
        lock.acquire("GH-1", "agentic resume --issue 1")
    assert str(os.getpid()) in str(info.value)
    assert info.value.record.pid == os.getpid()
    # The refusal did not overwrite what the live process wrote.
    assert lock.read("GH-1").command == "agentic run t d"


def test_a_dead_pid_is_taken_over_and_the_old_record_comes_back(tmp_path):
    lock = FilesystemRunLock(tmp_path / "runs")
    lock._write("GH-1", pid=DEAD_PID, host=lock._host(), started_at="2026-09-15T00:00:00Z")
    taken_over = lock.acquire("GH-1", "agentic run t d")
    assert taken_over is not None
    assert taken_over.pid == DEAD_PID
    assert lock.read("GH-1").pid == os.getpid()


def test_a_lock_on_another_host_refuses_by_name_and_its_pid_is_never_probed(tmp_path):
    lock = FilesystemRunLock(tmp_path / "runs")
    # This pid is alive (it is the test's own), but the host differs: it must refuse
    # by name alone, never call `os.kill` on it.
    lock._write("GH-1", pid=os.getpid(), host="another-host", started_at="2026-09-15T00:00:00Z")
    with pytest.raises(LockHeld) as info:
        lock.acquire("GH-1", "agentic run t d")
    assert "another-host" in str(info.value)
    assert lock.read("GH-1").host == "another-host"  # untouched: no takeover happened
