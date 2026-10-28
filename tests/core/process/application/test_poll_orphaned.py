"""GH-403: the poller retakes a task of the bandeja whose run left no lock (Corrida huérfana)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueComment, IssueState
from agentic.core.process.domain import rules
from agentic.core.process.domain.scratch import SpaceReading
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_poll import _dead_lock, _Locks, _no_sleep
from tests.fakes import (
    FakeFreeSpace,
    FakeLauncher,
    MemoryDocuments,
    MemoryRunRegistry,
    RunRegistration,
    memory_ports,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
RESUME = ["agentic", "resume", "--issue", "7", "--yes", "--detach", "--from-document"]


class _Documents(MemoryDocuments):
    """Counts loads and answers `ids_touched_since` from the ids a test marks as touched."""

    def __init__(self, touched: set[str] | None = None) -> None:
        super().__init__()
        self.touched = touched or set()
        self.loads: list[str] = []

    def ids_touched_since(self, since):
        return sorted(self.touched)

    def load(self, task_id):
        self.loads.append(task_id)
        return super().load(task_id)


class _FlakyLock(_Locks):
    def __init__(self, error: Exception):
        super().__init__()
        self.error = error

    def read(self, task_id):
        raise self.error


def _ports(status="planned", *, touched=None, queue=(7,), **more):
    documents = _Documents(touched)
    ports = memory_ports(documents=documents)
    ports.tracker._queue = list(queue)
    for n in queue:
        ports.tracker.states[n] = IssueState("OPEN", "", (), None)
    if status is not None:
        documents.save({"id": "GH-7", "status": status, **more})
    return ports


def _go(ports, *, lock=None, registry=None, launcher=None, cfg=None, now=NOW):
    launcher = launcher or FakeLauncher()
    lines = poll.run_once(
        cfg or Configuration(product_dir="/p"),
        ports,
        registry or MemoryRunRegistry(),
        launcher,
        now,
        "agentic",
        sleep=_no_sleep,
        lock=lock or _Locks(),
        current_boot="boot-now",
    )
    return lines, launcher


def _comment(body: str, at: datetime) -> IssueComment:
    return IssueComment(body=body, created_at=at.isoformat().replace("+00:00", "Z"))


def test_a_planned_task_with_no_lock_and_no_live_run_is_retaken_from_the_document():
    ports = _ports()
    lines, launcher = _go(ports)
    assert launcher.spawned == [[*RESUME, "--product-dir", "/p"]]
    assert lines[0] == "agentic: poll: retake #7 -> pid 4242"
    assert ports.tracker.comments == [("GH-7", "Retaken with no lock: retake 1 of 3")]
    assert "nothing to do" not in " ".join(lines)


def test_a_planned_task_with_a_live_run_or_a_held_lock_is_left_alone():
    live = RunRegistration(pid=1, host="h", product="memory", task_id="GH-7")
    ports = _ports()
    lines, launcher = _go(ports, registry=MemoryRunRegistry([live]))
    assert launcher.spawned == [] and ports.tracker.comments == []
    held, launcher = _go(_ports(), lock=_Locks(alive={"GH-7"}))
    assert launcher.spawned == [] and held == ["agentic: poll: nothing to do this round"]


def test_a_task_with_a_lock_record_is_not_this_rules_even_when_its_run_is_dead():
    lock = _Locks(_dead_lock("GH-7", boot_id="boot-now"))
    lines, launcher = _go(_ports(), lock=lock)
    assert launcher.spawned == []
    assert not any("orphaned" in line for line in lines)  # `gather_retakes` owns it


def test_waiting_and_delivered_tasks_are_not_touched():
    for status in ("waiting_on_human", "ready_for_review"):
        lines, launcher = _go(_ports(status))
        assert launcher.spawned == [] and lines == ["agentic: poll: nothing to do this round"]


def test_an_escalated_or_delivered_or_closed_document_is_not_retaken():
    for more in (
        {"escalated": "Developer failed"},
        {"pull_request": "https://x/pull/3"},
        {"closed_at": "2026-10-28"},
    ):
        lines, launcher = _go(_ports(**more))
        assert launcher.spawned == [] and not any("orphaned" in line for line in lines)


def test_a_closed_or_unlabelled_issue_is_left_alone():
    ports = _ports(queue=())  # not in the bandeja: `queue()` lists open, labelled issues only
    lines, launcher = _go(ports)
    assert launcher.spawned == [] and lines == ["agentic: poll: nothing to do this round"]
    assert ports.documents.loads == [] or "GH-7" in ports.documents.loads  # other passes only
    assert ports.tracker.comments == []


def test_a_held_round_starts_no_orphan_retake_and_asks_nothing_of_the_tracker():
    documents = _Documents()
    full = FakeFreeSpace(SpaceReading(free_bytes=0, free_inodes=0))
    ports = memory_ports(documents=documents, free_space=full)
    ports.tracker._queue = [7]
    documents.save({"id": "GH-7", "status": "planned"})
    lines, launcher = _go(ports)
    assert launcher.spawned == [] and ports.tracker.comments == []
    assert not any("orphaned" in line for line in lines)
    assert ports.tracker.timestamped == {}


def test_a_document_written_inside_the_hour_is_quiet_and_says_so_without_asking_gh():
    ports = _ports(touched={"GH-7"})
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    assert lines == [rules.orphaned_line(7, "quiet", "60 min")]


def test_a_spent_cap_spawns_nothing_and_hands_the_task_to_the_human():
    ports = _ports()
    ports.tracker.timestamped["GH-7"] = [
        _comment(f"Retaken with no lock: retake {n} of 3", NOW - timedelta(hours=5 - n))
        for n in (1, 2, 3)
    ]
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    assert lines == [rules.orphaned_line(7, "cap_spent", "3 of 3")]
    assert "agentic resume --issue 7 --from-document" in lines[0]


def test_a_retake_that_ran_something_does_not_count_against_the_cap():
    ports = _ports(
        runs=[
            {
                "role": "Developer",
                "section": "Developer: api",
                "started_at": (NOW - timedelta(hours=2)).isoformat(),
            }
        ]
    )
    ports.tracker.timestamped["GH-7"] = [
        _comment(f"Retaken with no lock: retake {n} of 3", NOW - timedelta(hours=5))
        for n in (1, 2, 3)
    ]
    _, launcher = _go(ports)
    assert len(launcher.spawned) == 1


def test_the_second_retake_waits_on_the_retake_calendar():
    ports = _ports()
    ports.tracker.timestamped["GH-7"] = [
        _comment("Retaken with no lock: retake 1 of 3", NOW - timedelta(minutes=10))
    ]
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    due = (NOW + timedelta(minutes=20)).strftime("%H:%M UTC")
    assert lines == [rules.orphaned_line(7, "calendar", due)]


def test_a_human_comment_resets_the_orphan_count():
    ports = _ports()
    ports.tracker.timestamped["GH-7"] = [
        _comment("Retaken with no lock: retake 1 of 3", NOW - timedelta(hours=9)),
        _comment("Retaken with no lock: retake 2 of 3", NOW - timedelta(hours=8)),
        _comment("Retaken with no lock: retake 3 of 3", NOW - timedelta(hours=7)),
        _comment("go on, I fixed it", NOW - timedelta(hours=6)),
    ]
    _, launcher = _go(ports)
    assert len(launcher.spawned) == 1
    assert ports.tracker.comments == [("GH-7", "Retaken with no lock: retake 1 of 3")]


def test_an_unreadable_lock_is_a_line_and_the_pass_goes_on():
    for error in (ValueError("bad json"), OSError("denied"), KeyError("boot_id")):
        ports = _ports(queue=(7, 8))
        ports.documents.save({"id": "GH-8", "status": "planned"})
        queued = poll.queue_documents(ports)
        lock = _FlakyLock(error)
        orphans, lines = poll.gather_orphans(ports, lock, queued, frozenset(), set(), NOW)
        assert orphans == {}
        assert [line.split(":")[2] for line in lines] == [
            " #7 orphaned (no lock)",
            " #8 orphaned (no lock)",
        ]
        assert all("lock unreadable" in line and "--from-document" in line for line in lines)


def test_the_cap_of_the_machine_holds_an_orphan_back_with_a_line():
    ports = _ports()
    live = RunRegistration(pid=1, host="h", product="p", task_id="GH-9")
    lines, launcher = _go(
        ports,
        registry=MemoryRunRegistry([live]),
        cfg=Configuration(product_dir="/p", max_concurrent_runs=1),
    )
    assert launcher.spawned == []
    assert lines == [rules.orphaned_line(7, "no_slot")]


def test_each_queue_document_is_loaded_once_by_the_launch_and_orphan_passes():
    ports = _ports(queue=(7, 8))
    documents = ports.documents
    queued = poll.queue_documents(ports)
    assert sorted(documents.loads) == ["GH-7", "GH-8"]
    documents.loads.clear()
    poll.gather_launches(
        Configuration(product_dir="/p"), ports, MemoryRunRegistry(), documents=queued
    )
    poll.gather_orphans(ports, _Locks(), queued, frozenset(), set(), NOW)
    assert documents.loads == []  # neither pass read a file the mapping already held


def test_the_cheap_checks_rule_a_task_out_before_the_lock_the_stat_or_gh_are_asked():
    ports = _ports("ready_for_review")
    lock = _Locks()
    ports.documents.touched = {"GH-7"}
    poll.gather_orphans(ports, lock, poll.queue_documents(ports), frozenset(), set(), NOW)
    assert lock.reads == []
    assert ports.tracker.timestamped == {}


def test_an_orphan_line_beside_a_blocked_queue_prints_both_and_never_nothing_to_do():
    ports = _ports(queue=(7, 8))
    ports.tracker.states[8] = IssueState("OPEN", "", (59,), None)
    ports.tracker.states[59] = IssueState("CLOSED", "COMPLETED", (), None)
    ports.documents.touched = {"GH-7"}
    lines, launcher = _go(ports)
    assert lines[0] == rules.orphaned_line(7, "quiet", "60 min")
    assert lines[1] == (
        "agentic: poll: queue blocked by closed issues it cannot resolve: #59 (no merge recorded)"
    )
    assert len(lines) == 2 and launcher.spawned == []
