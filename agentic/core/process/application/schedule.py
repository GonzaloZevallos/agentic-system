"""The technical stage as a loop, not as waves (GH-364).

A subtask starts the moment the rule says it may, a delivery is reviewed the moment it is
delivered, and the system's turn (`integrate_turn`: the merges, the checkouts, the findings
table) runs alone after every termination. Nothing waits for a sibling that has nothing to do
with it.

Two things make that safe, and both live here:

**One folded state.** Branches run at the same time over copies of the state, so each used to
save the document with its own view and the last writer erased its sisters' work. Here the
loop owns the single folded state and `FoldingDocuments` is the only thing that saves the
document while a branch is in flight: `apply(folded, last delta of every branch in flight)`.
A branch's delta is replaced by its next save, never added to it, so the runs and the cost
of a branch that saves five times are counted once.

**Nothing starts during a turn.** The turn is awaited in a thread before the rule is asked
again, so there is at most one turn at a time without a lock, and a checkout is never created
beside a merge.

The loop knows no graph: the branch and the turn arrive as functions, and so does the rule that
says what starts (`rules.next_to_start`, passed by the node), so this module decides nothing
about readiness.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain.task import TaskState, apply, delta
from .ports import Ports, TaskDocuments

BranchRunner = Callable[[TaskState, Ports], Coroutine[object, object, TaskState]]
"""Runs one subtask's pipeline over the branch's copy of the state and returns the state it
ended holding (not what it added: the loop takes the delta)."""

Turn = Callable[[TaskState, Configuration, Ports], TaskState]
"""The system's turn over the folded state; returns the patch to fold in."""

NextToStart = Callable[[TaskState, frozenset[str], int], Sequence[str]]
"""`rules.next_to_start`: the subtasks to start given the ones in flight and the bound."""


class _Delegating:
    """The `TaskDocuments` methods that are not a save, handed to the wrapped port."""

    def __init__(self, inner: TaskDocuments) -> None:
        self._inner = inner

    def path_for(self, task_id: str) -> Path:
        return self._inner.path_for(task_id)

    def ids(self) -> list[str]:
        return self._inner.ids()

    def ids_touched_since(self, since: datetime) -> list[str]:
        return self._inner.ids_touched_since(since)

    def load(self, task_id: str) -> dict[str, object] | None:
        return self._inner.load(task_id)

    def text(self, task_id: str) -> str | None:
        return self._inner.text(task_id)

    def write_into(self, task_id: str, root: Path) -> Path | None:
        return self._inner.write_into(task_id, root)

    def record_closure(self, task_id: str, values: Mapping[str, object]) -> Path:
        return self._inner.record_closure(task_id, values)


@dataclass
class _InFlight:
    base: TaskState
    last_delta: TaskState | None = None


class FoldingDocuments(_Delegating):
    """The `TaskDocuments` of one loop run: holds the folded state and, per branch in flight,
    the state it started from and the delta of its latest save. Whoever saves, the document
    written is `apply(folded, *deltas in flight)`, so a branch that saves a stale view of the
    state cannot take its sisters' work out of the document.

    Saves come from the event loop (branches) and from the turn's thread, so every read and
    write of its own state is under one lock."""

    def __init__(self, inner: TaskDocuments, folded: TaskState) -> None:
        super().__init__(inner)
        self._folded = folded
        self._in_flight: dict[str, _InFlight] = {}
        self._lock = threading.Lock()

    @property
    def folded(self) -> TaskState:
        return self._folded

    def branch(self, branch_id: str, base: TaskState) -> TaskDocuments:
        """The documents one branch saves through; `base` is the state it starts from."""
        with self._lock:
            self._in_flight[branch_id] = _InFlight(base)
        return _BranchDocuments(self._inner, self, branch_id)

    def save(self, state: TaskState) -> Path:
        """The turn's save: `state` is the folded state it built, written with what the
        branches in flight have done on top of it. It does not become the folded state; the
        loop folds the turn's patch itself."""
        with self._lock:
            return self._inner.save(self._with_in_flight(state))

    def complete(self, branch_id: str, final: TaskState) -> None:
        """A branch ended holding `final`: its delta is folded in whole and it is no longer
        in flight."""
        with self._lock:
            entry = self._in_flight.pop(branch_id)
            self._folded = apply(self._folded, delta(entry.base, final))

    def fold(self, patch: TaskState) -> None:
        """Fold the turn's patch into the folded state. The document is written again when
        branches are in flight: one of them may have saved between the turn's own save and
        now, and that save did not know the patch."""
        with self._lock:
            self._folded = apply(self._folded, patch)
            if self._in_flight:
                self._inner.save(self._with_in_flight(self._folded))

    def save_branch(self, branch_id: str, state: TaskState) -> Path:
        with self._lock:
            entry = self._in_flight[branch_id]
            entry.last_delta = delta(entry.base, state)
            return self._inner.save(self._with_in_flight(self._folded))

    def _with_in_flight(self, state: TaskState) -> TaskState:
        for entry in self._in_flight.values():
            if entry.last_delta is not None:
                state = apply(state, entry.last_delta)
        return state


class _BranchDocuments(_Delegating):
    """What one branch sees as its documents."""

    def __init__(self, inner: TaskDocuments, folding: FoldingDocuments, branch_id: str) -> None:
        super().__init__(inner)
        self._folding = folding
        self._branch_id = branch_id

    def save(self, state: TaskState) -> Path:
        return self._folding.save_branch(self._branch_id, state)


async def run_subtasks(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    run_branch: BranchRunner,
    turn: Turn,
    limit: int,
    next_to_start: NextToStart,
) -> TaskState:
    """Run the subtasks of `state` until none is left to start or running.

    `next_to_start` is asked with the folded state, the ids in flight and `limit`; each id it
    names starts as its own asyncio task over a copy of the folded state with `subtask_id`
    set. Each branch that finishes has its delta folded in and is followed by one turn, alone,
    in a thread; only after them is the rule asked again, so a returned subtask restarts after
    the turn that prepared it.

    Once `escalated` is in the folded state nothing new starts and what runs is drained, never
    cancelled. A branch (or the turn) that raises stops the starts, lets the others drain, and
    is raised afterwards. Returns what the parent graph must fold: `delta(state, folded)`.
    """
    documents = FoldingDocuments(ports.documents, state)
    turn_ports = replace(ports, documents=documents)
    running: dict[str, asyncio.Task[TaskState]] = {}
    failure: BaseException | None = None
    try:
        while True:
            if failure is None and not documents.folded.get("escalated"):
                for subtask_id in next_to_start(documents.folded, frozenset(running), limit):
                    base: TaskState = {**documents.folded, "subtask_id": subtask_id}
                    branch_ports = replace(ports, documents=documents.branch(subtask_id, base))
                    running[subtask_id] = asyncio.create_task(run_branch(base, branch_ports))
            if not running:
                break
            await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            finished = [subtask_id for subtask_id, task in running.items() if task.done()]
            # One turn per completion, even when several finished in this wake-up: each
            # termination is folded and followed by its own turn.
            for subtask_id in finished:
                try:
                    documents.complete(subtask_id, running.pop(subtask_id).result())
                except (Exception, asyncio.CancelledError) as error:
                    failure = failure or error
                if failure is None:
                    try:
                        patch = await asyncio.to_thread(turn, documents.folded, cfg, turn_ports)
                    except Exception as error:
                        failure = error
                    else:
                        documents.fold(patch)
    finally:
        for task in running.values():
            task.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
    if failure is not None:
        raise failure
    return delta(state, documents.folded)
