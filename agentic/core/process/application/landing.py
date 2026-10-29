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
from ..domain.rules import RunKind
from ..domain.task import UPSTREAM_KEY, ReleasePatch, TaskState, apply
from . import landing_fix, landing_plan, landing_slice, landing_sync
from .checkouts import SetupFailed, task_checkout
from .escalate import NEEDS_HUMAN
from .landing_plan import LandingContext
from .poll import Abandoned, StackUnreadable, assess_merge
from .ports import GitError, Launcher, Ports, RunLock, Upstream
from .resume import state_from


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
    run_kind: RunKind = "land_model",
    await_resume: Callable[[str], bool] | None = None,
) -> LandResult:
    """Takes `task_id`'s run lock (`LockHeld` if another process holds it) and releases it
    on the way out, whatever happens. `run_kind` is the kind this process registered under
    at spawn: a step it does not admit (`landing_rules.run_kind_admits`) is refused before
    anything is recorded or dispatched. `await_resume` runs with the lock already released
    (GH-270): the resume this land spawned refuses a task whose lock a live pid holds, so a
    land waiting for it to register must not be that pid."""

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
            run_kind,
            _after_releasing(lock, await_resume),
        )
    finally:
        lock.release(task_id)


def _after_releasing(
    lock: RunLock, await_resume: Callable[[str], bool] | None
) -> Callable[[str], bool] | None:
    """`await_resume` preceded by letting go of the task's lock (`release` is a no-op the
    second time, so the `finally` in `land` stays harmless)."""
    if await_resume is None:
        return None

    def wait(task_id: str) -> bool:
        lock.release(task_id)
        return await_resume(task_id)

    return wait


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
    run_kind: RunKind,
    await_resume: Callable[[str], bool] | None,
) -> LandResult:
    # The port answers a bare `dict`: the document carries keys beyond the state, and every
    # reader below takes it as the state (`.get` for the keys that may be absent).
    loaded: Any = ports.documents.load(task_id)
    number = ports.inbox.issue_of(task_id)
    if loaded is None or not loaded.get("pull_request") or number is None:
        return LandResult("stopped", "no_pull_request")
    if not landing_rules.run_kind_admits(run_kind, None, None) and landing_plan.stranded_now(
        ports, task_id, loaded
    ):
        return _refused(task_id, loaded, run_kind, "recovery", None, log)
    raw: TaskState = _freed(task_id, loaded, ports, log)
    reference = raw["pull_request"]
    recovered = _recover(
        task_id, raw, str(reference), ports, log, clock, resume, run_kind, await_resume
    )
    if recovered is not None:
        return recovered
    master = upstream.sha
    if master is None:
        return LandResult("stopped", "no_upstream")
    try:
        assessed = assess_merge(ports, task_id, raw, number, eval_set, master)
    except StackUnreadable as exc:
        return LandResult("stopped", f"stack_unreadable: {exc}")
    if isinstance(assessed, Abandoned):
        return LandResult("skipped", "pr_abandoned")
    decision, facts = assessed.decision, assessed.facts
    if decision.kind != "refuse" or decision.reason is None:
        return LandResult("skipped", f"decision_{decision.kind}")
    head = facts.pr_head
    if head is None:
        return LandResult("skipped", "no_head")
    resumed = landing_slice.open_entry(landing_plan.live_entries(raw), head)
    read: list[landing_rules.SliceInputs] = []

    def slice_inputs() -> landing_rules.SliceInputs:
        read.append(landing_plan.slice_inputs(raw, cfg, ports, master, head))
        return read[-1]

    if resumed is not None:
        # A slice a crash or a CI wait left open: continued as recorded, never decided again.
        units = resumed.get("slice_units")
        step = landing_rules.LandingStep(
            "land_slice",
            decision.reason,
            tuple(str(u) for u in units) if isinstance(units, list) else (),
        )
    else:
        step = landing_plan.step_for(
            raw,
            decision.reason,
            facts,
            slice_inputs,
            landing_plan.head_ancestry(ports, cfg, head, master),
        )
    if not landing_rules.run_kind_admits(run_kind, step.kind, step.refusal):
        return _refused(task_id, raw, run_kind, step.kind, head, log)
    if not landing_plan.takes_action(step.kind, live):
        log(f"agentic: land: {task_id}: {head} {step.kind}: nothing to do")
        return LandResult("skipped", "not_live", step.kind)
    try:
        now: datetime | None = datetime.fromisoformat(clock())
    except ValueError:
        now = None
    released: ReleasePatch = {}
    if landing_plan.release_asked(raw, step.kind):
        outcome = landing_plan.release(ports, cfg, task_id, raw, number, head=head, upstream=master)
        if not outcome.released:
            return _waiting_on_human(task_id, head, outcome.why, log)
        # The step after the release is the table's on the document as the release leaves it
        # (a sync, a rerun, a wait), never the diagnose the escalated view gave; it must be
        # one this run may take before anything is written.
        at = clock()
        released = landing_plan.release_patch(raw, outcome, master, at)
        step = landing_plan.released_step(
            raw,
            outcome,
            facts,
            decision.reason,
            at=at,
            slicing=slice_inputs,
            ancestry=landing_plan.head_ancestry(ports, cfg, head, master),
        )
        if not landing_rules.run_kind_admits(run_kind, step.kind, step.refusal):
            return _refused(task_id, raw, run_kind, step.kind, head, log)
        if not landing_plan.takes_action(step.kind, live):
            log(f"agentic: land: {task_id}: {head} {step.kind}: nothing to do")
            return LandResult("skipped", "not_live", step.kind)
    # What the restart comment calls "(integration work)": the integrator's paths are what kept
    # every slice from landing (stamped on the diagnose entry, read by landing_fix).
    integration_work = (
        step.kind == "diagnose"
        and step.restart_why is not None
        and bool(read)
        and landing_rules.integration_blocked(read[-1])
    )
    # The document as the release leaves it: the gate and the entry below read this view, the
    # file keeps the escalation until everything else has agreed (head, merge).
    current: TaskState = apply(raw, released) if released else raw
    gate = landing_plan.diagnose_gate(current, step.kind, now)
    if gate == "skip":
        log(f"agentic: land: {task_id}: {head} {step.kind}: diagnosis not due")
        return LandResult("skipped", "diagnose_waiting", step.kind)
    if gate == "escalate":
        context = LandingContext(
            task_id, cfg, ports, step, head, upstream, assessed.facts, log, role_span=role_span
        )
        landing_fix.escalate_failed_diagnosis(context)
        return LandResult("dispatched", step=step.kind)
    if resumed is None and landing_rules.already_dispatched(
        landing_plan.live_entries(current), head, step.kind, master
    ):
        log(f"agentic: land: {task_id}: {head} {step.kind}: already taken")
        return LandResult("skipped", "already_dispatched", step.kind)
    if (
        foreign := _foreign_merge(task_id, cfg, ports, raw, step.kind, head, master, log)
    ) is not None:
        return foreign
    # The last look at the pull request before anything is written: a push since the
    # decision makes it stale, and the next round decides again.
    try:
        again = ports.tracker.pull_request(str(reference))
    except (ValueError, OSError) as exc:
        return LandResult("stopped", f"tracker_error: {exc}")
    if again is None or again.head_oid != head:
        log(f"agentic: land: {task_id}: head moved since the decision ({head})")
        return LandResult("skipped", "head_moved", step.kind)
    if released:
        # One write, in the same call that appends the entry and dispatches: the release
        # is never left undispatched (the head and the merge were checked above).
        ports.documents.save(apply(state_from(dict(raw)), released))
        if outcome.decision is None:
            # Nobody decided: the label goes with the escalation (Nota al tracker, best effort).
            ports.tracker.drop_label(task_id, NEEDS_HUMAN)
            log(
                f"agentic: land: {task_id}: {head} {step.kind}: escalation released, "
                "the base branch CI is green"
            )
        else:
            log(
                f"agentic: land: {task_id}: {head} {step.kind}: "
                "escalation released by a human decision"
            )
    entry: dict[str, object] = {
        "head": head,
        "step": step.kind,
        "refusal": step.refusal,
        "at": clock(),
    }
    if step.kind in ("sync_master", "diagnose") and upstream.sha is not None:
        # A sync: what it is for, `deliver` resolves it only if the head holds this sha. A
        # diagnosis: the master it is made against, which its `sync` verdict is served for.
        entry[UPSTREAM_KEY] = upstream.sha
    if step.kind == "escalate":
        # What the escalation was about, so its release reads the same names and pair
        # (`landing.escalation_checks`): the failing checks and the master they were seen at.
        verdict = facts.ci_verdict
        entry["checks"] = list(
            dict.fromkeys(check.name for check in (verdict.failing if verdict else ()))
        )
        if upstream.sha is not None:
            entry[UPSTREAM_KEY] = upstream.sha
    if step.kind == "land_slice":
        entry["slice_units"] = list(step.units)
    if step.restart_why is not None:
        entry["restart_why"] = step.restart_why
        if integration_work:
            entry["integration_work"] = True
    if resumed is None:
        ports.documents.record_closure(
            task_id, {"landing": [*landing_plan.all_entries(current), entry]}
        )
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
        await_resume=await_resume,
        clock=clock,
    )
    try:
        _dispatch(context)
    except GitError as exc:
        # A merge the step could not clear (`landing_sync.merges_cleared`): nothing was
        # reopened or spawned on a tree still mid-merge.
        log(f"agentic: land: {task_id}: {head} {step.kind}: git failed: {exc}")
        return LandResult("stopped", f"git_error: {exc}", step.kind)
    return LandResult("dispatched", step=step.kind)


_WAITING_ON: dict[landing_plan.ReleaseWhy, tuple[str, str]] = {
    "waiting_on_human": (
        "escalated_waiting_on_human",
        "a human comment after the Escalated: comment, the needs-human label removed, "
        "the issue open",
    ),
    "needs_restart": (
        "escalated_needs_restart",
        "restart it with `agentic run --fresh`: a comment does not release this one",
    ),
    "task_cap": ("escalated_task_cap", "raise the task budget: the task cap is spent"),
    "waiting_on_base_ci": (
        "escalated_waiting_on_base_ci",
        "waiting for the base branch CI to turn green, or an owner comment",
    ),
    "released": ("escalated_waiting_on_human", ""),
    "released_by_ci": ("escalated_waiting_on_human", ""),
}


def _waiting_on_human(
    task_id: str,
    head: str,
    why: landing_plan.ReleaseWhy,
    log: Callable[[str], None],
) -> LandResult:
    """An escalated diagnose that stays escalated (GH-328): nothing is written, and the line
    says what releases it instead of reading as a retry that is waiting."""
    reason, needs = _WAITING_ON[why]
    log(f"agentic: land: {task_id}: {head} diagnose: escalated, waiting on a human ({needs})")
    return LandResult("skipped", reason, "diagnose")


MERGING_STEPS: frozenset[LandingStepKind] = frozenset({"sync_master", "re_review", "renumber"})


def _foreign_merge(
    task_id: str,
    cfg: Configuration,
    ports: Ports,
    document: TaskState,
    kind: LandingStepKind,
    head: str,
    master: str,
    log: Callable[[str], None],
) -> LandResult | None:
    """A merge in the task worktree that is not the system's (`landing_rules.merge_owner`):
    a step that merges would collide with it, and aborting it would throw a person's work
    away, so nothing is recorded or aborted and the next round looks again. `None` when the
    step does not merge, nothing is in progress or the merge is the landing's own."""
    if kind not in MERGING_STEPS:
        return None
    try:
        checkout = task_checkout(document, cfg, ports)
    except SetupFailed:
        # The step itself reports a worktree that cannot be made (it reopens the unit).
        return None
    try:
        merging = ports.workspace.merge_head(checkout.path)
    except GitError as exc:
        return LandResult("stopped", f"merge_head_unreadable: {exc}", kind)
    owner = landing_rules.merge_owner(merging, master, head, landing_plan.all_entries(document))
    if owner != "foreign":
        return None
    log(f"agentic: land: {task_id}: merge in progress ({merging}) is not the system's, left alone")
    return LandResult("skipped", "foreign_merge", kind)


def _refused(
    task_id: str,
    document: TaskState,
    run_kind: RunKind,
    step: LandingStepKind | Literal["recovery"],
    head: str | None,
    log: Callable[[str], None],
) -> LandResult:
    """The child was registered for steps that spend no model and the step it recomputed
    does (the head moved since the poller classified it, or a recovery is owed): nothing is
    recorded, no resume or diagnoser is spawned, and the next round decides again. `head`
    is the one the step answers; a recovery has none, so the last entry's stands in."""
    entries = landing_plan.all_entries(document)
    shown = head or (str(entries[-1].get("head")) if entries else "-")
    log(
        f"agentic: land: {task_id}: {shown} skipped: run_kind_mismatch "
        f"(registered {run_kind}, step {step})"
    )
    return LandResult("skipped", "run_kind_mismatch")


def _freed(task_id: str, loaded: Any, ports: Ports, log: Callable[[str], None]) -> TaskState:
    """A stranded legacy diagnose (`landing_rules.stranded_diagnose`, GH-257) is freed with
    one `record_closure` call and read again; any other document, or one a comment says a
    human was told of, is returned as it is. The comments are read only for a document
    already shaped like the stranded one."""
    document: TaskState = loaded
    if not landing_plan.stranded_now(ports, task_id, document):
        return document
    ports.documents.record_closure(task_id, landing_rules.migration(document))
    log(f"agentic: land: {task_id}: stranded diagnosis freed, retrying on the calendar")
    freed: Any = ports.documents.load(task_id)
    again: TaskState = freed if freed is not None else document
    return again


@dataclass(frozen=True)
class _Recovery:
    """What `landing_sync.spawn_and_mark` reads: the recovery branch has no step to hand."""

    task_id: str
    ports: Ports
    resume: Callable[[int], int | None] | None
    log: Callable[[str], None]
    await_resume: Callable[[str], bool] | None = None


def _recover(
    task_id: str,
    raw: TaskState,
    reference: str,
    ports: Ports,
    log: Callable[[str], None],
    clock: Callable[[], str],
    resume: Callable[[int], int | None] | None,
    run_kind: RunKind,
    await_resume: Callable[[str], bool] | None,
) -> LandResult | None:
    """A reopened landing whose resume child is gone (`landing_rules.resume_owed`) is
    respawned, whatever the merge decision would say: its entry is already written, so the
    path below would only answer `already_dispatched` or `not_live` forever. `None` when
    nothing is owed and the ordinary path goes on."""
    written = landing_plan.live_entries(raw)
    last = written[-1] if written else {}
    try:
        now = datetime.fromisoformat(clock())
    except ValueError:
        now = None
    if now is not None and now.tzinfo is not None and landing_rules.resume_owed(raw, now):
        if not landing_rules.run_kind_admits(run_kind, None, None):
            return _refused(task_id, raw, run_kind, "recovery", None, log)
        try:
            pull_request = ports.tracker.pull_request(reference)
        except (ValueError, OSError) as exc:
            return LandResult("stopped", f"tracker_error: {exc}")
        if pull_request is None or pull_request.state != "OPEN":
            return LandResult("skipped", "pr_abandoned")
        pid = landing_sync.spawn_and_mark(
            _Recovery(task_id, ports, resume, log, await_resume), clock
        )
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
        case "fix_important":
            landing_fix.fix_important(context)
        case "rerun":
            landing_fix.rerun(context)
        case "file_followups_and_land":
            landing_fix.file_followups_and_land(context)
        case "diagnose":
            landing_fix.diagnose(context)
        case "escalate":
            landing_fix.escalate_red_on_base(context)
        case "land_slice":
            landing_slice.land_slice(context)
        case "settle":
            landing_fix.settle(context)
        case "escalate_ledger":
            landing_fix.escalate_ledger(context)
        case "escalate_adr":
            landing_fix.escalate_adr(context)
        case "wait" | "none":
            context.log(f"agentic: land: {context.task_id}: {context.head} {kind}: nothing to do")
        case _:
            assert_never(kind)
