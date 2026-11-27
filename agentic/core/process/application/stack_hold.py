"""The hold of a stacked child in the poller (GH-899, ADR-0103).

A queued child whose document names a `stack_parent` waits while `stacking.pause_cause` of its
parent chain says so. `compute` reads that once per round (the memo shares the parent's reads
among siblings) and answers the map the poller filters by; `write_episodes` tells the child's
issue once per episode and keeps the `stack_pauses` rows. Both are the poller's alone: the manual
verbs and `assess_merge` never read this module, and the gate on merging stays `stacking.refusal`.

A child that has no task branch yet is read a second time (`_unstarted`, GH-1163): a parent that
left Ready, or whose branch no longer carries the recorded head, holds it as `parent_not_ready`
so that no retake is spent on a start that waits. The branch and the parent's facts are read only
after `pause_cause` answered `none` for a parent that has not landed.

An episode is the stretch a child waits on `(parent, cause)`. It opens with one `Tracker.note`
(the `pause_note` text and a marker that makes a retry idempotent) and one open row, and ends with
`resumed_at` stamped when the cause reads `none`. The child is written to only when
`free_writer`: no live run of the product on it and no lock that reads alive, the same predicate
at opening and at closing, so the poller never writes a document a run holds. A note that failed
is recorded in `tracker_failures` and not retried: its row is written anyway. `unreadable` writes
nothing but one line a round. A round that is dry or held for space reads no comments and writes
nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentic.shared.configuration import Configuration

from ..domain import rules, stacking
from ..domain.stacking import NotedCause, PauseCause
from ..domain.task import (
    StackParent,
    StackPause,
    TaskState,
    TrackerFailureRecord,
    add_stack_pauses,
    add_tracker_failures,
)
from . import stack_facts
from .ports import GitError, Ports, RunLock, RunRegistration, Upstream


@dataclass(frozen=True)
class StackHold:
    """What one round knows of its stacked children. `held` maps an issue to the cause that
    holds it (any cause but `none`, `unreadable` included); `causes` and `parents` cover every
    child that was read, `none` included, and `parents` is the child's direct parent. `busy` is
    the task ids with a live run of this product. `lines` are the poller lines `compute` says."""

    held: dict[int, PauseCause] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    causes: dict[int, PauseCause] = field(default_factory=dict)
    parents: dict[int, int] = field(default_factory=dict)
    busy: frozenset[str] = frozenset()


def compute(
    ports: Ports,
    cfg: Configuration,
    lock: RunLock,
    queued_all: Mapping[int, TaskState | None],
    upstream: Callable[[], Upstream],
    memo: stack_facts.StackMemo,
    registry_live: Collection[RunRegistration],
) -> StackHold:
    """The cause of every queued child whose document has a readable `stack_parent`. A round
    with no such child makes no read, `upstream` included; a record that cannot be read names no
    parent to wait for and is left to `stacking.refusal`."""
    product = ports.stack.product()
    busy = frozenset(r.task_id for r in registry_live if r.product == product)
    children = {
        number: facts.record
        for number, raw in queued_all.items()
        if raw is not None
        and (facts := stacking.facts_of(raw)) is not None
        and facts.record is not None
    }
    hold = StackHold(busy=busy)
    if not children:
        return hold
    tip = upstream().sha or "HEAD"
    for number, record in children.items():
        chain = stack_facts.parent_chain(ports, cfg, lock, record, tip, memo)
        cause = stacking.pause_cause(chain)
        if cause == "none" and chain[0].in_upstream is False:
            cause = _unstarted(ports, cfg, number, record, chain[0], memo)
        hold.causes[number] = cause
        hold.parents[number] = record["issue"]
        if cause == "none":
            continue
        hold.held[number] = cause
        if cause == "unreadable":
            hold.lines.append(f"agentic: poll: #{number} held (stacked, parent unreadable)")
    return hold


def _unstarted(
    ports: Ports,
    cfg: Configuration,
    number: int,
    record: StackParent,
    link: stacking.ParentLink,
    memo: stack_facts.StackMemo,
) -> PauseCause:
    """The second step (GH-1163): the chain says nothing holds the child, yet its parent has not
    landed. A child with a task branch is a run's to judge, and makes no remote read. One without
    would start at the recorded head, so the cause is what `start_ref` says of the parent's facts
    at that head, read through the window's memo. A failed read is `unreadable`, never `none`."""
    try:
        if ports.workspace.branch_head(rules.task_branch(ports.inbox.task_id(number))) is not None:
            return "none"
        facts = stack_facts.parent_facts(
            cfg,
            ports,
            record["issue"],
            head=record["head"],
            paused=bool(link.waits_on_human or link.stopped),
            memo=memo,
        )
    except (GitError, OSError, ValueError):
        return "unreadable"
    return stacking.unstarted_cause(stacking.start_ref(record["issue"], facts))


def free_writer(task_id: str, busy: Collection[str], lock: RunLock) -> bool:
    """Whether the poller may write the task's document and issue: no live run of the product
    on it (`busy`, from `registry.live()`) and no lock `RunLock.refusal` reads alive, the one
    liveness rule `acquire` and the retakes use. A dead lock, or one of another boot, is free; a
    lock of another host reads alive. The lock is only read, never taken. A lock that cannot be
    read is not free."""
    if task_id in busy:
        return False
    try:
        return lock.refusal(task_id) is None
    except OSError:
        return False


def marker(parent: int, cause: NotedCause, k: int) -> str:
    """The comment that says episode `k` of `(parent, cause)` was already told."""
    return f"<!-- agentic-stack-pause: {parent} {cause} {k} -->"


def _rows(raw: Mapping[str, object] | None) -> list[StackPause]:
    """The well-formed `stack_pauses` rows of a document, in order; any other entry is left out."""
    stored = (raw or {}).get("stack_pauses")
    rows: list[StackPause] = []
    for entry in stored if isinstance(stored, list) else []:
        if not isinstance(entry, dict):
            continue
        parent, cause, at = entry.get("parent"), entry.get("cause"), entry.get("at")
        resumed = entry.get("resumed_at")
        if (
            isinstance(parent, int)
            and isinstance(cause, str)
            and isinstance(at, str)
            and (resumed is None or isinstance(resumed, str))
        ):
            rows.append(StackPause(parent=parent, cause=cause, at=at, resumed_at=resumed))
    return rows


def _sealed(rows: list[StackPause], stamp: str, keep: tuple[int, str] | None) -> list[StackPause]:
    """The open rows other than `keep`, each with `resumed_at` set and its `at` untouched."""
    return [
        StackPause(parent=r["parent"], cause=r["cause"], at=r["at"], resumed_at=stamp)
        for r in rows
        if r["resumed_at"] is None and (r["parent"], r["cause"]) != keep
    ]


def _write(
    ports: Ports,
    task_id: str,
    new_rows: list[StackPause],
    failure: TrackerFailureRecord | None,
) -> None:
    """One `record_closure` over a fresh load: the rows through `add_stack_pauses`, the failed
    note through `add_tracker_failures`; a row written since the round began is not lost."""
    stored = ports.documents.load(task_id) or {}
    values: dict[str, object] = {"stack_pauses": add_stack_pauses(_rows(stored), new_rows)}
    if failure is not None:
        failures = stored.get("tracker_failures")
        values["tracker_failures"] = add_tracker_failures(
            failures if isinstance(failures, list) else [], [failure]
        )
    ports.documents.record_closure(task_id, values)


def _open_episode(
    ports: Ports,
    task_id: str,
    number: int,
    parent: int,
    cause: NotedCause,
    rows: list[StackPause],
    stamp: str,
) -> str | None:
    """The first note and row of `(parent, cause)` for this child, or the line that says why
    nothing was written."""
    k = sum(1 for r in rows if (r["parent"], r["cause"]) == (parent, cause)) + 1
    tag = marker(parent, cause, k)
    try:
        comments = ports.tracker.timestamped_comments(task_id)
    except (ValueError, OSError):
        return f"agentic: poll: #{number} stack pause note unread"
    failure = None
    if not any(tag in comment.body for comment in comments):
        failed = ports.tracker.note(task_id, stacking.pause_note(parent, cause) + "\n" + tag)
        failure = failed.record() if failed is not None else None
    row = StackPause(parent=parent, cause=cause, at=stamp, resumed_at=None)
    try:
        _write(ports, task_id, [*_sealed(rows, stamp, (parent, cause)), row], failure)
    except (ValueError, OSError):
        return f"agentic: poll: #{number} stack pause not recorded"
    return None


def write_episodes(
    ports: Ports,
    lock: RunLock,
    hold: StackHold,
    queued_all: Mapping[int, TaskState | None],
    now: datetime,
    *,
    dry_run: bool,
    space_held: bool,
) -> list[str]:
    """Opens the episode of every held child that has none and seals those whose cause is
    gone, over `queued_all` and the map `compute` made (`parent_chain` is not read again).
    Nothing is read or written in a dry round or one held for space. A child with a live run or
    lock is skipped with a line. Returns the poller lines."""
    if dry_run or space_held:
        return []
    stamp = now.astimezone(UTC).isoformat()
    lines: list[str] = []
    for number, raw in queued_all.items():
        cause = hold.causes.get(number)
        if raw is None or cause is None:
            continue
        rows = _rows(raw)
        task_id = ports.inbox.task_id(number)
        match cause:
            case "none":
                gone = _sealed(rows, stamp, None)
                if gone and free_writer(task_id, hold.busy, lock):
                    try:
                        _write(ports, task_id, gone, None)
                    except (ValueError, OSError):
                        lines.append(f"agentic: poll: #{number} stack pause not recorded")
            case "parent_escalated" | "parent_stopped" | "parent_abandoned" | "parent_not_ready":
                parent = hold.parents[number]
                if any(
                    (r["parent"], r["cause"]) == (parent, cause) and r["resumed_at"] is None
                    for r in rows
                ):
                    continue
                if not free_writer(task_id, hold.busy, lock):
                    lines.append(f"agentic: poll: #{number} held (stacked, at work)")
                    continue
                said = _open_episode(ports, task_id, number, parent, cause, rows, stamp)
                if said is not None:
                    lines.append(said)
            case "unreadable":
                pass
    return lines
