"""`liveness`: the one reader of the kernel's boot id and the one pid probe (GH-176)."""

import os

from agentic.core.process.adapters.filesystem import liveness

DEAD_PID = 999_999_999


def test_current_boot_id_is_the_stripped_proc_file(tmp_path, monkeypatch):
    boot = tmp_path / "boot_id"
    boot.write_text("abc-123\n", encoding="utf-8")
    monkeypatch.setattr(liveness, "BOOT_ID_PATH", boot)
    assert liveness.current_boot_id() == "abc-123"


def test_current_boot_id_is_none_when_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(liveness, "BOOT_ID_PATH", tmp_path / "missing")
    assert liveness.current_boot_id() is None


def test_current_boot_id_is_none_when_empty(tmp_path, monkeypatch):
    boot = tmp_path / "boot_id"
    boot.write_text("\n", encoding="utf-8")
    monkeypatch.setattr(liveness, "BOOT_ID_PATH", boot)
    assert liveness.current_boot_id() is None


def test_a_live_pid_from_another_boot_is_dead():
    """The rejected input: `os.kill` would succeed (it is this test's own pid)."""
    assert liveness.is_alive(os.getpid(), "old-boot", "new-boot") is False


def test_a_live_pid_from_the_same_boot_is_alive():
    assert liveness.is_alive(os.getpid(), "boot", "boot") is True


def test_a_dead_pid_from_the_same_boot_is_dead():
    assert liveness.is_alive(DEAD_PID, "boot", "boot") is False


def test_without_a_recorded_boot_the_os_decides():
    assert liveness.is_alive(os.getpid(), None, "boot") is True
    assert liveness.is_alive(DEAD_PID, None, "boot") is False


def test_without_a_current_boot_the_os_decides():
    assert liveness.is_alive(os.getpid(), "boot", None) is True


def test_a_pid_owned_by_someone_else_is_alive(monkeypatch):
    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(liveness.os, "kill", denied)
    assert liveness.is_alive(1, None, None) is True


def _spawn_sleeper():
    import subprocess

    return subprocess.Popen(["sleep", "60"])


def _record_of(process, **over):
    import socket

    from agentic.core.process.application.ports import LockRecord

    fields = dict(
        pid=process.pid,
        host=socket.gethostname(),
        started_at="2026-11-03T00:00:00Z",
        command="agentic run",
        boot_id=liveness.current_boot_id(),
        starttime=liveness.starttime_of(process.pid),
    )
    return LockRecord(**{**fields, **over})


def test_run_process_terminates_the_process_the_record_names():
    process = _spawn_sleeper()
    try:
        control = liveness.OsRunProcess()
        record = _record_of(process)
        assert control.holds(record) is True
        control.terminate(record)
        assert process.wait(timeout=10) == -15
    finally:
        process.kill()
        process.wait()


def test_run_process_never_signals_a_process_whose_start_time_differs():
    """The rejected input: the pid is alive and on this boot, but its start time is not the
    one the record holds, as after a pid reuse."""
    process = _spawn_sleeper()
    try:
        control = liveness.OsRunProcess()
        record = _record_of(process, starttime="1")
        assert control.holds(record) is False
        control.terminate(record)
        control.kill(record)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()


def test_run_process_never_signals_a_record_that_holds_no_start_time():
    """The rejected input: a record written before start times were recorded names a live
    pid on this boot, but cannot tell which process it was, so nothing is signalled."""
    process = _spawn_sleeper()
    try:
        control = liveness.OsRunProcess()
        record = _record_of(process, starttime=None)
        assert control.holds(record) is False
        control.terminate(record)
        control.kill(record)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()


def test_run_process_never_signals_another_boots_or_another_hosts_record():
    process = _spawn_sleeper()
    try:
        control = liveness.OsRunProcess()
        for record in (
            _record_of(process, boot_id="other-boot"),
            _record_of(process, host="elsewhere"),
        ):
            assert control.holds(record) is False
            control.kill(record)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()
