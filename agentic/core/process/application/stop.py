"""`agentic stop --issue N`: the owner's deliberate stop of a task's run (GH-466).

It marks the task's lock record `stopped_deliberately` and then stops the run, so the poller
never retakes it as a crash or an orphan. The mark is written before any signal, because a
run that dies of the signal must already be marked when the poller looks at its record.
Only the pid the record names is signalled, and only after the record's boot id, host and
start time are checked (`ports.RunProcess`); the run's own scope is reaped after
(ADR-0052). Nothing here touches the task document or the tracker."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Literal

from . import release
from .ports import LockRecord, Ports, RunLock, RunProcess

StopOutcome = Literal["signalled", "killed", "marked_dead", "marked_recordless", "no_document"]

# Declared, not measured: seconds a run is given to unwind after SIGTERM (its own hard
# deadline is `reaping.STOP_DEADLINE_S`, which this must outlast) before SIGKILL.
GRACE_S = 45.0
# Declared, not measured: how often the run is looked at during the grace.
POLL_S = 0.5


def stop(
    task_id: str,
    ports: Ports,
    lock: RunLock,
    process: RunProcess,
    *,
    grace_s: float = GRACE_S,
    sleep: Callable[[float], None] = time.sleep,
) -> StopOutcome:
    if ports.documents.load(task_id) is None:
        return "no_document"
    record = lock.read(task_id)
    if record is None:
        lock.mark_stopped_recordless(task_id)
        return "marked_recordless"
    # Before any signal: the run that dies of it must find itself already marked.
    lock.keep_stopped(task_id, record.pid)
    if not process.holds(record):
        _reap(ports, task_id, record)
        return "marked_dead"
    process.terminate(record)
    outcome: StopOutcome = "signalled"
    waited = 0.0
    while process.holds(record):
        if waited >= grace_s:
            process.kill(record)
            outcome = "killed"
            break
        sleep(POLL_S)
        waited += POLL_S
    _reap(ports, task_id, record)
    return outcome


def _reap(ports: Ports, task_id: str, record: LockRecord) -> None:
    """What the run left alive, by its own pid and never the task's whole scope. Stderr."""
    reaped = release.release_processes(ports, task_id, run=record.pid)
    if line := release.summary(reaped):
        print(f"agentic: run {record.pid} left processes: {line}", file=sys.stderr)
