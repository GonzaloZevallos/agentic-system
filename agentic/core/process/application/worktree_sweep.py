"""The worktree sweep (GH-824): removes the checkouts under `.agentic/worktrees` of tasks whose
issue is CLOSED. The decisions are `domain/worktree_sweep.py`'s; this reads the tracker and
the disk and removes, and nothing in this part calls it (the poller does, in the next).

Per task, under the task's own run lock (`merge` and `land` hold it too, so a live one is
`LockHeld` and the task is left): every name is classified first, and one dirty checkout keeps
the whole task. Then the checkouts git no longer knows (no `.git`) go first, which is what
keeps `Workspace.cleanup` from raising on them; `release_task` runs unchanged; whatever is
still on disk is removed last. Branches are never deleted."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..domain.worktree_sweep import (
    SWEEP_WALL_S,
    Fate,
    SweepMemory,
    checkout_fate,
    due,
    remember,
    say,
    task_of,
    verdict,
)
from .ports import GitError, LockHeld, Ports, RunLock, TrackerCallFailed
from .release import release_checkout, release_task


@dataclass
class SweepCarry:
    """What the poller keeps between rounds: the latest memory, nothing else."""

    memory: SweepMemory = field(default_factory=SweepMemory)


def _listed(ports: Ports) -> dict[str, list[str]]:
    """The names on disk by task, from ONE listing: a prefix would take GH-8 for GH-82."""
    tasks: dict[str, list[str]] = {}
    for name in ports.workspace.checkout_names(""):
        if (task := task_of(name)) is not None:
            tasks.setdefault(task, []).append(name)
    return tasks


def sweep(
    ports: Ports, lock: RunLock, memory: SweepMemory, monotonic: Callable[[], float]
) -> tuple[list[str], SweepMemory]:
    """One round: at most `SWEEP_READS_MAX` tasks, none started after `SWEEP_WALL_S`. Returns
    the lines to say (each cause once per process) and the next memory. A `TrackerCallFailed`
    stops the reads of the round; any exception outside the ones a task's own try names
    propagates."""
    started = monotonic()
    names = _listed(ports)
    ids, memory = due(names, memory)
    lines: list[str] = []
    removed = 0
    for task in ids:
        if monotonic() - started >= SWEEP_WALL_S:
            break
        try:
            gone, memory = _task(ports, lock, task, names[task], memory, lines)
            removed += len(names[task]) if gone else 0
        except TrackerCallFailed:
            memory = remember(memory, task, "read_failed")
            break
        except LockHeld:
            memory = remember(memory, task, "held")
        except (GitError, OSError, ValueError) as exc:
            failed = remember(memory, task, "removal_failed")
            memory = _said(failed, lines, task, "removal_failed", f"removal failed: {exc}")
    if removed:
        lines.append(f"agentic: sweep: removed {removed} worktrees in {monotonic() - started:.1f}s")
    return lines, memory


def _said(memory: SweepMemory, lines: list[str], task: str, cause: str, text: str) -> SweepMemory:
    """`text` as a line, only the first time `cause` is said for `task`."""
    first, memory = say(memory, task, cause)
    if first:
        lines.append(f"agentic: sweep: {task}: {text}")
    return memory


def _task(
    ports: Ports,
    lock: RunLock,
    task: str,
    names: list[str],
    memory: SweepMemory,
    lines: list[str],
) -> tuple[bool, SweepMemory]:
    """Sweeps one task; whether it removed it, and the memory. Raises what `sweep` names."""
    try:
        state = ports.tracker.issue_state(int(task.removeprefix("GH-"))).state
    except TrackerCallFailed:
        raise
    except ValueError:
        return False, remember(memory, task, "read_failed")
    if not verdict(state):
        return False, remember(memory, task, "open")
    dead = lock.acquire(task, "sweep")
    try:
        if dead is not None:
            memory = _said(
                memory,
                lines,
                task,
                "dead_lock_taken",
                f"took over the lock left by pid {dead.pid} on {dead.host} "
                f"({dead.started_at}), no longer running",
            )
        fates = {name: _fate(ports, name) for name in names}
        if bad := {name: fate for name, fate in fates.items() if fate[0] == "dirty"}:
            unreadable = any(fate[1] for fate in bad.values())
            kept = ", ".join(bad)
            text = (
                f"kept, cannot read the state of {kept}"
                if unreadable
                else f"kept, {kept} has changes"
            )
            memory = _said(
                remember(memory, task, "dirty"),
                lines,
                task,
                "unreadable" if unreadable else "dirty",
                text,
            )
            return False, memory
        _remove(ports, task, names, [n for n, fate in fates.items() if fate[0] == "empty"])
        return True, remember(memory, task, "removed")
    finally:
        lock.release(task)


def _fate(ports: Ports, name: str) -> tuple[Fate, bool]:
    """The fate of a checkout and whether `is_committed` raised for it."""
    path = ports.workspace.checkout_path(name)
    if not ports.workspace.is_checkout(path):
        return checkout_fate(False, None), False
    try:
        return checkout_fate(True, ports.workspace.is_committed(path)), False
    except (GitError, OSError):
        return checkout_fate(True, None), True


def _remove(ports: Ports, task: str, names: list[str], empty: list[str]) -> None:
    for name in empty:
        release_checkout(ports, ports.workspace.checkout_path(name))
        ports.workspace.remove_checkout(name)
    release_task(ports, task)
    for name in ports.workspace.checkout_names(""):
        if name in names:
            ports.workspace.remove_checkout(name)
