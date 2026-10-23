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

import sys
from pathlib import Path

from ..domain import reaping
from ..domain.reaping import ReapedProcess
from .ports import Ports

# The scratch checkouts a merge and a landing slice make beside a task's worktrees (GH-178).
SCRATCH_PREFIXES = ("merge", "slice")


def release_processes(ports: Ports, task_id: str, run: int) -> tuple[ReapedProcess, ...]:
    """What the dead run `run` of `task_id` left alive, stopped: its roles' scope and the
    checks it ran (GH-315), which carry their own mark."""
    return ports.reaper.reap_run(task_id, run) + ports.reaper.reap_run(reaping.CHECK_TASK, run)


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


def summary(reaped: tuple[ReapedProcess, ...]) -> str:
    """One phrase for what was reaped, for a comment or a stderr line; empty for nothing."""
    if not reaped:
        return ""
    noun = "process" if len(reaped) == 1 else "processes"
    listed = ", ".join(f"`{item['command']}` ({item['outcome']})" for item in reaped)
    return f"reaped {len(reaped)} {noun}: {listed}"
