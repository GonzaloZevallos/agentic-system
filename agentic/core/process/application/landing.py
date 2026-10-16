"""`agentic land --issue N` (GH-239, ADR-0048): what the system does with an approved pull
request that `rules.auto_merge_decision` refused to merge, instead of leaving it for a
human.

The step is `domain.landing.landing_step`'s; this module only carries it out in the order
that keeps it safe to run twice: take the task's run lock, decide, read the pull request
again, and only then write `(head, step)` into the document's `landing` list -- before the
dispatch, so a crash after the write never repeats the step and a second pass finds the
key. Nothing is written when the head moved since the decision."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, assert_never

from agentic.shared.configuration import Configuration

from ..domain import landing as landing_rules
from ..domain.landing import LandingStepKind
from ..domain.task import TaskState
from . import landing_fix, landing_plan, landing_sync
from .landing_plan import LandingContext
from .poll import StackUnreadable, assess_merge
from .ports import Launcher, Ports, RunLock, Upstream


@dataclass(frozen=True)
class LandResult:
    """`dispatched`: the step was recorded and handed to its executor; `skipped`: nothing
    was written, `reason` says why (no step, not built, already taken, head moved...);
    `stopped`: nothing could be decided, `reason` says what failed."""

    kind: Literal["dispatched", "skipped", "stopped"]
    reason: str | None = None
    step: LandingStepKind | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def land(
    task_id: str,
    cfg: Configuration,
    ports: Ports,
    lock: RunLock,
    upstream: Upstream,
    eval_set: frozenset[int] | None,
    log: Callable[[str], None],
    *,
    clock: Callable[[], str] = _now,
    live: frozenset[LandingStepKind] = landing_plan.LIVE_STEPS,
    resume: Callable[[int], int | None] | None = None,
    launcher: Launcher | None = None,
    argv0: str = "agentic",
    role_span: landing_plan.RoleSpanOpener | None = None,
) -> LandResult:
    """Takes `task_id`'s run lock (`LockHeld` if another process holds it) and releases it
    on the way out, whatever happens."""
    lock.acquire(task_id, "land")
    try:
        return _land(
            task_id,
            cfg,
            ports,
            upstream,
            eval_set,
            log,
            clock,
            live,
            resume,
            launcher,
            argv0,
            role_span,
        )
    finally:
        lock.release(task_id)


def _land(
    task_id: str,
    cfg: Configuration,
    ports: Ports,
    upstream: Upstream,
    eval_set: frozenset[int] | None,
    log: Callable[[str], None],
    clock: Callable[[], str],
    live: frozenset[LandingStepKind],
    resume: Callable[[int], int | None] | None,
    launcher: Launcher | None,
    argv0: str,
    role_span: landing_plan.RoleSpanOpener | None,
) -> LandResult:
    # The port answers a bare `dict`: the document carries keys beyond the state, and every
    # reader below takes it as the state (`.get` for the keys that may be absent).
    loaded: Any = ports.documents.load(task_id)
    number = ports.inbox.issue_of(task_id)
    if loaded is None or not loaded.get("pull_request") or number is None:
        return LandResult("stopped", "no_pull_request")
    raw: TaskState = loaded
    reference = loaded["pull_request"]
    recovered = _recover(task_id, raw, str(reference), ports, log, clock, resume)
    if recovered is not None:
        return recovered
    master = upstream.sha
    if master is None:
        return LandResult("stopped", "no_upstream")
    try:
        assessed = assess_merge(ports, task_id, raw, number, eval_set, master)
    except StackUnreadable as exc:
        return LandResult("stopped", f"stack_unreadable: {exc}")
    if assessed is None:
        return LandResult("skipped", "pr_abandoned")
    decision, facts = assessed.decision, assessed.facts
    if decision.kind != "refuse" or decision.reason is None:
        return LandResult("skipped", f"decision_{decision.kind}")
    head = facts.pr_head
    if head is None:
        return LandResult("skipped", "no_head")
    step = landing_plan.step_for(raw, decision.reason, facts)
    if not landing_plan.takes_action(step.kind, live):
        log(f"agentic: land: {task_id}: {head} {step.kind}: nothing to do")
        return LandResult("skipped", "not_live", step.kind)
    if landing_rules.already_dispatched(landing_plan.entries(raw), head, step.kind):
        log(f"agentic: land: {task_id}: {head} {step.kind}: already taken")
        return LandResult("skipped", "already_dispatched", step.kind)
    # The last look at the pull request before anything is written: a push since the
    # decision makes it stale, and the next round decides again.
    try:
        again = ports.tracker.pull_request(str(reference))
    except (ValueError, OSError) as exc:
        return LandResult("stopped", f"tracker_error: {exc}")
    if again is None or again.head_oid != head:
        log(f"agentic: land: {task_id}: head moved since the decision ({head})")
        return LandResult("skipped", "head_moved", step.kind)
    entry: dict[str, object] = {
        "head": head,
        "step": step.kind,
        "refusal": step.refusal,
        "at": clock(),
    }
    ports.documents.record_closure(task_id, {"landing": [*landing_plan.entries(raw), entry]})
    context = LandingContext(
        task_id,
        cfg,
        ports,
        step,
        head,
        upstream,
        facts,
        log,
        resume=resume,
        launcher=launcher,
        argv0=argv0,
        role_span=role_span,
    )
    _dispatch(context)
    return LandResult("dispatched", step=step.kind)


@dataclass(frozen=True)
class _Recovery:
    """What `landing_sync.spawn_and_mark` reads: the recovery branch has no step to hand."""

    task_id: str
    ports: Ports
    resume: Callable[[int], int | None] | None
    log: Callable[[str], None]


def _recover(
    task_id: str,
    raw: TaskState,
    reference: str,
    ports: Ports,
    log: Callable[[str], None],
    clock: Callable[[], str],
    resume: Callable[[int], int | None] | None,
) -> LandResult | None:
    """A reopened landing whose resume child is gone (`landing_rules.resume_owed`) is
    respawned, whatever the merge decision would say: its entry is already written, so the
    path below would only answer `already_dispatched` or `not_live` forever. `None` when
    nothing is owed and the ordinary path goes on."""
    written = landing_plan.entries(raw)
    last = written[-1] if written else {}
    try:
        now = datetime.fromisoformat(clock())
    except ValueError:
        now = None
    if now is not None and now.tzinfo is not None and landing_rules.resume_owed(raw, now):
        try:
            pull_request = ports.tracker.pull_request(reference)
        except (ValueError, OSError) as exc:
            return LandResult("stopped", f"tracker_error: {exc}")
        if pull_request is None or pull_request.state != "OPEN":
            return LandResult("skipped", "pr_abandoned")
        pid = landing_sync.spawn_and_mark(_Recovery(task_id, ports, resume, log), clock)
        if pid is not None:
            spawned = last.get("resume_spawns", 0)
            k = (spawned if isinstance(spawned, int) else 0) + 1
            log(
                f"agentic: land: {task_id}: reopened pass not running, resume -> pid {pid} "
                f"({k} of {landing_rules.LANDING_RESUME_SPAWNS_MAX})"
            )
        step = next((kind for kind in landing_plan.LIVE_STEPS if kind == last.get("step")), None)
        return LandResult("dispatched", step=step)
    spent = last.get("resume_spawns", 0)
    if (
        landing_rules.landing_open(raw)
        and not raw.get("escalated")
        and isinstance(spent, int)
        and spent >= landing_rules.LANDING_RESUME_SPAWNS_MAX
    ):
        log(f"agentic: land: {task_id}: reopened pass not running, resume spawns spent")
        return LandResult("skipped", "resume_spent")
    return None


def _dispatch(context: LandingContext) -> None:
    """One arm per step kind; `assert_never` makes a ninth kind fail the type check here."""
    kind = context.step.kind
    match kind:
        case "sync_master":
            landing_sync.sync_master(context)
        case "re_review":
            landing_sync.re_review(context)
        case "renumber":
            landing_sync.renumber_adr(context)
        case "fix":
            landing_fix.fix(context)
        case "rerun":
            landing_fix.rerun(context)
        case "file_followups_and_land":
            landing_fix.file_followups_and_land(context)
        case "diagnose":
            landing_fix.diagnose(context)
        case "wait" | "none":
            context.log(f"agentic: land: {context.task_id}: {context.head} {kind}: nothing to do")
        case _:
            assert_never(kind)
