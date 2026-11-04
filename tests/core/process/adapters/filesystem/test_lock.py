"""`RunLock`, over one file per task: is a process holding this task's thread right
now (Tech Lead plan, subtask `run-lock`). Every case here runs with no real second
process: a live pid is this test's own (`os.getpid()`), a dead one is a pid number
past what Linux ever hands out."""

import os

import pytest

from agentic.core.process.adapters.filesystem import lock as lock_module
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


def test_release_leaves_a_lock_someone_else_now_holds_untouched(tmp_path):
    """The port's own contract: "Release, if this process holds it." A lock recorded
    under a different pid -- someone else's claim, taken over after this process lost
    its own -- must survive this process calling `release`."""
    lock = FilesystemRunLock(tmp_path / "runs")
    lock._write("GH-1", pid=os.getpid() + 1, host=lock._host(), started_at="2026-09-15T00:00:00Z")
    lock.release("GH-1")
    record = lock.read("GH-1")
    assert record is not None
    assert record.pid == os.getpid() + 1


def test_release_with_no_lock_written_is_a_no_op(tmp_path):
    lock = FilesystemRunLock(tmp_path / "runs")
    lock.release("GH-1")  # must not raise
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


def test_a_lock_on_another_host_refuses_by_name_and_its_pid_is_never_probed(tmp_path, monkeypatch):
    lock = FilesystemRunLock(tmp_path / "runs")

    # This pid is alive (it is the test's own), but the host differs: it must refuse
    # by name alone, never call `os.kill` on it -- a recycled pid on another host is a
    # coin flip. Patching `os.kill` to blow up makes that failure loud instead of the
    # message text alone, which both `LockHeld` branches interpolate the host into and
    # so cannot tell them apart.
    def _kill_must_not_be_called(pid, sig):
        raise AssertionError("a pid on another host must never be probed")

    monkeypatch.setattr(lock_module.os, "kill", _kill_must_not_be_called)
    lock._write("GH-1", pid=os.getpid(), host="another-host", started_at="2026-09-15T00:00:00Z")
    with pytest.raises(LockHeld) as info:
        lock.acquire("GH-1", "agentic run t d")
    assert "is locked by pid" in str(info.value)  # the by-name branch, not "already running"
    assert "another-host" in str(info.value)
    assert lock.read("GH-1").host == "another-host"  # untouched: no takeover happened


def test_refusal_is_none_when_free_and_writes_nothing(tmp_path):
    lock = FilesystemRunLock(tmp_path / "runs")
    assert lock.refusal("GH-1") is None
    assert lock.read("GH-1") is None


def test_refusal_matches_what_acquire_would_raise_and_takes_nothing(tmp_path):
    """`cli._refuse_if_locked` (`--detach`'s read-only parent-side check) relies on
    this staying the exact message `acquire` raises with, since it never calls
    `acquire` itself: same source, so it cannot drift."""
    lock = FilesystemRunLock(tmp_path / "runs")
    lock.acquire("GH-1", "agentic run t d")
    message = lock.refusal("GH-1")
    assert message is not None
    with pytest.raises(LockHeld) as info:
        lock.acquire("GH-1", "agentic resume --issue 1")
    assert message == str(info.value)
    # A read-only refusal is not itself an acquisition.
    assert lock.read("GH-1").command == "agentic run t d"


# -- boot id (GH-176) -----------------------------------------------------------------


def _lock(tmp_path, boot):
    return FilesystemRunLock(tmp_path / "runs", boot_id=lambda: boot)


def test_acquire_writes_the_boot_id(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.acquire("GH-1", "agentic run t d")
    assert lock.read("GH-1").boot_id == "boot-1"


def test_a_live_pid_recorded_in_another_boot_is_taken_over_and_not_refused(tmp_path):
    """The rejected input: the pid is this test's own, so `os.kill` says alive."""
    old = _lock(tmp_path, "boot-1")
    old.acquire("GH-1", "agentic run t d")
    new = _lock(tmp_path, "boot-2")
    assert new.refusal("GH-1") is None
    taken_over = new.acquire("GH-1", "agentic resume --issue 1")
    assert taken_over is not None
    assert taken_over.boot_id == "boot-1"
    assert new.read("GH-1").boot_id == "boot-2"


def test_a_live_pid_in_the_same_boot_still_refuses(tmp_path):
    _lock(tmp_path, "boot-1").acquire("GH-1", "agentic run t d")
    assert _lock(tmp_path, "boot-1").refusal("GH-1") is not None


def test_a_record_without_a_boot_id_behaves_as_before(tmp_path):
    lock = _lock(tmp_path, "boot-2")
    lock._write("GH-1", pid=os.getpid(), host=lock._host(), started_at="2026-09-15T00:00:00Z")
    path = lock._path("GH-1")
    path.write_text(path.read_text().replace(', "boot_id": null', ""), encoding="utf-8")
    assert lock.read("GH-1").boot_id is None
    assert lock.refusal("GH-1") is not None  # os.kill decides: the pid is alive
    lock._write("GH-2", pid=DEAD_PID, host=lock._host(), started_at="2026-09-15T00:00:00Z")
    assert lock.refusal("GH-2") is None


def test_keep_at_shutdown_marks_this_processs_record_and_keeps_the_boot_id(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.acquire("GH-1", "agentic run t d")
    lock.keep_at_shutdown("GH-1")
    record = lock.read("GH-1")
    assert record.stopped_at_shutdown is True
    assert record.boot_id == "boot-1"
    assert record.command == "agentic run t d"


def test_keep_at_shutdown_leaves_another_pids_record_untouched(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock._write(
        "GH-1",
        pid=os.getpid() + 1,
        host=lock._host(),
        started_at="2026-09-15T00:00:00Z",
        boot_id="boot-1",
    )
    lock.keep_at_shutdown("GH-1")
    assert lock.read("GH-1").stopped_at_shutdown is False


def test_keep_at_shutdown_with_no_record_is_a_no_op(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.keep_at_shutdown("GH-1")
    assert lock.read("GH-1") is None


def test_keep_crashed_marks_this_processs_record_and_keeps_the_boot_id(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.acquire("GH-1", "agentic run t d")
    lock.keep_crashed("GH-1", "abc123+dirty")
    record = lock.read("GH-1")
    assert record.crashed_version == "abc123+dirty"
    assert record.stopped_at_shutdown is False
    assert record.boot_id == "boot-1"
    assert record.command == "agentic run t d"
    assert record.pid == os.getpid()


def test_keep_crashed_leaves_another_pids_record_untouched(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock._write(
        "GH-1",
        pid=os.getpid() + 1,
        host=lock._host(),
        started_at="2026-09-15T00:00:00Z",
        boot_id="boot-1",
    )
    lock.keep_crashed("GH-1", "abc123")
    assert lock.read("GH-1").crashed_version is None


def test_keep_crashed_with_no_record_is_a_no_op(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.keep_crashed("GH-1", "abc123")
    assert lock.read("GH-1") is None


def test_a_record_without_the_crash_key_reads_as_none_and_is_not_written_with_one(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.acquire("GH-1", "agentic run t d")
    assert lock.read("GH-1").crashed_version is None
    assert "crashed_version" not in lock._path("GH-1").read_text(encoding="utf-8")
    lock.keep_at_shutdown("GH-1")
    assert lock.read("GH-1").crashed_version is None


def test_acquire_records_the_holders_starttime_and_the_mark_is_absent(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.acquire("GH-1", "agentic run t d")
    record = lock.read("GH-1")
    assert record.starttime is not None
    assert record.stopped_deliberately is False
    assert "stopped_deliberately" not in lock._path("GH-1").read_text(encoding="utf-8")


def test_keep_stopped_marks_the_record_and_keeps_pid_boot_id_and_starttime(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.acquire("GH-1", "agentic run t d")
    before = lock.read("GH-1")
    lock.keep_stopped("GH-1", before.pid)
    after = lock.read("GH-1")
    assert after.stopped_deliberately is True
    assert (after.pid, after.host, after.boot_id, after.starttime) == (
        before.pid,
        before.host,
        before.boot_id,
        before.starttime,
    )
    assert (after.started_at, after.command) == (before.started_at, before.command)


def test_keep_stopped_marks_a_dead_holders_record_without_taking_it_over(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock._write(
        "GH-1",
        pid=DEAD_PID,
        host=lock._host(),
        started_at="2026-09-15T00:00:00Z",
        boot_id="boot-0",
        starttime="123",
    )
    lock.keep_stopped("GH-1", DEAD_PID)
    record = lock.read("GH-1")
    assert (record.pid, record.boot_id, record.starttime) == (DEAD_PID, "boot-0", "123")
    assert record.stopped_deliberately is True


def test_keep_stopped_leaves_a_record_that_names_another_pid_untouched(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.acquire("GH-1", "agentic run t d")
    lock.keep_stopped("GH-1", os.getpid() + 1)
    assert lock.read("GH-1").stopped_deliberately is False


def test_keep_stopped_with_no_record_is_a_no_op(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.keep_stopped("GH-1", 1)
    assert lock.read("GH-1") is None


def test_a_recordless_mark_writes_this_processs_own_identity(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.mark_stopped_recordless("GH-1")
    record = lock.read("GH-1")
    assert record.stopped_deliberately is True
    assert record.pid == os.getpid()
    assert record.boot_id == "boot-1"
    assert record.starttime is not None


def test_a_recordless_mark_never_overwrites_an_existing_record(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock._write("GH-1", pid=DEAD_PID, host=lock._host(), started_at="2026-09-15T00:00:00Z")
    lock.mark_stopped_recordless("GH-1")
    assert lock.read("GH-1").pid == DEAD_PID
    assert lock.read("GH-1").stopped_deliberately is False


def test_a_record_written_before_the_mark_existed_reads_as_not_stopped(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock._path("GH-1").parent.mkdir(parents=True, exist_ok=True)
    lock._path("GH-1").write_text(
        '{"pid": 7, "host": "h", "started_at": "2026-09-15T00:00:00Z", "command": "c"}',
        encoding="utf-8",
    )
    record = lock.read("GH-1")
    assert (record.stopped_deliberately, record.starttime) == (False, None)


def test_the_other_marks_keep_the_starttime(tmp_path):
    lock = _lock(tmp_path, "boot-1")
    lock.acquire("GH-1", "agentic run t d")
    starttime = lock.read("GH-1").starttime
    lock.keep_at_shutdown("GH-1")
    assert lock.read("GH-1").starttime == starttime
    lock.keep_crashed("GH-1", "abc123")
    assert lock.read("GH-1").starttime == starttime


def test_the_starttime_is_field_22_of_stat_even_when_the_command_holds_a_parenthesis(tmp_path):
    from agentic.core.process.adapters.filesystem.liveness import starttime_of

    (tmp_path / "7").mkdir()
    rest = " ".join(["S", *[str(n) for n in range(4, 22)], "98765", "23"])
    (tmp_path / "7" / "stat").write_text(f"7 (we) ird) {rest}\n", encoding="utf-8")
    assert starttime_of(7, tmp_path) == "98765"
    assert starttime_of(8, tmp_path) is None
