"""Releasing what a task left running (GH-282, ADR-0052): the one place the application
takes a task's worktree out, and the one place it stops the processes a role started.

A role that starts something in the background (`nohup`, `setsid`, `run_in_background`)
leaves the process group the system controls; the reaper finds it again by the mark every
attempt carries. `release_task` reaps the whole task and THEN calls `Workspace.cleanup`,
because the processes run out of the worktrees that `git worktree remove` is about to
delete. `release_processes` is the narrower verb for a run found dead: only that run's own
scope, never the task's. No reaper method raises (`ProcessReaper`), so a reap never stops
a delivery, a discard, an archive or a reflection; a `GitError` from `cleanup` is the
caller's, exactly as it was before."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..domain import reaping
from ..domain.reaping import ReapedProcess
from ..domain.task import WorktreeReleaseLapse
from .ports import GitError, Ports

# The scratch checkouts a merge and a landing slice make beside a task's worktrees (GH-178).
SCRATCH_PREFIXES = ("merge", "slice")
# The one pause before the one retry `release_after_merge` makes of a `GitError` (a lock held
# for a moment by another git process).
RELEASE_RETRY_PAUSE_S = 2.0


def release_processes(ports: Ports, task_id: str, run: int) -> tuple[ReapedProcess, ...]:
    """What the dead run `run` of `task_id` left alive, stopped: its roles' scope and the
    checks and performance commands it ran (GH-315, GH-385), which carry their own marks."""
    reaped = ports.reaper.reap_run(task_id, run)
    for scoped in reaping.RUN_SCOPED:
        reaped += ports.reaper.reap_run(scoped, run)
    return reaped


def release_checkout(ports: Ports, path: Path) -> tuple[ReapedProcess, ...]:
    """Stops the unmarked processes still running in the checkout `path`, before the
    caller removes it (GH-315). The only thing application code calls before it takes a
    checkout out. What it stopped is one line on stderr."""
    reaped = ports.reaper.reap_within([path])
    if line := summary(reaped):
        print(f"release: {path.name}: {line}", file=sys.stderr)
    return reaped


def release_task(ports: Ports, task_id: str) -> tuple[ReapedProcess, ...]:
    """Stops every marked process of the task, then whatever still runs in one of its
    checkouts, then removes them. Returns what was reaped; a `GitError` from the removal
    propagates unchanged."""
    reaped = ports.reaper.reap_task(task_id)
    scratch = [f"{prefix}-{task_id}" for prefix in SCRATCH_PREFIXES]
    paths = [*ports.workspace.worktrees(task_id), *map(ports.workspace.checkout_path, scratch)]
    for path in paths:
        reaped += release_checkout(ports, path)
    for name in scratch:
        ports.workspace.remove_checkout(name)
    ports.workspace.cleanup(task_id)
    return reaped


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def release_after_merge(
    ports: Ports,
    task_id: str,
    log: Callable[[str], None],
    clock: Callable[[], str] = _now,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """`release_task` for a task the system just merged (GH-820): the removal is housekeeping
    after a merge that happened, so it never fails the merge. A `GitError` is retried once
    after `RELEASE_RETRY_PAUSE_S`; an `OSError` or `SubprocessError` is not. A removal that
    still fails leaves one `worktree_release_lapse` row in the document (a record, read by no
    decision) and one log line. Nothing else is caught: a `TypeError` is a bug."""
    started = time.monotonic()
    cause: Literal["git_failed", "os_error"]
    try:
        try:
            reaped = release_task(ports, task_id)
        except GitError:
            sleep(RELEASE_RETRY_PAUSE_S)
            reaped = release_task(ports, task_id)
    except GitError:
        cause = "git_failed"
    except (OSError, subprocess.SubprocessError):
        cause = "os_error"
    else:
        line = f"agentic: merge: {task_id}: worktrees released in {time.monotonic() - started:.1f}s"
        if reaped_line := summary(reaped):
            line += f"; {reaped_line}"
        log(line)
        return
    lapse: WorktreeReleaseLapse = {"at": clock(), "cause": cause}
    try:
        ports.documents.record_closure(task_id, {"worktree_release_lapse": lapse})
    except (ValueError, OSError) as exc:
        log(f"agentic: merge: {task_id}: worktree release lapsed ({cause}), not recorded: {exc}")
        return
    log(f"agentic: merge: {task_id}: worktree release lapsed ({cause})")


def summary(reaped: tuple[ReapedProcess, ...]) -> str:
    """One phrase for what was reaped, for a comment or a stderr line; empty for nothing."""
    if not reaped:
        return ""
    noun = "process" if len(reaped) == 1 else "processes"
    listed = ", ".join(f"`{item['command']}` ({item['outcome']})" for item in reaped)
    return f"reaped {len(reaped)} {noun}: {listed}"
