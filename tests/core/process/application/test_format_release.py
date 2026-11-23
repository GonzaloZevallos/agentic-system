"""GH-1020 part 2: the poller releases a noted format hold once its refusal issue is fixed. The
step (`format_release.release_fixed_holds`) and the round's entry point (`poll.run_once`) over
fake ports and a fake `FormatFixReads`; no `gh`, no git."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from agentic.core.process.application import format_release, poll
from agentic.core.process.application.format_release import FormatReleaseCarry
from agentic.core.process.application.ports import (
    IssueState,
    LockHeld,
    LockRecord,
    TrackerTimedOut,
)
from agentic.core.process.domain import rules
from tests.core.process.application import test_poll_format_retakes as held
from tests.fakes import (
    FakeFormatFixReads,
    FakeLauncher,
    MemoryDocuments,
    MemoryRunRegistry,
    memory_ports,
)

URL = "https://github.com/o/r/issues/50"
OTHER_URL = "https://github.com/o/r/issues/51"
SHA = "a" * 40
HOLD = {**held.HOLD, "issue_url": URL}
CLOSED = IssueState("CLOSED", "COMPLETED", (), SHA)
HAND_CLOSED = IssueState("CLOSED", "NOT_PLANNED", (), None)
OPEN = IssueState("OPEN", "", (), None)


class _OmitsNone(MemoryDocuments):
    """The store renders a key whose value is `None` as absent; the plain fake keeps it."""

    def save(self, state):
        return super().save({k: v for k, v in state.items() if v is not None})


class _Locks:
    """A lock fake: `records` by task, `alive` the tasks whose record reads alive."""

    def __init__(self, records=None, alive=()):
        self.records = dict(records or {})
        self.alive = set(alive)
        self.acquired: list[tuple[str, str]] = []
        self.released: list[str] = []

    def read(self, task_id):
        return self.records.get(task_id)

    def refusal(self, task_id):
        return "alive" if task_id in self.alive else None

    def acquire(self, task_id, command):
        record = self.records.get(task_id)
        if task_id in self.alive and record is not None:
            raise LockHeld(record, "held")
        self.acquired.append((task_id, command))
        self.records[task_id] = LockRecord(1, "h", "t", command)
        return record

    def release(self, task_id):
        self.released.append(task_id)
        self.records.pop(task_id, None)


def _held(ports, number=19, hold=HOLD, **kw):
    held._pause(ports, counts={held.RULE: 2}, hold=hold, number=number, **kw)


def _step(ports, reads, lock=None, carry=None, busy=(), skip=frozenset()):
    ports = replace(ports, format_fixes=reads)
    lock = lock or _Locks()
    return format_release.release_fixed_holds(
        ports,
        lock,
        lambda task_id: task_id not in busy and lock.refusal(task_id) is None,
        carry or FormatReleaseCarry(),
        skip,
    )


def _doc(ports, number=19):
    return ports.documents.load(f"GH-{number}")


def test_a_fixed_hold_is_released_with_the_record_and_one_retake_left():
    ports = memory_ports()
    _held(ports)
    reads = FakeFormatFixReads({URL: CLOSED}, {SHA: True})
    lock = _Locks()
    lines = _step(ports, reads, lock)
    assert lines == ["agentic: poll: #19 format hold released (schema_invalid)"]
    doc = _doc(ports)
    assert doc["format_hold"] is None and doc["format_retakes"] is None
    assert [r["issue_url"] for r in doc["format_released"]] == [URL]
    assert doc["format_released"][0]["closing_commit"] == SHA
    assert lock.acquired == [("GH-19", "format_release")] and lock.released == ["GH-19"]


def test_a_hand_closed_issue_releases_without_reading_the_commit():
    ports = memory_ports()
    _held(ports)
    reads = FakeFormatFixReads({URL: HAND_CLOSED})
    assert len(_step(ports, reads)) == 1
    assert reads.calls == [("closing", URL)]


def test_no_port_call_for_a_hold_without_issue_url_or_noted_at():
    ports = memory_ports()
    _held(ports, 19, hold=held.HOLD)  # no issue_url
    no_note = {k: v for k, v in HOLD.items() if k != "noted_at"}
    _held(ports, 20, hold=no_note)
    reads = FakeFormatFixReads({URL: CLOSED}, {SHA: True})
    assert _step(ports, reads) == [] and reads.calls == []
    assert _doc(ports)["format_hold"] == held.HOLD


def test_no_fixes_port_means_no_call_and_no_line():
    ports = memory_ports()
    _held(ports)
    assert _step(ports, None) == [] and _doc(ports)["format_hold"] == HOLD


def test_two_tasks_with_one_url_make_one_read_per_round():
    ports = memory_ports()
    _held(ports, 19)
    _held(ports, 20)
    reads = FakeFormatFixReads({URL: CLOSED}, {SHA: True})
    lines = _step(ports, reads)
    assert len(lines) == 2
    assert reads.calls == [("closing", URL), ("contains", SHA)]


def test_an_unreadable_read_keeps_the_hold():
    ports = memory_ports()
    _held(ports)
    reads = FakeFormatFixReads()  # the URL is unknown: None
    assert _step(ports, reads) == [] and _doc(ports)["format_hold"] == HOLD
    assert _step(ports, FakeFormatFixReads({URL: OPEN})) == []
    assert _doc(ports)["format_hold"] == HOLD


def test_a_spent_pair_is_read_in_round_one_and_not_in_round_two():
    ports = memory_ports()
    _held(ports, hold=HOLD)
    ports.documents.record_closure(
        "GH-19", {"format_released": [{"issue_url": URL, "closing_commit": SHA, "at": "x"}]}
    )
    reads, carry = FakeFormatFixReads({URL: CLOSED}, {SHA: True}), FormatReleaseCarry()
    assert _step(ports, reads, carry=carry) == []
    assert reads.calls == [("closing", URL)] and _doc(ports)["format_hold"] == HOLD
    assert _step(ports, reads, carry=carry) == [] and reads.calls == [("closing", URL)]


def test_the_waiting_lines_are_each_said_once_over_two_rounds():
    ports = memory_ports()
    _held(ports, 19)
    _held(ports, 20, hold={**HOLD, "issue_url": OTHER_URL})
    carry = FormatReleaseCarry()
    reads = FakeFormatFixReads({URL: CLOSED, OTHER_URL: CLOSED}, {SHA: False})
    first = _step(ports, reads, carry=carry)
    assert "agentic: poll: #19 format hold: fix #50 merged, not in stage0 yet" in first
    assert _step(ports, reads, carry=carry) == []
    unreadable = memory_ports()
    _held(unreadable)
    carry = FormatReleaseCarry()
    reads = FakeFormatFixReads({URL: CLOSED})  # the commit is unknown: None
    expected = ["agentic: poll: #19 format hold: fix #50 merged, stage0 unreadable"]
    assert _step(unreadable, reads, carry=carry) == expected
    assert _step(unreadable, reads, carry=carry) == []
    assert _doc(unreadable)["format_hold"] == HOLD


def test_a_task_with_a_live_run_or_a_stacked_issue_is_neither_read_nor_released():
    ports = memory_ports()
    _held(ports)
    reads = FakeFormatFixReads({URL: CLOSED}, {SHA: True})
    assert _step(ports, reads, busy={"GH-19"}) == []
    assert _step(ports, reads, skip=frozenset({19})) == []
    assert _step(ports, reads, _Locks({"GH-19": LockRecord(1, "h", "t", "run")}, {"GH-19"})) == []
    assert reads.calls == [] and _doc(ports)["format_hold"] == HOLD


def test_a_document_that_lost_its_hold_between_read_and_write_is_unchanged():
    ports = memory_ports()
    _held(ports)
    reads = FakeFormatFixReads({URL: CLOSED}, {SHA: True})
    lock = _Locks()
    original = lock.acquire

    def acquire(task_id, command):
        ports.documents.save({**_doc(ports), "format_hold": None})  # a person resumed
        return original(task_id, command)

    lock.acquire = acquire
    before = len(ports.documents.saved)
    assert _step(ports, reads, lock) == []
    assert len(ports.documents.saved) == before + 1  # only the person's save
    assert _doc(ports).get("format_released") is None and lock.released == ["GH-19"]


def test_a_dead_lock_of_another_command_is_left_alone_and_the_task_skipped():
    ports = memory_ports()
    _held(ports)
    lock = _Locks({"GH-19": LockRecord(9, "h", "t", "resume")})
    assert _step(ports, FakeFormatFixReads({URL: CLOSED}, {SHA: True}), lock) == []
    assert lock.acquired == [] and lock.records["GH-19"].command == "resume"
    assert _doc(ports)["format_hold"] == HOLD


def test_its_own_dead_lock_is_taken_over_and_said_once():
    ports = memory_ports()
    _held(ports)
    lock = _Locks({"GH-19": LockRecord(9, "h", "t", "format_release")})
    carry = FormatReleaseCarry()
    lines = _step(ports, FakeFormatFixReads({URL: CLOSED}, {SHA: True}), lock, carry)
    assert lines == [
        "agentic: poll: #19 format release took over its own dead lock (pid 9)",
        "agentic: poll: #19 format hold released (schema_invalid)",
    ]
    assert lock.released == ["GH-19"] and carry.took == {("GH-19", 9)}


def test_an_unreadable_lock_skips_the_task():
    ports = memory_ports()
    _held(ports)
    lock = _Locks()

    def broken(task_id):
        raise OSError("disk")

    lock.read = broken
    assert _step(ports, FakeFormatFixReads({URL: CLOSED}, {SHA: True}), lock) == []
    assert lock.acquired == []


def _round(tmp_path, ports, launcher=None, **kw):
    ports.tracker._queue = [19]
    return poll.run_once(
        held._cfg(tmp_path, max_concurrent_runs=2),
        ports,
        MemoryRunRegistry(),
        launcher or FakeLauncher(),
        held.T0 + timedelta(days=2),
        "agentic",
        sleep=held._no_sleep,
        lock=_Locks(),
        current_boot="boot",
        **kw,
    )


def test_a_released_task_is_retaken_in_the_same_round(tmp_path):
    ports = memory_ports(documents=_OmitsNone())
    _held(ports)
    ports = replace(ports, format_fixes=FakeFormatFixReads({URL: CLOSED}, {SHA: True}))
    launcher = FakeLauncher()
    lines = _round(tmp_path, ports, launcher, format_release_carry=FormatReleaseCarry())
    assert "agentic: poll: #19 format hold released (schema_invalid)" in lines
    assert not rules.format_held(_doc(ports))
    assert [argv[3] for argv in launcher.spawned] == ["19"]


def test_a_dry_round_makes_no_call_and_writes_nothing(tmp_path):
    ports = memory_ports()
    _held(ports)
    reads = FakeFormatFixReads({URL: CLOSED}, {SHA: True})
    ports = replace(ports, format_fixes=reads)
    _round(tmp_path, ports, dry_run=True)
    assert reads.calls == [] and _doc(ports)["format_hold"] == HOLD


def test_a_round_that_starts_nothing_makes_no_call_and_writes_nothing(tmp_path):
    ports = memory_ports(documents=_OmitsNone())
    _held(ports)
    reads = FakeFormatFixReads({URL: CLOSED}, {SHA: True})
    ports = replace(ports, format_fixes=reads)
    ports.tracker.held_issues_error = TrackerTimedOut("gh timed out")  # the holds are unread
    lines = _round(tmp_path, ports)
    assert "agentic: poll: holds unread (timed_out)" in lines
    assert reads.calls == [] and _doc(ports)["format_hold"] == HOLD
    ports.tracker.held_issues_error = None  # the control: the same round, holds read
    assert any("format hold released" in line for line in _round(tmp_path, ports))
    assert reads.calls != []


def test_a_round_without_the_port_prints_nothing_about_it(tmp_path):
    ports = memory_ports()
    _held(ports)
    lines = _round(tmp_path, ports)
    assert not any("format hold released" in line for line in lines)
    assert _doc(ports)["format_hold"] == HOLD


def test_a_held_escalated_task_with_a_dead_lock_of_this_boot_is_not_retaken():
    ports = memory_ports()
    _held(ports)
    ports.tracker._queue = [19]
    lock = _Locks({"GH-19": LockRecord(9, "h", "t", "run", boot_id="boot")})
    assert poll.gather_retakes(ports, lock, "boot")[0] == []
