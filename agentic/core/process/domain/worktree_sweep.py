"""The worktree sweep's decisions (GH-824): which task a directory under
`.agentic/worktrees` belongs to, which tasks one round looks at, what a round remembers and
what it says only once. Pure: no I/O, no clock; a round is a counter the caller advances by
calling `due`. The application module reads the tracker and the disk and removes."""

from __future__ import annotations

import errno
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

SWEEP_READS_MAX = 10
OPEN_TTL_ROUNDS = 30
HELD_TTL_ROUNDS = 30
SWEEP_WALL_S = 30

Outcome = Literal[
    "open",
    "removed",
    "dirty",
    "held",
    "removal_failed",
    "read_failed",
    "dead_lock_taken",
    "no_space",
]
Fate = Literal["empty", "clean", "dirty"]

# Rounds an outcome is not looked at again; 0 means the next round looks again.
MEMO_TTL_ROUNDS: dict[Outcome, int] = {
    "open": OPEN_TTL_ROUNDS,
    "held": HELD_TTL_ROUNDS,
    "dirty": 30,
    "removal_failed": 30,
    "read_failed": 5,
    "removed": 0,
    "dead_lock_taken": 0,
    "no_space": 1,
}

_TASK_NAME = re.compile(r"^(?:(?:merge|slice)-)?(GH-\d+)(?:\..+)?$")


def task_of(name: str) -> str | None:
    """The task id a directory name belongs to: `GH-N`, `GH-N.<subtask>`, `merge-GH-N` and
    `slice-GH-N`. Any other name (`integration-<hex>`, `perf-*`, `playbook`) is None and is
    kept. The only place that decides it."""
    found = _TASK_NAME.match(name)
    return found.group(1) if found else None


@dataclass(frozen=True)
class SweepMemory:
    """What the sweep carries from one round to the next. `cursor` is an issue NUMBER, never
    a position in a list: a task that disappears between rounds moves nobody."""

    memos: Mapping[str, tuple[int, Outcome]] = field(default_factory=dict)
    cursor: int = 0
    round: int = 0
    said: frozenset[tuple[str, str]] = frozenset()


def _number(task: str) -> int:
    return int(task.removeprefix("GH-"))


def due(names: Mapping[str, list[str]], memory: SweepMemory) -> tuple[list[str], SweepMemory]:
    """The task ids to read this round and the next memory. `names` maps each task id with a
    listed checkout to its names. Ids go by issue number from the one after the cursor,
    wrapping to the smallest; those whose memo is within its TTL are skipped; at most
    `SWEEP_READS_MAX` come back and the cursor becomes the last of them (unchanged when none).
    Memos and said pairs of tasks with no listed checkout are dropped."""
    now = memory.round + 1
    ordered = sorted(names, key=_number)
    after = [t for t in ordered if _number(t) > memory.cursor]
    before = [t for t in ordered if _number(t) <= memory.cursor]
    ready = [t for t in after + before if not _fresh(memory, t, now)]
    ids = ready[:SWEEP_READS_MAX]
    return ids, SweepMemory(
        memos={t: m for t, m in memory.memos.items() if t in names},
        cursor=_number(ids[-1]) if ids else memory.cursor,
        round=now,
        said=frozenset(pair for pair in memory.said if pair[0] in names),
    )


def _fresh(memory: SweepMemory, task: str, now: int) -> bool:
    memo = memory.memos.get(task)
    return memo is not None and now - memo[0] < MEMO_TTL_ROUNDS[memo[1]]


def verdict(state: str) -> bool:
    """Remove only for exactly CLOSED: any other state, or none readable, keeps the task."""
    return state == "CLOSED"


def checkout_fate(is_checkout: bool, committed: bool | None) -> Fate:
    """What a directory under a task is. No `.git` is `empty`: removable, and removing it
    DISCARDS whatever the directory holds, git no longer knows it. A checkout with no change
    to a tracked file is `clean`; one with changes, or whose `is_committed` raised (None), is
    `dirty` and keeps the whole task. Untracked files deliberately do not make a checkout
    dirty (venvs, build artefacts), as `is_committed`'s `--untracked-files=no`."""
    if not is_checkout:
        return "empty"
    return "clean" if committed else "dirty"


def remember(memory: SweepMemory, task: str, outcome: Outcome) -> SweepMemory:
    """The memory with `task`'s outcome of the current round."""
    return replace(memory, memos={**memory.memos, task: (memory.round, outcome)})


def say(memory: SweepMemory, task: str, cause: str) -> tuple[bool, SweepMemory]:
    """Whether to say `cause` for `task`: True only the first time the pair is said."""
    pair = (task, cause)
    if pair in memory.said:
        return False, memory
    return True, replace(memory, said=memory.said | {pair})


def space_outcome(code: int | None) -> Outcome:
    """The outcome of an `OSError` with errno `code` (GH-865): only `ENOSPC` and `EDQUOT` are
    `no_space`, the full disk the whole round stops at; every other one is `removal_failed`."""
    return "no_space" if code in (errno.ENOSPC, errno.EDQUOT) else "removal_failed"


def rewind(memory: SweepMemory, task: str) -> SweepMemory:
    """The memory whose cursor is just before `task`, so the next `due` starts at it."""
    return replace(memory, cursor=_number(task) - 1)
