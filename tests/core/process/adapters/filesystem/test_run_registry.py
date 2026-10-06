"""`FilesystemRunRegistry`, over `Configuration.run_registry_dir`: how many `agentic`
invocations are alive on this machine right now (GH-137, `AGENTIC_MAX_CONCURRENT_RUNS`),
and the poller's own schedule for a launch attempt that has not registered a run yet.
No real second process: a live pid is this test's own, a dead one is a pid number past
what Linux ever hands out (the same convention `test_lock.py` already uses)."""

import socket
from datetime import UTC, datetime

from agentic.core.process.adapters.filesystem.run_registry import FilesystemRunRegistry
from agentic.core.process.application.ports import LaunchAttempt, RunRegistration

DEAD_PID = 999_999_999


def test_live_is_empty_with_nothing_registered(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    assert registry.live() == []


def test_register_and_unregister_round_trip(tmp_path):
    import os

    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry.register("apostrophe", "GH-19")
    assert registry.live() == [
        RunRegistration(os.getpid(), socket.gethostname(), "apostrophe", "GH-19")
    ]
    registry.unregister()
    assert registry.live() == []


def test_unregister_with_nothing_registered_is_a_no_op(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry.unregister()  # must not raise
    assert registry.live() == []


def test_a_dead_pids_entry_does_not_count_as_live(tmp_path):
    """The rejected input: a stale entry a crashed `run` never cleaned up must not
    occupy a slot of `AGENTIC_MAX_CONCURRENT_RUNS` forever."""
    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry._write(DEAD_PID, socket.gethostname(), "apostrophe", "GH-19")
    assert registry.live() == []


def test_an_entry_on_another_host_is_never_probed_and_does_not_count_as_live(tmp_path, monkeypatch):
    """The rejected input: a pid recorded on another host is a coin flip the same way
    `RunLock` already refuses to probe -- it must not count as live, and `os.kill` must
    never be asked about it."""
    import os

    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry._write(os.getpid(), "another-host", "apostrophe", "GH-19")

    def _kill_must_not_be_called(pid, sig):
        raise AssertionError("a pid on another host must never be probed")

    monkeypatch.setattr(
        "agentic.core.process.adapters.filesystem.run_registry.os.kill", _kill_must_not_be_called
    )
    assert registry.live() == []


def test_exclusive_is_a_reentrant_safe_file_lock(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    with registry.exclusive():
        pass
    # A second, sequential use must not deadlock.
    with registry.exclusive():
        pass


def test_launch_attempts_is_none_until_one_is_recorded(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    assert registry.launch_attempts("apostrophe", 20) is None


def test_record_launch_grows_prior_and_moves_since(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    first = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    registry.record_launch("apostrophe", 20, first)
    assert registry.launch_attempts("apostrophe", 20) == LaunchAttempt(first, 1)
    second = datetime(2026, 10, 3, 12, 30, tzinfo=UTC)
    registry.record_launch("apostrophe", 20, second)
    assert registry.launch_attempts("apostrophe", 20) == LaunchAttempt(second, 2)


def test_clear_launch_forgets_the_schedule(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry.record_launch("apostrophe", 20, datetime(2026, 10, 3, 12, 0, tzinfo=UTC))
    registry.clear_launch("apostrophe", 20)
    assert registry.launch_attempts("apostrophe", 20) is None


def test_clear_launch_with_nothing_recorded_is_a_no_op(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry.clear_launch("apostrophe", 20)  # must not raise
    assert registry.launch_attempts("apostrophe", 20) is None


def test_launches_of_different_products_or_issues_do_not_collide(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    when = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    registry.record_launch("apostrophe", 20, when)
    registry.record_launch("gym-saas", 20, when)
    registry.record_launch("apostrophe", 5, when)
    assert registry.launch_attempts("apostrophe", 20) == LaunchAttempt(when, 1)
    assert registry.launch_attempts("gym-saas", 20) == LaunchAttempt(when, 1)
    assert registry.launch_attempts("apostrophe", 5) == LaunchAttempt(when, 1)


# -- boot id (GH-176) -----------------------------------------------------------------


def test_register_writes_the_boot_id_and_live_keeps_the_same_boot(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs", boot_id=lambda: "boot-1")
    registry.register("apostrophe", "GH-19")
    assert len(registry.live()) == 1


def test_a_live_pid_registered_in_another_boot_does_not_count_as_live(tmp_path):
    """The rejected input: the pid is this test's own, so `os.kill` says alive, and a
    phantom entry would block every launch under `max_concurrent_runs = 1`."""
    FilesystemRunRegistry(tmp_path / "runs", boot_id=lambda: "boot-1").register("a", "GH-19")
    after_reboot = FilesystemRunRegistry(tmp_path / "runs", boot_id=lambda: "boot-2")
    assert after_reboot.live() == []
    assert list((tmp_path / "runs").glob("*.json"))  # live() never deletes


def test_an_entry_without_a_boot_id_is_decided_by_os_kill(tmp_path):
    import os

    registry = FilesystemRunRegistry(tmp_path / "runs", boot_id=lambda: "boot-2")
    registry._write(os.getpid(), socket.gethostname(), "a", "GH-19")
    path = tmp_path / "runs" / f"{os.getpid()}.json"
    path.write_text(path.read_text().replace(', "boot_id": null', ""), encoding="utf-8")
    assert len(registry.live()) == 1
