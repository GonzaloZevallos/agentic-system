"""`FilesystemRunRegistry`, over `Configuration.run_registry_dir`: how many `agentic`
invocations are alive on this machine right now (GH-137, `AGENTIC_MAX_CONCURRENT_RUNS`),
and the poller's own schedule for a launch attempt that has not registered a run yet.
No real second process: a live pid is this test's own, a dead one is a pid number past
what Linux ever hands out (the same convention `test_lock.py` already uses)."""

import hashlib
import json
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentic.core.process.adapters.filesystem.run_registry import FilesystemRunRegistry
from agentic.core.process.application.ports import LaunchAttempt, RunRegistration
from agentic.core.process.domain.rules import SpendRun

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


# -- spend ledger (GH-177) ---------------------------------------------------------------

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _spend(minutes_ago: int, cost: float, length_min: int = 10) -> SpendRun:
    end = T0 - timedelta(minutes=minutes_ago)
    return SpendRun(end - timedelta(minutes=length_min), end, cost)


def test_the_spend_dir_does_not_exist_until_the_first_publish(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    assert registry.spend(T0 - timedelta(hours=5)) == ([], [])
    registry.live()
    registry.launch_attempts("apostrophe", 1)
    assert not (tmp_path / "runs" / "spend").exists()
    registry.publish_spend(tmp_path / "docs", "apostrophe", [_spend(10, 3.0)], T0)
    assert (tmp_path / "runs" / "spend").is_dir()


def test_two_publishers_with_the_same_product_name_and_different_roots_both_count(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry.publish_spend(tmp_path / "a", "apostrophe", [_spend(10, 3.0)], T0)
    registry.publish_spend(tmp_path / "b", "apostrophe", [_spend(20, 4.0)], T0)
    runs, notes = registry.spend(T0 - timedelta(hours=5))
    assert sorted(r.cost_usd for r in runs) == [3.0, 4.0]
    assert notes == []
    assert len(list((tmp_path / "runs" / "spend").glob("*.json"))) == 2


def test_a_run_published_once_is_still_read_after_its_document_is_gone(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    root = tmp_path / "docs"
    registry.publish_spend(root, "apostrophe", [_spend(10, 3.0)], T0)
    registry.publish_spend(root, "apostrophe", [], T0 + timedelta(minutes=1))
    runs, _ = registry.spend(T0 - timedelta(hours=5))
    assert runs == [_spend(10, 3.0)]


def test_a_republished_run_with_a_changed_cost_is_read_once_at_the_new_cost(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    root = tmp_path / "docs"
    registry.publish_spend(root, "apostrophe", [_spend(10, 3.0)], T0)
    registry.publish_spend(root, "apostrophe", [_spend(10, 5.5)], T0)
    runs, _ = registry.spend(T0 - timedelta(hours=5))
    assert runs == [_spend(10, 5.5)]


def test_a_run_that_left_the_window_is_dropped_on_the_next_publish(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    root = tmp_path / "docs"
    registry.publish_spend(root, "apostrophe", [_spend(10, 3.0)], T0)
    registry.publish_spend(root, "apostrophe", [], T0 + timedelta(hours=6))
    assert registry.spend(T0 - timedelta(hours=5))[0] == []


def test_a_truncated_spend_file_is_skipped_with_a_note_and_never_raises(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry.publish_spend(tmp_path / "good", "apostrophe", [_spend(10, 3.0)], T0)
    registry.publish_spend(tmp_path / "bad", "gym", [_spend(10, 9.0)], T0)
    spend_dir = tmp_path / "runs" / "spend"
    bad = next(p for p in spend_dir.glob("*.json") if '"gym"' in p.read_text(encoding="utf-8"))
    bad.write_text(bad.read_text(encoding="utf-8")[:20], encoding="utf-8")

    runs, notes = registry.spend(T0 - timedelta(hours=5))
    assert runs == [_spend(10, 3.0)]
    assert notes == [f"agentic: poll: spend file {bad.name} unreadable, skipped"]

    # the owner's next publish reads its truncated file as empty, says so, and replaces it
    notes = registry.publish_spend(tmp_path / "bad", "gym", [_spend(5, 1.0)], T0)
    assert notes == [f"agentic: poll: spend file {bad.name} unreadable, skipped"]
    assert registry.spend(T0 - timedelta(hours=5))[1] == []


def test_a_naive_or_malformed_entry_is_skipped_with_a_note_and_the_rest_is_read(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    spend_dir = tmp_path / "runs" / "spend"
    spend_dir.mkdir(parents=True)
    good = _spend(10, 3.0)
    entries = [
        [good.started_at.isoformat(), good.ended_at.isoformat(), good.cost_usd],
        ["2026-10-06T11:00:00", "2026-10-06T11:10:00", 4.0],  # naive
        ["not a date", "2026-10-06T11:10:00+00:00", 4.0],
        ["2026-10-06T11:00:00+00:00"],
    ]
    (spend_dir / "x.json").write_text(
        json.dumps({"product": "p", "root": "/r", "written_at": "", "runs": entries}),
        encoding="utf-8",
    )
    runs, notes = registry.spend(T0 - timedelta(hours=5))
    assert runs == [good]
    assert notes == ["agentic: poll: spend file x.json: 3 malformed entries skipped"]


def test_spend_returns_only_entries_that_ended_inside_the_range(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry.publish_spend(
        tmp_path / "docs",
        "apostrophe",
        [_spend(10, 3.0), _spend(400, 8.0)],
        T0 - timedelta(hours=7),
    )
    runs, _ = registry.spend(T0 - timedelta(hours=5))
    assert runs == [_spend(10, 3.0)]


def test_the_spend_file_is_keyed_by_the_resolved_root_and_written_atomically(tmp_path, monkeypatch):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    root = tmp_path / "docs"
    root.mkdir()
    replaced: list[tuple[str, str]] = []
    real = os.replace
    monkeypatch.setattr(
        os, "replace", lambda a, b: (replaced.append((str(a), str(b))), real(a, b))[1]
    )
    registry.publish_spend(root, "apostrophe", [_spend(10, 3.0)], T0)
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    final = tmp_path / "runs" / "spend" / f"{key}.json"
    assert final.is_file()
    assert [dst for _, dst in replaced] == [str(final)]
    assert Path(replaced[0][0]).parent == final.parent
    assert list(final.parent.iterdir()) == [final]  # no temp file left behind
    data = json.loads(final.read_text(encoding="utf-8"))
    assert data["product"] == "apostrophe" and data["root"] == str(root.resolve())
    assert data["runs"] == [
        [_spend(10, 3.0).started_at.isoformat(), _spend(10, 3.0).ended_at.isoformat(), 3.0]
    ]


def test_spend_files_are_invisible_to_live_and_launch_attempts(tmp_path):
    registry = FilesystemRunRegistry(tmp_path / "runs")
    registry.register("apostrophe", "GH-19")
    registry.record_launch("apostrophe", 7, T0)
    before = (registry.live(), registry.launch_attempts("apostrophe", 7))
    registry.publish_spend(tmp_path / "docs", "apostrophe", [_spend(10, 3.0)], T0)
    assert (registry.live(), registry.launch_attempts("apostrophe", 7)) == before
    assert registry.launch_attempts("apostrophe", 8) is None
