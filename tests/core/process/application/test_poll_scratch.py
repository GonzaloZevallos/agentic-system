"""GH-391: what a poller round does about scratch -- the sweep first, then the free-space
floor -- against `MemoryScratch` and `FakeFreeSpace`. No real disk and no real volume."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.application import poll, scratch_sweep
from agentic.core.process.application.ports import IssueComment, IssueState
from agentic.core.process.domain import scratch
from agentic.core.process.domain.scratch import SpaceReading
from agentic.core.process.domain.task import PullRequest
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_poll import _Locks, _TrackerWithPullRequest
from tests.fakes import (
    FakeFreeSpace,
    FakeLauncher,
    MemoryRunRegistry,
    MemoryScratch,
    memory_ports,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
MB = 1024 * 1024


def _round(tmp_path, ports, locks=None, **over):
    launcher = FakeLauncher()
    lines = poll.run_once(
        Configuration(product_dir=tmp_path, **{"scratch_dir": tmp_path / "scratch-root", **over}),
        ports,
        MemoryRunRegistry(),
        launcher,
        NOW,
        argv0="agentic",
        sleep=lambda _: None,
        lock=locks or _Locks(),
        current_boot="boot-now",
    )
    return lines, launcher


def _free(
    tmp_path, *, scratch_mb=380_000, product_mb=380_000, tmp_mb=380_000, tmp_inodes=10**7
) -> FakeFreeSpace:
    # The product and the scratch root live under /tmp in a test, so each volume is named
    # explicitly: the longest path wins.
    return FakeFreeSpace(
        by_path={
            tmp_path / "scratch-root": SpaceReading(scratch_mb * MB, 10**7),
            tmp_path / ".agentic": SpaceReading(product_mb * MB, 10**7),
            Path("/tmp"): SpaceReading(tmp_mb * MB, tmp_inodes),
        }
    )


def _owner(name, task="GH-1", age=timedelta(0), alive=None):
    record = None if alive is None else scratch.RecordFact(NOW, alive)
    return scratch.OwnerEntry(task, name, NOW - age, record)


def _disk(ports) -> MemoryScratch:
    assert isinstance(ports.scratch, MemoryScratch)
    return ports.scratch


def test_the_scratch_of_a_closed_task_is_removed_and_an_open_task_keeps_its_own():
    ports = memory_ports(scratch=MemoryScratch(), free_space=FakeFreeSpace())
    ports.documents.save({"id": "GH-3", "closed_at": "2026-10-20T00:00:00Z"})
    ports.documents.save({"id": "GH-4"})
    disk = _disk(ports)
    disk.tasks = ["GH-3", "GH-4"]
    disk.owners = [
        _owner("c-aaaaaaaa", "GH-3", alive=True),
        _owner("c-bbbbbbbb", "GH-4", alive=True),
    ]
    lines, launcher = _round(Path("/p"), ports)
    assert disk.swept[0].tasks == ("GH-3",)
    assert disk.tasks == ["GH-4"]
    assert [o.task for o in disk.owners] == ["GH-4"]
    assert lines == ["agentic: poll: scratch swept: 1 closed task(s), 0 owner(s), 0 kept folder(s)"]
    assert launcher.spawned == []


def test_a_dead_owner_goes_and_an_alive_owner_and_a_young_orphan_stay():
    ports = memory_ports(scratch=MemoryScratch(), free_space=FakeFreeSpace())
    disk = _disk(ports)
    disk.tasks = ["GH-1"]
    disk.owners = [
        _owner("c-dead0000", alive=False),
        _owner("c-live0000", alive=True),
        _owner("c-young000", age=timedelta(minutes=1)),
    ]
    _round(Path("/p"), ports)
    assert [(r.name, r.directory, r.record) for r in disk.swept[0].owners] == [
        ("c-dead0000", True, True)
    ]
    assert {o.name for o in disk.owners} == {"c-live0000", "c-young000"}


def test_a_kept_folder_goes_fourteen_days_after_its_task_closed_not_before():
    ports = memory_ports(scratch=MemoryScratch(), free_space=FakeFreeSpace())
    closed_at = NOW - scratch.KEPT_RETENTION
    ports.documents.save({"id": "GH-1", "closed_at": closed_at.isoformat()})
    ports.documents.save({"id": "GH-2", "closed_at": (closed_at + timedelta(hours=1)).isoformat()})
    ports.documents.save({"id": "GH-3", "closed_at": "garbled"})
    disk = _disk(ports)
    disk.kept_on_disk = [
        ("GH-1", NOW),
        ("GH-2", NOW),
        ("GH-3", NOW - scratch.KEPT_RETENTION - timedelta(days=1)),  # the mtime stands in
        ("GH-4", NOW - timedelta(days=90)),  # no document: its task is not known closed
    ]
    _round(Path("/p"), ports)
    assert disk.swept[0].kept == ("GH-1", "GH-3")
    assert [task for task, _ in disk.kept_on_disk] == ["GH-2", "GH-4"]


def test_a_discarded_or_split_task_counts_as_closed_from_its_finished_at():
    ports = memory_ports(scratch=MemoryScratch(), free_space=FakeFreeSpace())
    old = (NOW - scratch.KEPT_RETENTION).isoformat()
    ports.documents.save({"id": "GH-6", "status": "discarded", "finished_at": old})
    ports.documents.save({"id": "GH-7", "status": "split", "finished_at": NOW.isoformat()})
    ports.documents.save({"id": "GH-8", "status": "developing"})
    disk = _disk(ports)
    disk.tasks = ["GH-6", "GH-7", "GH-8"]
    disk.kept_on_disk = [("GH-6", NOW), ("GH-7", NOW), ("GH-8", NOW - timedelta(days=90))]
    _round(Path("/p"), ports)
    assert disk.swept[0].tasks == ("GH-6", "GH-7")
    assert disk.swept[0].kept == ("GH-6",)


def test_a_kept_folder_whose_task_document_cannot_be_read_expires_by_its_own_mtime(monkeypatch):
    ports = memory_ports(scratch=MemoryScratch(), free_space=FakeFreeSpace())

    def unreadable(task_id):
        raise ValueError("bad frontmatter")

    monkeypatch.setattr(ports.documents, "load", unreadable)
    disk = _disk(ports)
    disk.tasks = ["GH-1"]
    disk.kept_on_disk = [
        ("GH-1", NOW - scratch.KEPT_RETENTION - timedelta(days=1)),
        ("GH-2", NOW),
    ]
    _round(Path("/p"), ports)
    # Its tmp folder stays (nobody can say the task closed); the old kept folder goes.
    assert disk.swept[0].tasks == ()
    assert disk.swept[0].kept == ("GH-1",)


def test_a_round_with_nothing_to_sweep_says_nothing_and_leaves_the_disk_alone():
    ports = memory_ports(scratch=MemoryScratch(), free_space=FakeFreeSpace())
    lines, _ = _round(Path("/p"), ports)
    assert lines == ["agentic: poll: nothing to do this round"]
    assert _disk(ports).swept == []


def _busy_ports(free: FakeFreeSpace):
    """A merged pull request to close, an issue to launch and a paused task due a resume."""
    merged = PullRequest(
        state="MERGED",
        created_at="2026-10-01T00:00:00Z",
        merged_at="2026-10-02T00:00:00Z",
        commits=(),
        merge_commit="c0ffee",
    )
    tracker = _TrackerWithPullRequest({"https://github.com/o/r/pull/7": merged})
    ports = memory_ports(tracker=tracker, scratch=MemoryScratch(), free_space=free)
    ports.documents.save({"id": "GH-3", "pull_request": "https://github.com/o/r/pull/7"})
    ports.documents.save(
        {
            "id": "GH-19",
            "escalated": "Developer failed 2 times: API Error: 429",
            "pause_log": [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        }
    )
    ports.tracker.timestamped["GH-19"] = [
        IssueComment(
            body="Escalated: Developer failed 2 times: API Error: 429",
            created_at="2026-10-01T00:00:00Z",  # a day ago: its wait elapsed
        )
    ]
    ports.tracker._queue = [5]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None)
    ports.inbox.issues["5"] = {"id": "GH-5", "title": "t", "description": "no blockers"}
    return ports


def test_with_room_on_the_three_volumes_the_round_closes_and_resumes(tmp_path):
    free = _free(tmp_path)
    lines, launcher = _round(tmp_path, _busy_ports(free))
    assert [argv[1] for argv in launcher.spawned][:2] == ["close", "resume"]
    assert not any("held" in line for line in lines)
    assert free.asked == [tmp_path / "scratch-root", tmp_path / ".agentic", Path("/tmp")]


@pytest.mark.parametrize(
    ("free_kwargs", "line"),
    [
        (
            {"tmp_mb": 1400},
            "agentic: poll: held: /tmp 1400 MB < floor 2048 MB "
            "(scratch 380000 MB ok, product 380000 MB ok)",
        ),
        (
            {"scratch_mb": 100},
            "agentic: poll: held: scratch 100 MB < floor 2048 MB "
            "(product 380000 MB ok, /tmp 380000 MB ok)",
        ),
        (
            {"product_mb": 100},
            "agentic: poll: held: product 100 MB < floor 2048 MB "
            "(scratch 380000 MB ok, /tmp 380000 MB ok)",
        ),
        (
            {"scratch_mb": 100, "product_mb": 5, "tmp_mb": 2047},
            "agentic: poll: held: scratch 100 MB < floor 2048 MB, product 5 MB < floor 2048 MB, "
            "/tmp 2047 MB < floor 2048 MB",
        ),
        (
            {"tmp_inodes": 500},
            "agentic: poll: held: /tmp 500 inodes < floor 10000 "
            "(scratch 380000 MB ok, product 380000 MB ok)",
        ),
    ],
)
def test_under_the_floor_on_any_volume_the_round_only_closes_and_says_why(
    tmp_path, free_kwargs, line
):
    ports = _busy_ports(_free(tmp_path, **free_kwargs))
    lines, launcher = _round(tmp_path, ports)
    assert [argv[1] for argv in launcher.spawned] == ["close"]
    assert lines == [line, "agentic: poll: close #3 -> pid 4242"]
    assert ports.tracker.comments == []  # no resume was announced


def test_hold_line_names_the_product_volume_with_its_figures():
    low = SpaceReading(free_bytes=100 * MB, free_inodes=10**7)
    ample = SpaceReading(free_bytes=5000 * MB, free_inodes=10**7)
    readings = {"scratch": ample, "product": low, "/tmp": ample}
    assert scratch_sweep.hold_line(("product",), readings, 2048 * MB) == (
        "agentic: poll: held: product 100 MB < floor 2048 MB (scratch 5000 MB ok, /tmp 5000 MB ok)"
    )


def test_the_floor_is_configurable(tmp_path):
    free = _free(tmp_path, tmp_mb=1400)
    _, held = _round(tmp_path, _busy_ports(free))
    assert [argv[1] for argv in held.spawned] == ["close"]
    _, open_ = _round(tmp_path, _busy_ports(free), scratch_floor_mb=1000)
    assert [argv[1] for argv in open_.spawned][:2] == ["close", "resume"]


def test_the_sweep_runs_before_the_volumes_are_read():
    order: list[str] = []

    class _Disk(MemoryScratch):
        def sweep(self, decision):
            order.append("sweep")
            super().sweep(decision)

    class _Free(FakeFreeSpace):
        def read(self, path):
            order.append("read")
            return super().read(path)

    ports = memory_ports(scratch=_Disk(), free_space=_Free())
    ports.documents.save({"id": "GH-3", "closed_at": "2026-10-20T00:00:00Z"})
    _disk(ports).tasks = ["GH-3"]
    _round(Path("/p"), ports)
    assert order == ["sweep", "read", "read", "read"]


def test_a_volume_that_cannot_be_read_holds_nothing_and_says_so(tmp_path):
    class _Broken(FakeFreeSpace):
        def read(self, path):
            raise OSError("no such volume")

    lines, launcher = _round(tmp_path, _busy_ports(_Broken()))
    assert lines[0] == "agentic: poll: free space unreadable: no such volume"
    assert [argv[1] for argv in launcher.spawned][:2] == ["close", "resume"]


def test_a_held_round_starts_no_retake_merge_or_reflect_child_and_an_open_one_starts_all(tmp_path):
    from agentic.core.process.domain import playbook_candidates as pc
    from tests.core.process.application.test_poll import _dead_lock, _open_task
    from tests.core.process.application.test_poll_merge import _ports as merge_ports

    def build(free):
        ports = replace(merge_ports(5), scratch=MemoryScratch(), free_space=free)
        ports.workspace.files[("HEAD", pc.LEDGER_FILE)] = pc.render_ledger(
            pc.Ledger(started_at="2026-10-05T00:00:00+00:00")
        )
        ports.documents.save({"id": "GH-9", "title": "t", "description": "d"})
        ports.documents.record_closure(
            "GH-9", {"closed_at": "2026-10-06T09:00:00.000+00:00", "delivery_outcome": "merged"}
        )
        _open_task(ports, "GH-30")
        return ports

    held_lines, held = _round(
        tmp_path, build(_free(tmp_path, tmp_mb=10)), _Locks(_dead_lock("GH-30"))
    )
    assert held.spawned == []
    assert held_lines == [
            "agentic: poll: held: /tmp 10 MB < floor 2048 MB "
            "(scratch 380000 MB ok, product 380000 MB ok)"
        ]

    _, open_ = _round(tmp_path, build(_free(tmp_path)), _Locks(_dead_lock("GH-30")))
    kinds = {argv[1] for argv in open_.spawned}
    assert {"merge", "resume", "reflect"} <= kinds
