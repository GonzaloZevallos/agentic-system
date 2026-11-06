"""The shadow review of the `single` arm (GH-473, ADR "Revisión única con sombra"): while the
integration's one fresh review runs, the review a branch always got runs beside it, once per
approved branch, and only its findings and cost are kept. It measures what the single review
misses; it blocks nothing.

What makes it a shadow, in one place each:

- **It writes `shadow_reviews`, `shadow_wait_s`, its own section and its cost.** Never
  `findings`, `settled_findings`, `changes_requested`, `runs`, `failures`, a status or
  `escalated`: `run_one` keeps the findings and the cost of what `run_reviewer` returns and
  drops the rest of its patch. It never calls `finish`.
- **Its failure is a record, not a failure.** `run_one` runs the reviewer with
  `role_retries=0`, so a failed attempt is never retried at the cost of the task, and an
  `Escalating` role, a setup error, a missing checkout or any exception becomes
  `unavailable`; a shadow still running when the grace ends is `late`.
- **It never takes the run's role semaphore.** Each shadow has its own `RunLimits`, and how
  many run at once is `rules.shadow_slots`, one role slot short of the run's.
- **It builds the reviewer through `run_reviewer`**, the function `review()` calls, as a first
  review of the branch's delivered head: the same sections and arguments, no second list.

The records travel in the node's patch; a process that dies while they run pays for them again
on the resume, and a subtask that already has a record is not relaunched."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import anyio

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.task import (
    INTEGRATION,
    FindingRecord,
    ShadowCause,
    ShadowRecord,
    ShadowStatus,
    TaskState,
)
from . import review as review_module
from .checkouts import SetupFailed, detached_checkout_in_thread, task_base
from .limits import RunLimits
from .ports import Checkout, GitError, Ports


def shadow_section(subtask_id: str) -> str:
    """Where a shadow's own text goes; no role reads it."""
    return f"Tech Lead: shadow review of {subtask_id}"


def due(state: TaskState, subtask_id: str, cfg: Configuration) -> bool:
    """Whether this review node is the one that runs the shadows: `single` mode, the reviewed
    unit (the integration, or the lone subtask), on its first pass."""
    return (
        cfg.review_mode == "single"
        and subtask_id == rules.reviewed_unit_name(state)
        and not state["subtasks"][subtask_id].get("reviewed_head")
    )


@dataclass
class ShadowOutcome:
    """What the shadows add to the node's patch: the records, the seconds the node waited past
    the single review, the sections and the cost."""

    records: list[ShadowRecord] = field(default_factory=list)
    wait_s: float = 0.0
    sections: dict[str, str] = field(default_factory=dict)
    cost_usd: float = 0.0

    def patch(self) -> TaskState:
        patch: TaskState = {}
        if self.records:
            patch["shadow_reviews"] = self.records
            patch["shadow_wait_s"] = round(self.wait_s, 4)
        if self.sections:
            patch["sections"] = self.sections
        if self.cost_usd:
            patch["cost_usd"] = self.cost_usd
        return patch


def _record(
    subtask_id: str,
    head: str,
    status: ShadowStatus,
    cause: ShadowCause | None,
    *,
    findings: list[FindingRecord] | None = None,
    cost_usd: float = 0.0,
    changed_lines: int | None = None,
) -> ShadowRecord:
    return {
        "subtask": subtask_id,
        "head": head,
        "status": status,
        "cause": cause,
        "findings": findings or [],
        "cost_usd": cost_usd,
        "changed_lines": changed_lines,
    }


def _as_first_review(state: TaskState, subtask_id: str) -> TaskState:
    """`state` as the subtask's first review read it: its own `reviewed_head` and the system's
    one-line approval gone, so `run_reviewer` diffs the whole branch with the brief as spec."""
    subtasks = state["subtasks"]
    own = subtasks[subtask_id].copy()
    own.pop("reviewed_head", None)
    section = rules.review_section(subtask_id)
    sections = {k: v for k, v in state.get("sections", {}).items() if k != section}
    return {
        **state,
        "subtask_id": subtask_id,
        "subtasks": {**subtasks, subtask_id: own},
        "sections": sections,
    }


async def run_one(
    state: TaskState,
    subtask_id: str,
    cfg: Configuration,
    ports: Ports,
    checkout: Checkout,
    limits: RunLimits,
    *,
    base: str,
    head: str,
    changed_lines: int | None,
) -> tuple[ShadowRecord, tuple[str, str] | None]:
    """One shadow review of `subtask_id` in `checkout`: the full reviewer, with the Developer's
    section, no CI, the one attempt (`role_retries=0`). The record, and the section's `(name,
    text)` when it ran. All that is kept of `run_reviewer`'s patch is the findings and the cost:
    an `Escalating` role is `unavailable` (`cap` for a cap, `failure` otherwise) with the cost it
    paid, and so is any exception."""
    shadow_ports = replace(ports, limits=limits)
    shadow_cfg = cfg.model_copy(update={"role_retries": 0})
    cause: ShadowCause = "failure"
    try:
        output, patch = await review_module.run_reviewer(
            _as_first_review(state, subtask_id),
            subtask_id,
            shadow_cfg,
            shadow_ports,
            checkout,
            base=base,
            mode="full",
            with_developer=True,
            ci_names=(),
        )
    except Exception:  # a shadow never fails the task, whatever broke
        output = None
    else:
        if output is None and patch.get("escalation_class") in ("cap", "estimate"):
            cause = "cap"
    if output is None:
        unavailable = _record(
            subtask_id,
            head,
            "unavailable",
            cause,
            cost_usd=limits.spent_usd,
            changed_lines=changed_lines,
        )
        return unavailable, None
    findings, _ = rules.lower_landing_blocks(output.data["findings"])
    findings, _ = rules.lower_unbacked_blocks(findings)
    record = _record(
        subtask_id,
        head,
        "ran",
        None,
        findings=rules.finding_records(subtask_id, 1, findings),
        cost_usd=output.cost_usd,
        changed_lines=changed_lines,
    )
    return record, (shadow_section(subtask_id), output.text)


async def run_shadows[T](
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    main: Callable[[], Awaitable[T]],
) -> tuple[T, ShadowOutcome]:
    """Runs `main` (the single review) and, beside it, one shadow per approved branch that has
    no record yet; the result of `main` and what the shadows add to the node's patch.

    Started before `main`, at most `rules.shadow_slots` at once, smallest diff first. After
    `main` returns the node waits at most `cfg.shadow_grace_s` for them; what is still running
    is cancelled and recorded `late` with the cost seen so far. A task with a lone subtask
    records `skipped`/`single_unit`; no slots, or a task cap the shadows could reach, records
    `skipped`/`cap`. Nothing here raises for a shadow."""
    subtasks = state["subtasks"]
    held = {r["subtask"] for r in state.get("shadow_reviews") or []}
    if len(subtasks) == 1:
        lone = next(iter(subtasks))
        skipped = [] if lone in held else [_record(lone, "", "skipped", "single_unit")]
        return await main(), ShadowOutcome(records=skipped)
    pending = [
        sid
        for sid, sub in subtasks.items()
        if sid != INTEGRATION and sid not in held and sub.get("reviewed_head")
    ]
    if not pending:
        return await main(), ShadowOutcome()
    slots = rules.shadow_slots(cfg.shadow_parallel, cfg.max_concurrent_roles)
    cap = rules.task_cap(cfg.task_max_budget_usd, cfg.task_budget_usd)
    if slots == 0 or rules.task_spent(state) + slots * cfg.max_budget_usd >= cap:
        capped = [_record(sid, subtasks[sid]["reviewed_head"], "skipped", "cap") for sid in pending]
        return await main(), ShadowOutcome(records=capped)
    return await _beside(state, cfg, ports, main, pending, slots)


async def _beside[T](
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    main: Callable[[], Awaitable[T]],
    pending: list[str],
    slots: int,
) -> tuple[T, ShadowOutcome]:
    subtasks = state["subtasks"]
    fallback = task_base(state, ports)
    bases = {sid: subtasks[sid].get("base") or fallback for sid in pending}
    sizes = {sid: _lines(ports, bases[sid], subtasks[sid]["reviewed_head"]) for sid in pending}
    order = deque(sorted(pending, key=lambda sid: (sizes[sid] is None, sizes[sid] or 0, sid)))
    records: dict[str, ShadowRecord] = {}
    sections: dict[str, str] = {}
    limits = {sid: RunLimits().sized(slots) for sid in pending}
    alive = min(slots, len(order))
    all_done = anyio.Event()

    async def one(sid: str) -> None:
        head = subtasks[sid]["reviewed_head"]
        name = f"{state['id']}.shadow-{sid}"
        try:
            records[sid] = await _shadow(
                state, sid, cfg, ports, name, bases[sid], head, limits[sid], sizes[sid], sections
            )
        except Exception:  # never reaches the group: a shadow cannot fail the review
            records[sid] = _record(
                sid,
                head,
                "unavailable",
                "failure",
                cost_usd=limits[sid].spent_usd,
                changed_lines=sizes[sid],
            )
        except BaseException:
            # Cancelled at the end of the grace: the cost seen so far, no findings.
            records[sid] = _record(
                sid, head, "late", "late", cost_usd=limits[sid].spent_usd, changed_lines=sizes[sid]
            )
            raise
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(ports.workspace.remove_checkout, name)

    async def worker() -> None:
        nonlocal alive
        try:
            while order:
                await one(order.popleft())
        finally:
            alive -= 1
            if alive == 0:
                all_done.set()

    held_result: list[T] = []
    failure: BaseException | None = None
    waited = 0.0
    async with anyio.create_task_group() as group:
        for _ in range(alive):
            group.start_soon(worker)
        try:
            held_result.append(await main())
        except BaseException as exc:
            # The shadows stop with the review; the error is raised once the group is closed.
            failure = exc
            group.cancel_scope.cancel()
        else:
            began = time.monotonic()
            with anyio.move_on_after(cfg.shadow_grace_s) as grace:
                await all_done.wait()
            waited = time.monotonic() - began
            if grace.cancelled_caught:
                group.cancel_scope.cancel()
    if failure is not None:
        raise failure
    for sid in pending:
        if sid not in records:  # never started before the grace ended
            head = subtasks[sid]["reviewed_head"]
            records[sid] = _record(sid, head, "late", "late", changed_lines=sizes[sid])
    ordered = [records[sid] for sid in sorted(pending)]
    cost = sum(r["cost_usd"] for r in ordered)
    return held_result[0], ShadowOutcome(ordered, waited, sections, cost)


def _lines(ports: Ports, base: str, head: str) -> int | None:
    try:
        return ports.workspace.changed_lines(base, head)
    except GitError:
        return None


async def _shadow(
    state: TaskState,
    sid: str,
    cfg: Configuration,
    ports: Ports,
    name: str,
    base: str,
    head: str,
    limits: RunLimits,
    changed_lines: int | None,
    sections: dict[str, str],
) -> ShadowRecord:
    """One shadow, from the room check to its record: the checkout is the caller's to remove."""
    if ports.free_space is not None:
        reading = ports.free_space.read(Path(cfg.product_dir))
        if rules.space_holds({rules.PRODUCT_VOLUME: reading}, cfg.scratch_floor_bytes):
            return _record(sid, head, "unavailable", "setup", changed_lines=changed_lines)
    try:
        checkout = await detached_checkout_in_thread(state, name, head, base, cfg, ports)
    except SetupFailed:
        return _record(sid, head, "unavailable", "setup", changed_lines=changed_lines)
    except GitError:
        return _record(sid, head, "unavailable", "no_checkout", changed_lines=changed_lines)
    record, section = await run_one(
        state, sid, cfg, ports, checkout, limits, base=base, head=head, changed_lines=changed_lines
    )
    if section is not None:
        sections[section[0]] = section[1]
    return record
