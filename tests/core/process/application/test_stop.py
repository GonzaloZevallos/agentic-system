"""`agentic stop --issue N` at the application seam (GH-466): the mark is written before
any signal, only the process the record names is signalled, and every outcome is one of the
closed set. Fakes for the lock, the process and the reaper: no real process is touched."""

from __future__ import annotations

from agentic.core.process.application import stop as stopmod
from agentic.core.process.application.ports import LockRecord
from tests.fakes import FakeReaper, memory_ports

TASK = "GH-7"


def _record(**over) -> LockRecord:
    fields = dict(
        pid=4242,
        host="h",
        started_at="2026-11-03T00:00:00Z",
        command="agentic run",
        boot_id="boot-1",
        starttime="777",
    )
    return LockRecord(**{**fields, **over})


class _Documents:
    def __init__(self, present: bool = True):
        self.present = present
        self.writes: list[str] = []

    def load(self, task_id):
        return {"id": task_id} if self.present else None

    def save(self, state):
        self.writes.append("save")

    def write_into(self, task_id, root):
        self.writes.append("write_into")


class _Journal:
    """One ordered list every fake appends to, so the test can assert what came first."""

    def __init__(self):
        self.calls: list[str] = []


class _Lock:
    def __init__(self, journal: _Journal, record: LockRecord | None):
        self.journal, self.record = journal, record

    def read(self, task_id):
        return self.record

    def keep_stopped(self, task_id, pid):
        self.journal.calls.append(f"keep_stopped:{pid}")

    def mark_stopped_recordless(self, task_id):
        self.journal.calls.append("mark_recordless")

    def release(self, task_id):
        raise AssertionError("agentic stop never releases a lock")

    def acquire(self, task_id, command):
        raise AssertionError("agentic stop never takes a lock")


class _Process:
    """`holds` answers `alive` while the run has not been signalled (or `survives` the
    SIGTERM); a `mismatch` is a pid that now belongs to another process."""

    def __init__(self, journal: _Journal, *, alive: bool, survives_term: bool = False):
        self.journal = journal
        self.alive = alive
        self.survives_term = survives_term

    def holds(self, record):
        return self.alive

    def terminate(self, record):
        self.journal.calls.append(f"term:{record.pid}")
        if not self.survives_term:
            self.alive = False

    def kill(self, record):
        self.journal.calls.append(f"kill:{record.pid}")
        self.alive = False


def _run(record, *, alive=True, survives_term=False, present=True, grace_s=2.0):
    journal = _Journal()
    reaper = FakeReaper()
    documents = _Documents(present)
    ports = memory_ports(documents=documents, reaper=reaper)
    lock = _Lock(journal, record)
    process = _Process(journal, alive=alive, survives_term=survives_term)
    naps: list[float] = []
    outcome = stopmod.stop(
        TASK, ports, lock, process, grace_s=grace_s, sleep=lambda s: naps.append(s)
    )
    return outcome, journal.calls, reaper, documents, naps


def test_the_mark_is_written_before_the_first_signal():
    outcome, calls, reaper, _, _ = _run(_record())
    assert outcome == "signalled"
    assert calls == ["keep_stopped:4242", "term:4242"]
    assert reaper.calls[0] == ("run", TASK, 4242, None)


def test_a_run_that_outlives_the_grace_is_killed_after_the_term():
    outcome, calls, _, _, naps = _run(_record(), survives_term=True, grace_s=2.0)
    assert outcome == "killed"
    assert calls == ["keep_stopped:4242", "term:4242", "kill:4242"]
    assert sum(naps) == 2.0


def test_a_killed_run_has_its_own_scope_reaped_and_the_document_is_never_written():
    outcome, _, reaper, documents, _ = _run(_record(), survives_term=True)
    assert outcome == "killed"
    assert reaper.calls[0] == ("run", TASK, 4242, None)
    assert documents.writes == []


def test_a_pid_whose_start_time_no_longer_matches_is_marked_and_never_signalled():
    """The rejected input: the record names pid 4242, but the process there now is another
    one (`holds` is False: the start time differs), so no signal may be sent."""
    outcome, calls, reaper, _, _ = _run(_record(starttime="old"), alive=False)
    assert outcome == "marked_dead"
    assert calls == ["keep_stopped:4242"]
    assert not any(call.startswith(("term", "kill")) for call in calls)
    assert reaper.calls[0] == ("run", TASK, 4242, None)


def test_a_dead_record_is_marked_with_no_signal_and_its_scope_reaped():
    outcome, calls, reaper, _, naps = _run(_record(), alive=False)
    assert outcome == "marked_dead"
    assert calls == ["keep_stopped:4242"]
    assert naps == []
    assert {call[2] for call in reaper.calls} == {4242}
    assert {call[1] for call in reaper.calls} >= {TASK}


def test_a_task_with_no_record_gets_a_mark_only_record_and_nothing_is_signalled():
    outcome, calls, reaper, _, _ = _run(None)
    assert outcome == "marked_recordless"
    assert calls == ["mark_recordless"]
    assert reaper.calls == []


def test_a_task_with_no_document_touches_nothing():
    outcome, calls, reaper, documents, _ = _run(_record(), present=False)
    assert outcome == "no_document"
    assert calls == []
    assert reaper.calls == []
    assert documents.writes == []
