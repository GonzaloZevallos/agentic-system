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
