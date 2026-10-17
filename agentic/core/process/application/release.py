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

from ..domain.reaping import ReapedProcess
from .ports import Ports


def release_processes(ports: Ports, task_id: str, run: int) -> tuple[ReapedProcess, ...]:
    """What the dead run `run` of `task_id` left alive, stopped."""
    return ports.reaper.reap_run(task_id, run)


def release_task(ports: Ports, task_id: str) -> tuple[ReapedProcess, ...]:
    """Stops every marked process of the task, then removes its worktrees. Returns what
    was reaped; a `GitError` from the removal propagates unchanged."""
    reaped = ports.reaper.reap_task(task_id)
    ports.workspace.cleanup(task_id)
    return reaped


def summary(reaped: tuple[ReapedProcess, ...]) -> str:
    """One phrase for what was reaped, for a comment or a stderr line; empty for nothing."""
    if not reaped:
        return ""
    noun = "process" if len(reaped) == 1 else "processes"
    listed = ", ".join(f"`{item['command']}` ({item['outcome']})" for item in reaped)
    return f"reaped {len(reaped)} {noun}: {listed}"
