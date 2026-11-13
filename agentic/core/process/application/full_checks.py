"""The `full_checks` node (GH-358): the whole suite, once, on the integrated head.

A product that declares `delivery_checks` gates each delivery on the fast tier only; this node
is where the rest of `checks` runs, with no affected form substituted, before the pull request
opens (`rules.after_integrate` sends the task here once every unit is approved). No role runs
and nothing is paid: it is the system running the product's own commands.

In order: the task checkout and the clean tree it requires, both made once by
`verifiers.verify` (GH-469) and handed in (a setup that fails escalates as `deliver`'s does),
every declared check run once through the same `CheckoutPresubmit` the merge gate
uses, the tree put back as its head holds it whatever the checks did (`restore_clean`, never
`-x`), the record and the `Full checks` section written. A red run reopens the integration
with one row per failed check (`rules.full_checks_return`); a green one writes a passed record
and nothing else, and the fan-in goes on.

A product that declares `ci:` (GH-376) does not run the suite here: the integrated head is
published to its own ref (`rules.ci_ref`) and the node waits for the declared jobs on exactly
that head (`ci.full_tier_verdict`), recording their verdict. Any way the CI cannot be used
escalates instead (GH-462): when GitHub gives no verdict (`CiCause`: the push failed, no run
started, a job never started, `gh` unreadable) the task stops with class `ci` through
`rules.ci_unavailable`, writes no record and never runs a local check. The one way left to the
local tier above is a head the product's own workflow cannot judge (`untrusted`), and the record
and the section say so, unless the product declares `ci_dispatch`: then the base branch's workflow
is dispatched with the head sha as its input, its run is found by event, workflow and the sha in
its title, and its verdict is recorded with `ran_on: ci_dispatch` and the `run_id`. A refused
dispatch escalates at once with no wait and no retry; no run after the grace period is dispatched
once more. A trusted head whose pushes got no run even after the republish (an old branch whose own
workflow lacks the CI ref trigger) is judged the same way when the product's fetched upstream
declares `ci_dispatch` (GH-531, `_fallback_dispatch`): the record adds `dispatch_cause: no_run` and
`stack_at`, the upstream revision whose `ci:` names judge it. The head is published to
`rules.ci_dispatch_ref` before every dispatch and the ref is never deleted here (GH-504); a
dispatched job that failed at `Set up job` or the checkout is
`checkout_failed`, CI not usable, never a red check.

In that run a check that timed out and that the stack maps to a CI job (`ci_covers`, GH-453)
is deferred when `presubmit.deferred_verdict` says so: its row is marked `deferred`, it is no
failure and no pass, and the merge asks that job for the verdict."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, assert_never

import anyio
from pydantic import TypeAdapter

from agentic.shared.configuration import Configuration

from ..domain import ci, presubmit, rules, speculation
from ..domain.check_schedule import CheckPriority
from ..domain.ci import (
    CiCheck,
    CiReport,
    CiUnreadable,
    DispatchedRun,
    FailedStage,
    FullTierVerdict,
)
from ..domain.ci_push import PushKind, push_failure_markdown
from ..domain.presubmit import CheckResult, CiCover, CiDispatch
from ..domain.roles import DEVELOPER_INTEGRATOR
from ..domain.task import (
    WAITING_ON_HUMAN,
    CiCancelRerun,
    CiCause,
    CiGhostDispatch,
    CiTiming,
    FallbackCause,
    FullCheckEntry,
    FullChecksRecord,
    TaskState,
    apply,
)
from . import ci_flakes, ci_push, ci_trust
from .checkouts import Checkout, task_base
from .ci_push import Pushed, PushFailed
from .ports import GitError, Ports
from .presubmit import CheckoutPresubmit, readiness_of, reuse_of
from .run_role import finish

SECTION = "Full checks"
_NOTHING_BEFORE = ""
_ENTRIES = TypeAdapter(list[FullCheckEntry])
_REPRODUCE = (
    "The integrated head failed CI; reproduce it with the product's declared checks in your "
    "checkout."
)

# What was tried for each closed cause, as prose for the human: the cause itself is the value
# the node chose from the step that failed, never text read back from `gh` or git (ADR-0029).
_CI_DETAIL: dict[CiCause, str] = {
    "gh_unavailable": "`gh` could not be run for the whole {min:g} min wait on {ref}",
    "gh_unreadable": "`gh` answered something unreadable for the whole {min:g} min wait on {ref}",
    "no_run": "no CI run on {ref} for that head after {min:g} min, though the ref was pushed twice",
    "publish_failed": "the push of {ref} failed",
    "not_started": "the declared CI jobs on {ref} did not start (startup failure or cancelled)",
    "dispatch_refused": "`gh` refused to dispatch the workflow for that head",
    "jobs_missing": "the run completed without a declared job",
    "unknown_names": "no check run carries a declared job name",
    "run_unfinished": "a workflow run of that head was still going after {min:g} min and had "
    "reported no declared job",
}
# Every cause when the verdict was asked of a dispatched run (GH-504), none inherited from the CI
# branch ref's table: `{ref}` is the workflow file, `{dispatch_ref}` the ref the head was
# published to, `{sha}` the head, `{stage}` where a failed job stopped. All system data.
_DISPATCH_DETAIL: dict[CiCause, str] = {
    "gh_unavailable": "`gh` could not be run for the whole {min:g} min wait on {ref}",
    "gh_unreadable": "`gh` answered something unreadable for the whole {min:g} min wait on {ref}",
    "no_run": "no dispatched run of {ref} for that head after {min:g} min, though it was "
    "dispatched twice",
    "not_started": "the declared CI jobs of the dispatched {ref} did not start "
    "(startup failure or cancelled)",
    "dispatch_refused": "`gh` refused to dispatch {ref} for that head",
    "jobs_missing": "the dispatched {ref} completed without a declared job",
    "unknown_names": "no check run carries a declared job name",
    "publish_failed": "the push of the head {sha} to {dispatch_ref} failed, so {ref} was not "
    "dispatched",
    "checkout_failed": "the dispatched {ref} failed at its {stage} step before the suite ran; head "
    "{sha} had been published to {dispatch_ref}",
    "run_unfinished": "the dispatched {ref} was still going after {min:g} min and had reported no "
    "declared job",
}

_LOG = logging.getLogger(__name__)

# Why the dispatch of the no-run fallback (GH-531) was not even tried: a closed value chosen by
# the node, never text read back from git. `undeclared` is the product that declares no
# `ci_dispatch` (or no `ci`) at its upstream, whose escalation reads exactly as it always did.
NotTriedWhy = Literal["no_upstream", "stack_unreadable", "undeclared"]
_NOT_TRIED_DETAIL: dict[NotTriedWhy, str] = {
    "no_upstream": "; no dispatch was tried: the product's upstream could not be read",
    "stack_unreadable": "; no dispatch was tried: the stack at the upstream did not load",
    "undeclared": "",
}
_UNFETCHED_DETAIL = " (the upstream was not fetched)"


@dataclass
class _Restart:
    """What the wait saw of the single rerun of a cancelled head (GH-539), held in memory: the
    rerun's run id, handed on to `ci.rerun_restarted` and never compared here, and whether a read
    showed that run outside `COMPLETED`."""

    run_id: str | None = None
    seen: bool = False

    def watch(self, read: CiReport | CiUnreadable) -> None:
        if self.run_id is not None and ci.rerun_restarted(read, self.run_id):
            self.seen = True

    def rearm(self, run_id: str) -> None:
        self.run_id = run_id
        self.seen = False


@dataclass
class _Ghosts:
    """What the node saw of the ghosts of its head (GH-601), held in memory for the whole node:
    the `ci.GhostWatch` every read of every wait feeds, and the node's start, from the node's own
    injected `clock()`. One per node because the node chains up to three waits (the speculative
    one, the republish one and each rescue round) and a ghost must keep its first instant across
    them. No instant GitHub gave is ever compared with a clock here.

    Known risk: a node that dies loses the map and the retake counts from zero, which is the safe
    side (it waits longer and never dispatches too early)."""

    node_started: float
    bound_s: float
    current: ci.GhostWatch = field(default_factory=ci.GhostWatch)

    def watch(self, read: CiReport | CiUnreadable, at_s: float) -> None:
        self.current = self.current.observe_report(read, at_s)

    def watch_dispatched(self, runs: Sequence[DispatchedRun], head: str, at_s: float) -> None:
        self.current = self.current.observe_dispatched(tuple(runs), head, at_s)

    def elapsed(self, now_s: float) -> float:
        return now_s - self.node_started

    def known(self) -> frozenset[str]:
        return frozenset(self.current.seen)

    def ghosts(self, now_s: float) -> frozenset[str]:
        return self.current.ghosts(now_s, self.bound_s)

    def maturing(self, now_s: float) -> frozenset[str]:
        return self.current.maturing(now_s, self.bound_s)


@dataclass
class _GhostFact:
    """The `ci_ghost_dispatch` the node holds while it dispatches (GH-601): the one fact saved
    after an accepted dispatch, kept to be carried by every patch that follows."""

    fact: CiGhostDispatch | None = None


@dataclass(frozen=True)
class _Fallback:
    """The declaration that makes a `no_run` push dispatchable (GH-531), read at one revision of
    the upstream: the revision, the `ci_dispatch` and the gate names of its `ci:`."""

    sha: str
    dispatch: CiDispatch
    gate_names: tuple[str, ...]


@dataclass(frozen=True)
class _NotTried:
    """The no-run fallback could not be tried: `why`, and for `undeclared` whether the upstream
    was not fetched (it is then the last known one, which only the escalation says)."""

    why: NotTriedWhy
    unfetched: bool = False


@dataclass(frozen=True)
class _LocalUntrusted:
    """A head the product's own CI cannot judge and no `ci_dispatch` can: the local tier runs.
    `detail` is the closed suffix of the section's line, `_UNFETCHED_DETAIL` only when the master
    could not be resolved or fetched, never when the merge-base read failed."""

    detail: str = ""


@dataclass(frozen=True)
class _NoVerdict:
    """GitHub gave no verdict: the closed `cause` and how long the node waited on it."""

    cause: CiCause
    waited_s: float
    # The workflow file of a dispatched run that gave none; None for the task's own CI ref.
    workflow: str | None = None
    # What a push that still failed after its retries reported (GH-487): git's stderr already
    # through `ci_push.scrub`, the tries it took and how it read. Prose for the human only; the
    # `cause` stays the closed value (ADR-0029). None for any other way of giving no verdict.
    git_said: str | None = None
    push_attempts: int | None = None
    push_kind: PushKind | None = None
    # `unknown_names` only (GH-485): the names no check run carries, and the names that did report.
    unknown: tuple[str, ...] = ()
    reported: tuple[str, ...] = ()
    # A dispatched run only (GH-504): the head that was published to the dispatch ref and, for
    # `checkout_failed`, the stage the failed job(s) stopped at, so the message names them.
    head: str | None = None
    stage: FailedStage | None = None
    # The instants of the wait that gave no verdict (GH-467); `None` when nothing was pushed.
    timing: CiTiming | None = None
    # A push that got no run and whose dispatch fallback was not tried (GH-531): why, for the
    # closed suffix of the escalation. `None` for every other way of giving no verdict.
    not_tried: _NotTried | None = None
    # The single rerun of a cancelled run of the head (GH-539), kept on the escalation's patch so
    # the fact survives the document write; `None` when the node reran nothing.
    cancel_rerun: CiCancelRerun | None = None
    # The fresh run dispatched for a ghost (GH-601), kept on the escalation's patch like the rerun.
    ghost_dispatch: CiGhostDispatch | None = None


Sleep = Callable[[float], Awaitable[None]]
PushSleep = Callable[[float], None]
Clock = Callable[[], float]
Now = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _head_trust(ports: Ports, checkout: Checkout, base: str) -> tuple[str, ci_trust.TrustRead]:
    """The integrated head and how the product's own CI may judge it (`ci_trust.read`, GH-565):
    the task base's diff first, and the master's merge base only for a head that diff distrusts.
    Not trusted when the change touches the CI definition or the diff cannot be read (fail
    closed, `rules.ci_untrusted`). The one place the two are read, for the node and for
    `full_checks_mode`; the master may be fetched, so the node runs it in a worker thread."""
    head = ports.workspace.full_head(checkout.path)
    read = ci_trust.read(
        ports, head, task_base=base, resolve_master=lambda: ports.workspace.upstream()
    )
    return head, read


def _pushed_for(state: TaskState, head: str) -> bool:
    """Whether a review already pushed this very commit and its run can be waited for
    (GH-465): the one place `speculation.consumable` is asked."""
    return bool(state.get("ci_speculation")) and speculation.consumable(state, head)


def full_checks_mode(
    state: TaskState, cfg: Configuration, ports: Ports, checkout: Checkout
) -> Literal["local", "consumable", "wait"]:
    """How the integrated head's `full_checks` will be decided (GH-469), from the same reads
    the node makes and with no side effect: `local` when the suite runs on this machine (no
    `ci:`, or a head the product's workflow cannot judge and no `ci_dispatch`), `consumable`
    when the run a review pushed for this very commit only has to be read, `wait` when a CI
    verdict has to be waited for (a push, or a dispatched run). Only `wait` is slow enough to
    be worth running beside the roles. At most one fetch, only for a head the task base does not
    trust; writes nothing."""
    base = task_base(state, ports)
    if not ports.stack.ci(base):
        return "local"
    head, trust = _head_trust(ports, checkout, base)
    if not trust.trusted:
        return "wait" if trust.dispatch is not None else "local"
    return "consumable" if _pushed_for(state, head) else "wait"


async def full_checks(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    *,
    sleep: Sleep = anyio.sleep,
    clock: Clock = time.monotonic,
    now: Now = _utc_now,
    checkout: Checkout,
    push_sleep: PushSleep = time.sleep,
) -> TaskState:
    """`sleep` and `clock` are the CI wait's, injectable so a test does not wait; `now` is the
    wall clock `speculation.event_lost` measures the speculative push against; `push_sleep` is
    the pause between the tries of a failed push of the CI ref (GH-487), a thread's, not the
    wait's. A `checkout` handed in (the verifier set resolves the task's once, GH-469): no
    resolution and no clean check here, `verifiers.verify` made both."""
    workspace = ports.workspace
    task = checkout
    base = task_base(state, ports)
    names = ports.stack.ci(base)
    if not names:
        return await _local_full_checks(state, cfg, ports, task, base, None)
    outcome = await _ci_full_checks(
        state, cfg, ports, task, base, names, sleep, clock, now, push_sleep
    )
    if isinstance(outcome, _LocalUntrusted):
        return await _local_full_checks(state, cfg, ports, task, base, "untrusted", outcome.detail)
    if isinstance(outcome, _NoVerdict) and outcome.cause == "unknown_names":
        head = workspace.full_head(task.path)
        plan_only = rules.ci_gate_names(names, state.get("ci_required") or []).plan_only
        return finish(
            state,
            rules.ci_planning_defect(head, _unknown_text(outcome, head, plan_only), at=ports.now()),
            ports,
        )
    if isinstance(outcome, _NoVerdict):
        head = workspace.full_head(task.path)
        table = _CI_DETAIL if outcome.workflow is None else _DISPATCH_DETAIL
        ref = outcome.workflow or rules.ci_ref(state["id"])
        detail = table[outcome.cause].format(
            ref=ref,
            min=round(outcome.waited_s / 60, 1),
            stage=outcome.stage,
            dispatch_ref=rules.ci_dispatch_ref(state["id"]),
            sha=outcome.head or head,
        )
        patch = rules.ci_unavailable(
            outcome.cause,
            head,
            detail + _not_tried_text(outcome) + _git_said(outcome),
            at=ports.now(),
            ci=outcome.timing,
        )
        if outcome.cancel_rerun is not None:
            patch["ci_cancel_rerun"] = outcome.cancel_rerun
        if outcome.ghost_dispatch is not None:
            patch["ci_ghost_dispatch"] = outcome.ghost_dispatch
        if outcome.git_said and outcome.push_attempts and outcome.push_kind:
            # Git's words reach the section only through `push_failure_markdown`, which
            # scrubs them; the older run stays as history under its own free `(vN)` key.
            # A dispatched run's push was of the dispatch ref, not of the workflow file.
            pushed_ref = rules.ci_dispatch_ref(state["id"]) if outcome.workflow else ref
            patch["sections"] = _section(
                state,
                0,
                push_failure_markdown(
                    outcome.cause,
                    pushed_ref,
                    outcome.push_attempts,
                    outcome.push_kind,
                    outcome.git_said,
                ),
            )
        return finish(state, patch, ports)
    return finish(state, outcome, ports)


def _not_tried_text(outcome: _NoVerdict) -> str:
    """The closed suffix of a `no_run` whose dispatch fallback was not tried (GH-531): empty
    for a product that declares no `ci_dispatch` at its upstream, so its text is today's."""
    if outcome.not_tried is None:
        return ""
    unfetched = _UNFETCHED_DETAIL if outcome.not_tried.unfetched else ""
    return _NOT_TRIED_DETAIL[outcome.not_tried.why] + unfetched


def _git_said(outcome: _NoVerdict) -> str:
    """What git said, as the tail of the escalation's detail. Added after the `str.format` of
    the detail, never inside it: git's text is a third party's and may hold braces."""
    if not (outcome.git_said and outcome.push_attempts and outcome.push_kind):
        return ""
    tries = f"{outcome.push_attempts} attempt(s) ({outcome.push_kind})"
    return f" after {tries}; git said: {outcome.git_said}"


def _publish_failed(
    exc: GitError,
    waited: float,
    workflow: str | None = None,
    head: str | None = None,
    timing: CiTiming | None = None,
) -> _NoVerdict:
    """The no-verdict of a push that failed. A `PushFailed` brings what git said; a `GitError`
    that is not one is today's bare `publish_failed`. `workflow` and `head` are a dispatched
    run's (GH-504): the push was of the dispatch ref, and the message names it."""
    if isinstance(exc, PushFailed):
        return _NoVerdict(
            "publish_failed",
            waited,
            workflow,
            git_said=exc.said,
            push_attempts=exc.attempts,
            push_kind=exc.kind,
            head=head,
            timing=timing,
        )
    return _NoVerdict("publish_failed", waited, workflow, head=head, timing=timing)


async def _push(op: Callable[[], None], push_sleep: PushSleep) -> Pushed:
    """One git step of the CI path, retried by what git says (`ci_push.publish_with_retry`),
    run in a worker thread: its pauses block the thread, never the loop."""
    return await anyio.to_thread.run_sync(lambda: ci_push.publish_with_retry(op, sleep=push_sleep))


def _with_pushes(patch: dict[str, Any], pushes: list[Pushed]) -> None:
    """Note on the record the push that needed more than one try (the worst one, when two
    steps did). A first-try push adds no key."""
    worst = max(pushes, key=lambda pushed: pushed.attempts, default=None)
    if worst is not None and worst.attempts > 1 and worst.kind is not None:
        patch["full_checks"]["push_attempts"] = worst.attempts
        patch["full_checks"]["push_kind"] = worst.kind


def _unknown_text(outcome: _NoVerdict, head: str, plan_only: tuple[str, ...]) -> str:
    """The escalation for names no check run carries (GH-485): `ci.unreported_text`, the one
    wording the full tier, the merge gate and the landing share."""
    return ci.unreported_text(outcome.unknown, outcome.reported, head, plan_only)


async def _local_full_checks(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    task: Checkout,
    base: str,
    fallback: FallbackCause | None,
    detail: str = "",
) -> TaskState:
    """The suite run on this machine. `fallback` is why a `ci:` product ended up here: the
    record and the section carry it, the section with `detail` after it (empty, or the closed
    `_UNFETCHED_DETAIL` of a head whose master could not be used). `None` for a product without
    `ci:`, whose record gets no new key."""
    workspace = ports.workspace
    declared = ports.stack.checks(base)
    gate = CheckoutPresubmit(
        ports,
        task.path,
        _NOTHING_BEFORE,
        declared,
        cfg.check_timeout_s,
        priority=CheckPriority.INTEGRATION,
        wait_max_s=cfg.check_wait_max_s,
        witness=ports.stack.witness(base),
        parallel=ports.stack.parallel_checks(base),
        reuse=reuse_of(ports, base),
        rerun_forms=ports.stack.rerun_checks(base),
        flake_gate="full_checks",
        readiness=readiness_of(ports, base, cfg.check_cores),
    )
    head = workspace.full_head(task.path)
    started = time.monotonic()
    try:
        # Called once, with no refusal loop and no affected plan: the suite as declared.
        await anyio.to_thread.run_sync(gate)
    finally:
        # A check that edits a tracked file or writes an untracked one must not refuse the
        # next delivery of a reopened unit, nor the landing's own clean-tree checks.
        # Shielded: it must also run when the signal's cancellation is what unwinds us.
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(workspace.restore_clean, task.path)
    covers = ports.stack.ci_covers(base) if fallback is not None else {}
    rows, deferred = _deferrals(gate.results, fallback, covers)
    merged = presubmit.merge_checks_record(
        head=head,
        master=base,
        duration_s=round(time.monotonic() - started, 1),
        declared=declared,
        results=rows,
        witness=gate.witness_result,
        gate=gate.gate,
    )
    record = _record(state, merged, declared, gate, deferred)
    lines = [_deferred_line(name, covers[name], cfg.check_timeout_s) for name in deferred]
    text = presubmit.failed_checks_markdown(
        _without_deferred(merged, deferred), head, neutralise=False
    )
    if record["passed"]:
        patch: TaskState = {"full_checks": record}
        reused = {entry["name"] for entry in record["results"] if entry.get("reused")}
        flaked = {entry["name"] for entry in record["results"] if entry.get("flake")}
        shown = [_shown(name, reused, flaked) for name in declared if name not in deferred]
        text = f"Passed on `{head}`: {', '.join(shown)}."
        if lines:
            text += "\n\n" + "\n".join(lines)
        if breaches := presubmit.budget_breaches(gate.results, ports.stack.check_budgets(base)):
            # A record and a sentence, never a return: a slow suite is not a failed one.
            record["over_budget"] = [
                {**b, "duration_s": _shown_up(b["duration_s"])} for b in breaches
            ]
            text += f" {_over_budget_sentence(record['over_budget'])}"
    else:
        patch = rules.full_checks_return(
            state,
            record,
            cfg.max_changes_requested_per_pair,
            at=ports.now(),
            mode=cfg.review_mode,
        )
        text = text or f"Failed on `{head}`: no check output was recorded."
        if lines:
            text += "\n\n" + "\n".join(lines)
    if fallback is not None:
        record["ran_on"] = "local"
        record["ci_fallback"] = fallback
        text = f"CI not used ({fallback}){detail}: ran locally.\n\n{text}"
    patch["sections"] = _section(state, record["version"], text)
    if flakes := gate.flakes():
        patch["flakes"] = ci_flakes.dated(flakes, ci_flakes.now())
    return finish(state, patch, ports)


async def _ci_full_checks(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    task: Checkout,
    base: str,
    names: tuple[str, ...],
    sleep: Sleep,
    clock: Clock,
    now: Now,
    push_sleep: PushSleep,
) -> dict[str, Any] | _NoVerdict | _LocalUntrusted:
    """The patch of a run decided on GitHub, `_LocalUntrusted` for running the local tier, or the
    `_NoVerdict` GitHub gave no verdict for. When no run starts within the grace period the
    ref is published once more (GH-462). The
    paths are read from the workspace only and the head is published to the task's own CI
    ref; the wait takes no check slot and runs no command (ADR-0070). Each push is retried
    by what git said (GH-487); the one that needed it is noted on the record."""
    workspace = ports.workspace
    head, trust = await anyio.to_thread.run_sync(lambda: _head_trust(ports, task, base))
    gate_names = rules.ci_gate_names(names, state.get("ci_required") or []).names
    ghosts = _Ghosts(clock(), cfg.check_timeout_s)
    recorded_ghost = state.get("ci_ghost_dispatch")
    if recorded_ghost is not None and ci.ghost_fact_in_force(recorded_ghost, head):
        # GH-601: a fresh run was already dispatched for this very head and the task was
        # retaken: wait on the dispatched runs, never dispatch (nor publish) again. A fact of
        # another head, an escalated one or an unreadable instant is not in force.
        since = _since_of(recorded_ghost["dispatched_at"])
        waiting_on = await anyio.to_thread.run_sync(_fallback_dispatch, ports, state)
        if since is not None and isinstance(waiting_on, _Fallback):
            return await _dispatched_after_no_run(
                state,
                cfg,
                ports,
                task,
                head,
                waiting_on,
                [],
                ports.now(),
                0.0,
                sleep,
                clock,
                push_sleep,
                cause="ghost",
                ghosts=ghosts,
                ghost_fact=recorded_ghost,
                since=since,
            )
    if not trust.trusted:
        if trust.dispatch is None:
            unfetched = trust.fallback in ("no_master", "unfetched")
            return _LocalUntrusted(_UNFETCHED_DETAIL if unfetched else "")
        return await _dispatched_full_checks(
            state, cfg, ports, task, head, gate_names, trust.dispatch, sleep, clock, push_sleep
        )
    ref = rules.ci_ref(state["id"])
    speculative = False
    pushes: list[Pushed] = []
    pushed = state.get("ci_speculation")
    # Whether a read showed the rerun's run outside `COMPLETED` while this node waited (GH-539),
    # in memory only; the rerun fact of an earlier node, when it is for this head, starts it.
    recorded = state.get("ci_cancel_rerun")
    running = _Restart(
        recorded["run_id"] if recorded and rules.same_commit(recorded["head"], head) else None
    )
    # The instants of the CI wait (GH-467), stamped by the application from `ports.now`: this
    # node's start, and the push the wait is for (the review's persisted one when its run is
    # consumed, this node's own otherwise).
    node_started_at = ports.now()
    pushed_at: str | None = pushed["pushed_at"] if pushed and _pushed_for(state, head) else None
    if pushed and _pushed_for(state, head):
        # A review already pushed this very commit (GH-465): its run is waited for, not
        # pushed again. A run that never came, or was cancelled, is today's path from the
        # republish on, and only the wait that follows it may escalate.
        decided, read, waited = await _speculated_wait(
            pushed["pushed_at"],
            cfg,
            ports,
            ref,
            head,
            gate_names,
            sleep,
            clock,
            now,
            running,
            ghosts,
        )
        speculative = decided is not None
        if decided is None:
            try:
                pushes.append(
                    await anyio.to_thread.run_sync(
                        lambda: ci_push.republish(workspace, task.path, ref, sleep=push_sleep)
                    )
                )
            except GitError as exc:
                return _publish_failed(
                    exc, waited, timing=_no_verdict_timing(pushed_at, node_started_at)
                )
            pushed_at = ports.now()
            verdict, read, again = await _wait_ci(
                ports, ref, head, gate_names, cfg, sleep, clock, running=running, ghosts=ghosts
            )
            waited += again
        else:
            verdict = decided
    else:
        try:
            pushes.append(await _push(lambda: workspace.publish_ci_ref(task.path, ref), push_sleep))
        except GitError as exc:
            return _publish_failed(exc, 0.0)
        pushed_at = ports.now()
        verdict, read, waited = await _wait_ci(
            ports, ref, head, gate_names, cfg, sleep, clock, running=running, ghosts=ghosts
        )
        if verdict.state == "unreachable" and verdict.cause == "no_run":
            # Once per call: a CI that never started on the first push is told that it may have
            # missed the event, and the second wait is bounded by the grace period again.
            try:
                pushes.append(
                    await anyio.to_thread.run_sync(
                        lambda: ci_push.republish(workspace, task.path, ref, sleep=push_sleep)
                    )
                )
            except GitError as exc:
                return _publish_failed(
                    exc, waited, timing=_no_verdict_timing(pushed_at, node_started_at)
                )
            pushed_at = ports.now()
            verdict, read, again = await _wait_ci(
                ports, ref, head, gate_names, cfg, sleep, clock, running=running, ghosts=ghosts
            )
            waited += again
    verdict, read, waited, rerun = await _rescued(
        state,
        cfg,
        ports,
        ref,
        head,
        gate_names,
        verdict,
        read,
        waited,
        sleep,
        clock,
        now,
        running,
        ghosts,
    )
    not_tried: _NotTried | None = None
    if verdict.state == "unreachable":
        # A push that got no run is a candidate for the dispatch of the upstream's workflow
        # (GH-531): only then is the upstream read. `ci.dispatch_after_no_run` decides, with
        # the availability that declaration gave.
        fallback: _Fallback | _NotTried | None = None
        if ci.push_got_no_run(verdict):
            fallback = await anyio.to_thread.run_sync(_fallback_dispatch, ports, state)
        if ci.dispatch_after_no_run(verdict, isinstance(fallback, _Fallback)):
            assert isinstance(fallback, _Fallback)  # available only when the declaration was read
            return await _dispatched_after_no_run(
                state,
                cfg,
                ports,
                task,
                head,
                fallback,
                pushes,
                node_started_at,
                waited,
                sleep,
                clock,
                push_sleep,
            )
        not_tried = fallback if isinstance(fallback, _NotTried) else None
        reported = ci.reported_names(read) if isinstance(read, CiReport) else ()
        return _NoVerdict(
            verdict.cause or "no_run",
            waited,
            unknown=verdict.unknown,
            reported=reported,
            timing=_no_verdict_timing(pushed_at, node_started_at),
            not_tried=not_tried,
        )
    assert isinstance(read, CiReport)  # a verdict other than `unreachable` came from a report
    if ci.ghost_dispatch_due(verdict, read, ghosts.ghosts(clock())):
        # GH-601: every live run of the head is a ghost (queued, no job, for the bound): a
        # mechanical failure of CI, mended by the upstream's workflow when it declares one.
        ghost_fallback = await anyio.to_thread.run_sync(_fallback_dispatch, ports, state)
        if isinstance(ghost_fallback, _Fallback):
            return await _dispatched_after_no_run(
                state,
                cfg,
                ports,
                task,
                head,
                ghost_fallback,
                pushes,
                node_started_at,
                waited,
                sleep,
                clock,
                push_sleep,
                cause="ghost",
                ghosts=ghosts,
                ghost_run_ids=sorted(read.running_runs),
            )
        return _NoVerdict(
            "run_unfinished",
            waited,
            timing=_no_verdict_timing(pushed_at, node_started_at),
            not_tried=ghost_fallback,
        )
    if (
        verdict.state == "timed_out"
        and read.running_runs
        and set(ci.ci_state(read, gate_names).missing) == set(gate_names)
    ):
        # GH-564: a live run that reported no declared job by the bound says nothing about the
        # suite: the CI is unusable for this head, not red. Never a return, a dispatch or a
        # republish (the run is alive); the human decides.
        return _NoVerdict(
            "run_unfinished",
            waited,
            timing=_no_verdict_timing(pushed_at, node_started_at),
        )
    match verdict.state:
        case "green":
            patch = _ci_green(state, head, gate_names, read, waited)
        case "red" | "timed_out":
            patch = _ci_red(state, cfg, head, gate_names, verdict, read, waited, at=ports.now())
        case "cancelled":
            # A second cancellation: never a return, the escalation of a CI that did not run.
            return _NoVerdict(
                "not_started",
                waited,
                timing=_no_verdict_timing(pushed_at, node_started_at),
                cancel_rerun=rerun,
            )
        case "waiting" | "unreachable":
            raise AssertionError(f"`{verdict.state}` is settled before a record is written")
        case _ as unreachable:
            assert_never(unreachable)
    if rerun is not None:
        patch["ci_cancel_rerun"] = rerun
    _with_timing(patch, pushed_at, node_started_at, verdict, read, gate_names)
    if speculative:
        patch["full_checks"]["speculative"] = True
    else:
        _with_pushes(patch, pushes)
    # The flake block of the jobs on this head: evidence only, it adds records and decides
    # nothing (the verdict above is already made).
    flakes = await anyio.to_thread.run_sync(
        ci_flakes.block_records, ports, ref, head, base, names, "full_checks", ci_flakes.now()
    )
    if flakes:
        patch["flakes"] = flakes
    return patch


async def _rescued(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    ref: str,
    head: str,
    names: tuple[str, ...],
    verdict: FullTierVerdict,
    read: CiReport | CiUnreadable,
    waited: float,
    sleep: Sleep,
    clock: Clock,
    now: Now,
    running: _Restart,
    ghosts: _Ghosts,
) -> tuple[FullTierVerdict, CiReport | CiUnreadable, float, CiCancelRerun | None]:
    """The rescue of a `cancelled` verdict (GH-539), which is never red: the run is rerun once per
    head (`ci_cancel_rerun`, written to the document at once) and the wait goes on. A `cancelled`
    verdict still standing is returned as it is, for the caller to escalate, once the rerun was
    refused or could not be asked (no run id), or is a second cancellation
    (`speculation.second_cancel`); a stale read of the run the rerun has yet to restart waits. Any
    other verdict is returned untouched, with the fact when one was written or already stood."""
    recorded = state.get("ci_cancel_rerun")
    rerun = recorded if recorded and rules.same_commit(recorded["head"], head) else None
    while verdict.state == "cancelled":
        if rerun is None:
            run_id = next(
                (found for c in verdict.cancelled if (found := ci.run_id_of_link(c.link))), None
            )
            if run_id is None:
                break
            refused = await anyio.to_thread.run_sync(ports.tracker.rerun_ci_run, run_id)
            if refused is not None:
                break
            rerun = {"head": head, "run_id": run_id, "rerun_at": ports.now()}
            ports.documents.save(apply(state, {"ci_cancel_rerun": rerun}))
            running.rearm(run_id)
        elif speculation.second_cancel(
            rerun["rerun_at"], running.seen, now(), cfg.ci_start_grace_s
        ):
            break
        await sleep(ci.CI_POLL_S)
        verdict, read, again = await _wait_ci(
            ports, ref, head, names, cfg, sleep, clock, running=running, ghosts=ghosts
        )
        waited += again
    return verdict, read, waited, rerun


def _fallback_dispatch(ports: Ports, state: TaskState) -> _Fallback | _NotTried:
    """What a push that got no run may be dispatched with (GH-531), read at the fetched upstream
    of the product checkout: the `ci_dispatch` and the `ci:` names at that one revision. This is
    the only place the no-run fallback reads the declaration, and the exception to the ADR-0016
    addendum (the stack is otherwise read at the task base): the workflow a dispatch runs is
    the default branch's own, so its declaration and the job names it reports come from the
    commit that workflow is. It assumes the upstream of the product checkout is the default
    branch, the one `dispatch_ci(workflow, "", ...)` runs. `upstream()` fetches and never
    raises; a stack that does not load there is a `_NotTried`, never an exception."""
    upstream = ports.workspace.upstream()
    if upstream.sha is None:
        return _NotTried("no_upstream")
    try:
        names = ports.stack.ci(upstream.sha)
        dispatch = ports.stack.ci_dispatch(upstream.sha)
    except ValueError:
        _LOG.warning("full_checks: the stack at the upstream %s did not load", upstream.sha)
        return _NotTried("stack_unreadable")
    if dispatch is None or not names:
        return _NotTried("undeclared", unfetched=upstream.note is not None)
    gate_names = rules.ci_gate_names(names, state.get("ci_required") or []).names
    return _Fallback(upstream.sha, dispatch, gate_names)


async def _dispatched_after_no_run(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    task: Checkout,
    head: str,
    fallback: _Fallback,
    pushes: list[Pushed],
    node_started_at: str,
    waited: float,
    sleep: Sleep,
    clock: Clock,
    push_sleep: PushSleep,
    *,
    cause: Literal["no_run", "ghost"] = "no_run",
    ghosts: _Ghosts | None = None,
    ghost_run_ids: Sequence[str] = (),
    ghost_fact: CiGhostDispatch | None = None,
    since: str | None = None,
) -> dict[str, Any] | _NoVerdict:
    """The head whose pushes got no run, judged by the upstream's dispatched workflow against
    the names read at that same revision (GH-531). The wait is the whole node's (`node_started_at`
    and `waited` carry the pushes'), the push rows stay on the record, and the record says why it
    was dispatched and at which revision the declaration was read. Neither decides anything:
    `rules.ci_untrusted` still comes from the paths.

    With `cause="ghost"` (GH-601) the head's live runs were all ghosts, and the record says so.
    The fact `ci_ghost_dispatch` is saved at once after an accepted dispatch (`ports.documents`),
    before the fresh run is waited on, and every patch that follows carries it. `ghost_fact` is
    the fact of an earlier node that is still in force: no dispatch, the wait starts at `since`
    (and dispatches only if the listing shows no run). `ghosts` also watches the dispatched run,
    which is itself a ghost when it stays queued with no job: that ends `run_unfinished`, not red."""
    held = _GhostFact(ghost_fact)

    def saved(dispatched_at: str) -> None:
        base = held.fact or {
            "head": head,
            "ghost_run_ids": list(ghost_run_ids),
            "dispatched_at": dispatched_at,
            "escalated": False,
        }
        held.fact = {**base, "dispatched_at": dispatched_at}
        ports.documents.save(apply(state, {"ci_ghost_dispatch": held.fact}))

    outcome = await _dispatched_full_checks(
        state,
        cfg,
        ports,
        task,
        head,
        fallback.gate_names,
        fallback.dispatch,
        sleep,
        clock,
        push_sleep,
        node_started_at=node_started_at,
        waited=waited,
        since=since,
        wait_first=ghost_fact is not None and since is not None,
        cause=cause,
        ghosts=ghosts,
        on_dispatched=saved if cause == "ghost" else None,
    )
    if isinstance(outcome, _NoVerdict):
        if held.fact is not None:
            return replace(outcome, ghost_dispatch=held.fact)
        return outcome
    _with_pushes(outcome, pushes)
    record = outcome["full_checks"]
    record["dispatch_cause"] = cause
    record["stack_at"] = fallback.sha
    if held.fact is not None:
        outcome["ci_ghost_dispatch"] = held.fact
    return outcome


async def _speculated_wait(
    pushed_at: str,
    cfg: Configuration,
    ports: Ports,
    ref: str,
    head: str,
    names: tuple[str, ...],
    sleep: Sleep,
    clock: Clock,
    now: Now,
    running: _Restart | None = None,
    ghosts: _Ghosts | None = None,
) -> tuple[FullTierVerdict | None, CiReport | CiUnreadable, float]:
    """The wait on the run a review pushed for `head` (GH-465), from one read of the ref. The
    verdict is `None` when that run cannot be counted on and the caller publishes again: the
    event never started a run (`speculation.event_lost`), or the run was cancelled or never
    appeared, which is not a verdict of the suite (`no_run`, `not_started`). Any other end,
    unreadable CI included, is the verdict as the wait gave it."""
    first = await anyio.to_thread.run_sync(ports.tracker.watched_branch_ci, ref)
    has_run = (
        isinstance(first, CiReport)
        and rules.same_commit(first.head_sha, head)
        and any(ci.runs_of(ci.current_runs(first), names).values())
    )
    if speculation.event_lost(pushed_at, now(), cfg.ci_start_grace_s, has_run):
        if ghosts is not None:
            ghosts.watch(first, clock())
        return None, first, 0.0
    verdict, read, waited = await _wait_ci(
        ports, ref, head, names, cfg, sleep, clock, first, running, ghosts
    )
    if verdict.state == "unreachable" and verdict.cause in ("no_run", "not_started"):
        return None, read, waited
    return verdict, read, waited


async def _wait_ci(
    ports: Ports,
    ref: str,
    head: str,
    names: tuple[str, ...],
    cfg: Configuration,
    sleep: Sleep,
    clock: Clock,
    first: CiReport | CiUnreadable | None = None,
    running: _Restart | None = None,
    ghosts: _Ghosts | None = None,
) -> tuple[FullTierVerdict, CiReport | CiUnreadable, float]:
    """Read the CI of `ref` every `CI_POLL_S` until a verdict that is not `waiting`, counted
    from the first read: bounded by `cfg.ci_start_grace_s` while no run exists on `head` and by
    `cfg.check_timeout_s` once one does (`ci.full_tier_verdict`). `first` is a read already
    taken, used for the first round instead of asking again. `running` notes whether a read
    showed the rerun's run outside `COMPLETED` (GH-539), for the rescue of a cancelled run.
    `ghosts` is the node's watch (GH-601): every read is observed on the node's clock, and a ghost
    still maturing extends the bound up to twice the timeout of the whole node (`full_tier_verdict`
    with `maturing`). The read is `watched_branch_ci`: `branch_ci` plus the queued empty runs."""
    started = clock()
    previous: CiReport | CiUnreadable | None = None
    while True:
        read = (
            first
            if first is not None
            else await anyio.to_thread.run_sync(ports.tracker.watched_branch_ci, ref)
        )
        first = None
        at_s = clock()
        waited = at_s - started
        if running is not None:
            running.watch(read)
        extension: dict[str, Any] = {}
        if ghosts is not None:
            ghosts.watch(read, at_s)
            extension = {
                "maturing": ghosts.maturing(at_s),
                "watched": ghosts.known(),
                "ceiling_elapsed_s": ghosts.elapsed(at_s),
            }
        verdict = ci.full_tier_verdict(
            read,
            head,
            names,
            waited,
            cfg.check_timeout_s,
            cfg.ci_start_grace_s,
            _same_complete_read(previous, read, head),
            **extension,
        )
        if verdict.state != "waiting":
            return verdict, read, waited
        previous = read
        await sleep(ci.CI_POLL_S)


def _same_complete_read(
    previous: CiReport | CiUnreadable | None, read: CiReport | CiUnreadable, head: str
) -> bool:
    """Whether two consecutive reads agree that `head`'s workflow runs are completed and carry
    the same check names (GH-485): the one confirmation `ci.unreported` asks for, so a name that
    appears late is waited for. Held in memory by the wait, never stored."""
    return (
        isinstance(previous, CiReport)
        and isinstance(read, CiReport)
        and rules.same_commit(previous.head_sha, head)
        and rules.same_commit(read.head_sha, head)
        and previous.runs_completed is True
        and read.runs_completed is True
        and set(ci.reported_names(previous)) == set(ci.reported_names(read))
    )


async def _dispatched_full_checks(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    task: Checkout,
    head: str,
    names: tuple[str, ...],
    dispatch: CiDispatch,
    sleep: Sleep,
    clock: Clock,
    push_sleep: PushSleep,
    *,
    node_started_at: str | None = None,
    waited: float = 0.0,
    since: str | None = None,
    wait_first: bool = False,
    cause: Literal["no_run", "ghost"] = "no_run",
    ghosts: _Ghosts | None = None,
    on_dispatched: Callable[[str], None] | None = None,
) -> dict[str, Any] | _NoVerdict:
    """An untrusted head judged by the base branch's own workflow (GH-462): dispatched on the
    repository's default branch, the one the pull request targets, with the head sha as its
    input. A refusal ends the call at once: no wait, no second dispatch. No run after the
    grace period is dispatched once more, and the second wait is bounded by the grace again.

    Before every dispatch (GH-504) the head is published to `rules.ci_dispatch_ref`, the ref
    GitHub serves the checkout from, retried like every push of the CI path (GH-487); a push that
    still fails ends the call with no dispatch. That ref is never deleted here: the sweep is the
    only thing that does.

    `node_started_at` and `waited` are those of a node that already waited on the push of the
    task's own CI ref (GH-531), so the timing and `waited_s` cover the whole wait.

    The ghost dispatch (GH-601) uses the rest: `wait_first` skips the first dispatch and waits on
    the runs listed from `since` (a node retaken after a dispatch), `on_dispatched` is called with
    the instant of every accepted dispatch before its run is waited on, and with `cause="ghost"`
    and `ghosts` a dispatched run that is itself a ghost at the bound ends `run_unfinished` (no
    red record, no second dispatch). Without these arguments the behaviour is exactly the
    original's."""
    node_started_at = node_started_at or ports.now()
    pushed_at: str | None = None
    skip_dispatch = wait_first and since is not None
    listed_since = since or ""
    for attempt in range(1, ci.CI_DISPATCH_ATTEMPTS + 1):
        if skip_dispatch:
            skip_dispatch = False
        else:
            dispatched = await _dispatch_once(
                state, ports, task, head, dispatch, push_sleep, waited, node_started_at, pushed_at
            )
            if isinstance(dispatched, _NoVerdict):
                return dispatched
            pushed_at, listed_since = dispatched
            if on_dispatched is not None:
                on_dispatched(pushed_at)
        verdict, runs, took = await _wait_dispatched(
            ports,
            dispatch.workflow,
            listed_since,
            head,
            names,
            cfg,
            sleep,
            clock,
            ghosts if cause == "ghost" else None,
        )
        waited += took
        if (
            not (verdict.state == "unreachable" and verdict.cause == "no_run")
            or attempt == ci.CI_DISPATCH_ATTEMPTS
        ):
            break
    if (
        ghosts is not None
        and cause == "ghost"
        and ci.dispatched_ghost(verdict, runs, head, ghosts.ghosts(clock()))
    ):
        # GH-601: the fresh run is a ghost too: no verdict of the suite, never red, and no
        # second dispatch; the human decides.
        return _NoVerdict(
            "run_unfinished",
            waited,
            dispatch.workflow,
            head=head,
            timing=_no_verdict_timing(pushed_at, node_started_at),
        )
    if verdict.state == "unreachable":
        return _NoVerdict(
            verdict.cause or "no_run",
            waited,
            dispatch.workflow,
            head=head,
            stage=_stage_of(verdict),
            timing=_no_verdict_timing(pushed_at, node_started_at),
        )
    run = ci.chosen_run(runs, head) if isinstance(runs, tuple) else None
    assert run is not None  # a verdict other than `unreachable` came from a chosen run
    read = CiReport(head, None, None, None, run.jobs, run.status == "completed")
    match verdict.state:
        case "green":
            patch = _ci_green(state, head, names, read, waited)
        case "red" | "timed_out":
            patch = _ci_red(state, cfg, head, names, verdict, read, waited, at=ports.now())
        case "cancelled":
            # A dispatched run is judged from one run, so this is not reached today; it still is
            # never read as red (GH-539).
            return _NoVerdict(
                "not_started",
                waited,
                dispatch.workflow,
                head=head,
                timing=_no_verdict_timing(pushed_at, node_started_at),
            )
        case "waiting" | "unreachable":
            raise AssertionError(f"`{verdict.state}` is settled before a record is written")
        case _ as unreachable:
            assert_never(unreachable)
    _with_timing(patch, pushed_at, node_started_at, verdict, read, names)
    return _dispatched_record(patch, run)


async def _dispatch_once(
    state: TaskState,
    ports: Ports,
    task: Checkout,
    head: str,
    dispatch: CiDispatch,
    push_sleep: PushSleep,
    waited: float,
    node_started_at: str,
    pushed_at: str | None,
) -> tuple[str, str] | _NoVerdict:
    """One dispatch of the base branch's workflow for `head` (GH-504, GH-462): the head published
    to the dispatch ref, retried like every push, then the dispatch itself. `(pushed_at, since)`
    is the instant of the accepted dispatch and the loose lower bound of the listing that finds its
    run; the `_NoVerdict` is a push that still failed or a refusal, with no wait. `pushed_at` is
    the previous attempt's, for the timing of a failure before the new instant exists."""
    # A loose lower bound for the listing only: the run is found by its event, workflow
    # and the sha in its title, never by comparing this instant with GitHub's.
    since = (datetime.now(UTC) - timedelta(seconds=ci.CI_DISPATCH_SKEW_S)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        await _push(
            lambda: ports.workspace.publish_dispatch_ref(task.path, state["id"]), push_sleep
        )
    except GitError as exc:
        return _publish_failed(
            exc, waited, dispatch.workflow, head, _no_verdict_timing(pushed_at, node_started_at)
        )
    dispatched_at = ports.now()
    refused = await anyio.to_thread.run_sync(
        ports.tracker.dispatch_ci, dispatch.workflow, "", {dispatch.sha_input: head}
    )
    if refused is not None:
        return _NoVerdict(
            refused.cause,
            waited,
            dispatch.workflow,
            timing=_no_verdict_timing(dispatched_at, node_started_at),
        )
    return dispatched_at, since


def _since_of(dispatched_at: str) -> str | None:
    """The loose lower bound of the listing for a dispatch made at `dispatched_at` (GH-601):
    that instant minus `CI_DISPATCH_SKEW_S`, formatted like a fresh dispatch's. `None` for an
    instant that cannot be read, so the fact is not in force. It narrows the listing only."""
    try:
        instant = datetime.fromisoformat(dispatched_at)
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return (instant - timedelta(seconds=ci.CI_DISPATCH_SKEW_S)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _no_verdict_timing(pushed_at: str | None, node_started_at: str) -> CiTiming | None:
    """The timing of a wait that gave no verdict (GH-467): only the two instants, no run
    instants (`ci.timing`); `None` when nothing was pushed, so there is no wait to measure."""
    if pushed_at is None:
        return None
    return ci.timing(pushed_at, node_started_at, "no_verdict", (), ())


def _with_timing(
    patch: dict[str, Any],
    pushed_at: str | None,
    node_started_at: str,
    verdict: FullTierVerdict,
    read: CiReport,
    names: tuple[str, ...],
) -> None:
    """Write the instants of the wait onto the decided record, after the verdict is made and
    read by nothing that decides (GH-467). A bound that ran out (`timed_out`) is no verdict of
    the CI, so it reads `no_verdict` and carries no run end. Nothing is written when no push
    instant is known."""
    if pushed_at is None:
        return
    label: Literal["green", "red", "no_verdict"]
    match verdict.state:
        case "green":
            label = "green"
        case "red":
            label = "red"
        case "timed_out" | "waiting" | "unreachable" | "cancelled":
            label = "no_verdict"
        case _ as unreachable:
            assert_never(unreachable)
    patch["full_checks"]["ci_timing"] = ci.timing(
        pushed_at, node_started_at, label, read.checks, names
    )


def _stage_of(verdict: FullTierVerdict) -> FailedStage | None:
    """Where the failed job(s) of a `checkout_failed` verdict stopped (GH-504): the stage of the
    first one, for the message; `None` for any other verdict."""
    if verdict.cause != "checkout_failed":
        return None
    return next((c.failed_stage for c in verdict.failing if c.failed_stage), None)


async def _wait_dispatched(
    ports: Ports,
    workflow: str,
    since: str,
    head: str,
    names: tuple[str, ...],
    cfg: Configuration,
    sleep: Sleep,
    clock: Clock,
    ghosts: _Ghosts | None = None,
) -> tuple[FullTierVerdict, tuple[DispatchedRun, ...] | CiUnreadable, float]:
    """`_wait_ci` for a dispatched run: the listing is read every `CI_POLL_S` until a verdict
    that is not `waiting`, bounded by the grace period while no run of `head` is listed and by
    `cfg.check_timeout_s` once one is. An unreadable listing keeps the check timeout.
    `ghosts` (GH-601) watches the run that judges `head`: the first listing is observed at the
    wait's start, so a run queued with no job from then on is a ghost at the bound, and the
    later ones at the instant they were read."""
    started = clock()
    first_read = True
    while True:
        read = await anyio.to_thread.run_sync(ports.tracker.dispatched_ci, workflow, since)
        at_s = clock()
        waited = at_s - started
        if ghosts is not None and not isinstance(read, CiUnreadable):
            ghosts.watch_dispatched(read, head, started if first_read else at_s)
        first_read = False
        if isinstance(read, CiUnreadable):
            verdict = ci.full_tier_verdict(
                read, head, names, waited, cfg.check_timeout_s, cfg.ci_start_grace_s
            )
        else:
            verdict = ci.dispatch_verdict(
                read, head, names, waited, cfg.ci_start_grace_s, cfg.check_timeout_s
            )
        if verdict.state != "waiting":
            return verdict, read, waited
        await sleep(ci.CI_POLL_S)


def _dispatched_record(patch: dict[str, Any], run: DispatchedRun) -> dict[str, Any]:
    """The record of a dispatched run says so and names the run (GH-462)."""
    record = patch["full_checks"]
    record["ran_on"] = "ci_dispatch"
    record["run_id"] = run.run_id
    return patch


def _ci_record(
    state: TaskState,
    head: str,
    passed: bool,
    waited: float,
    results: list[FullCheckEntry],
) -> FullChecksRecord:
    previous = state.get("full_checks")
    return {
        "version": previous["version"] + 1 if previous else 1,
        "head": head,
        "passed": passed,
        "duration_s": round(waited, 1),
        "declared": {"name": "ci"},
        "results": results,
        "witness": None,
        "ran_on": "ci",
    }


def _link(report: CiReport, name: str) -> str:
    """The link of the first run the declared `name` claims that has one (GH-535)."""
    return next(
        (check.link for check in ci.runs_of(ci.current_runs(report), (name,))[name] if check.link),
        "",
    )


def _ci_green(
    state: TaskState, head: str, names: tuple[str, ...], read: CiReport, waited: float
) -> dict[str, Any]:
    record = _ci_record(
        state, head, True, waited, [{"name": name, "passed": True} for name in names]
    )
    links = "; ".join(f"{name}: {link}" for name in names if (link := _link(read, name)))
    text = f"Passed on CI for `{head}`: {', '.join(names)}."
    if links:
        text += f" Runs: {links}."
    return {
        "full_checks": record,
        "sections": _section(state, record["version"], text),
    }


def _ci_red(
    state: TaskState,
    cfg: Configuration,
    head: str,
    names: tuple[str, ...],
    verdict: FullTierVerdict,
    read: CiReport,
    waited: float,
    *,
    at: str,
) -> dict[str, Any]:
    """A red job, or a bound that ran out. A job still pending beside a red one is not a
    failure of its own and gets no row; at the bound it does, and the section names it."""
    timed_out = verdict.state == "timed_out"
    red = {name: runs[0] for name, runs in reversed(verdict.red)}
    results: list[FullCheckEntry] = []
    for name in names:
        if name in red:
            results.append({"name": name, "passed": False, "link": red[name].link})
        elif name not in verdict.pending:
            results.append({"name": name, "passed": True})
        elif timed_out:
            results.append({"name": name, "passed": False})
    record = _ci_record(state, head, False, waited, results)
    record["failed_step"] = next(iter(red), "timeout")
    lines = [f"Failed on CI for `{head}`."]
    lines += [_red_line(check) for check in verdict.failing]
    if timed_out:
        lines.append(
            f"Not finished within {cfg.check_timeout_s:g}s: still pending "
            f"{', '.join(verdict.pending)}."
        )
    lines.append(_REPRODUCE)
    patch = rules.full_checks_return(
        state, record, cfg.max_changes_requested_per_pair, at=at, mode=cfg.review_mode
    )
    patch["sections"] = _section(state, record["version"], "\n\n".join(lines))
    return patch


def _red_line(check: CiCheck) -> str:
    return f"- `{check.name}`: {check.conclusion}" + (f" ({check.link})" if check.link else "")


def _record(
    state: TaskState,
    merged: dict[str, object],
    declared: dict[str, str],
    gate: CheckoutPresubmit,
    deferred: list[str],
) -> FullChecksRecord:
    previous = state.get("full_checks")
    record: FullChecksRecord = {
        "version": previous["version"] + 1 if previous else 1,
        "head": str(merged["head"]),
        "passed": merged["passed"] is True,
        "duration_s": float(str(merged["duration_s"])),
        "declared": dict(declared),
        "results": _without_output(_ENTRIES.validate_python(merged["checks"]), deferred),
        "witness": dict(gate.witness_result) if gate.witness_result is not None else None,
    }
    if not record["passed"]:
        record["failed_step"] = str(merged["failed_step"])
    if (taken := gate.gate) is not None:
        record["gate"] = taken
    if deferred:
        record["deferred"] = deferred
    return record


def _deferrals(
    results: list[CheckResult] | None,
    fallback: FallbackCause | None,
    covers: Mapping[str, CiCover],
) -> tuple[list[CheckResult] | None, list[str]]:
    """The rows with `deferred: true` on each check whose last row `presubmit.deferred_verdict`
    defers (GH-453), and those checks' names in the order they ran. The one place the
    verdict is asked; the rows stay the gate's own, never stored."""
    if results is None or fallback is None or not covers:
        return results, []
    chosen = {
        id(row): row["name"]
        for row in presubmit.final_results(results)
        if presubmit.deferred_verdict(row, "full_checks", fallback, covers.get(row["name"]))
    }
    marked: list[CheckResult] = [
        {**row, "deferred": True} if id(row) in chosen else row for row in results
    ]
    return marked, list(chosen.values())


def _deferred_line(name: str, cover: CiCover, timeout_s: float) -> str:
    return f"{name}: timed out after {timeout_s:g}s, deferred to CI job `{cover.job}`"


def _without_deferred(merged: dict[str, object], deferred: list[str]) -> dict[str, object]:
    """`merged` without the entries of the deferred checks: they are no failure, so the
    failure text never names them."""
    checks = merged["checks"]
    assert isinstance(checks, list)  # `merge_checks_record` always writes the list
    return {**merged, "checks": [c for c in checks if c["name"] not in deferred]}


def _shown_up(seconds: float) -> float:
    """A breach's duration to a tenth, rounded up: the verdict compared the raw figure, so
    10.04s against a 10s budget must not be written as `10.0s / 10.0s`, which reads as no
    breach."""
    return math.ceil(round(seconds * 10, 6)) / 10


def _over_budget_sentence(breaches: list[presubmit.BudgetBreach]) -> str:
    """`Over budget: <name> <N>s / <M>s, ...`: declared names and numbers only."""
    return (
        "Over budget: "
        + ", ".join(f"{b['name']} {b['duration_s']}s / {b['budget_s']}s" for b in breaches)
        + "."
    )


def _shown(name: str, reused: set[str], flaked: set[str]) -> str:
    """A check in the passing section: a reused one says so, and a flake says `flake` (red
    on its first run, green on the one rerun), never plain passed (ADR-0066)."""
    marks = [mark for mark, held in (("reused", name in reused), ("flake", name in flaked)) if held]
    return f"{name} ({', '.join(marks)})" if marks else name


def _without_output(
    entries: list[FullCheckEntry], deferred: Collection[str] = ()
) -> list[FullCheckEntry]:
    """The name and verdict of each check: the record holds no command output (the `tail` the
    merge record keeps is free text); the `Full checks` section is where it is read. A reused
    row keeps its `reused: true` (ADR-0065) and a flaked one its `flake: true` (ADR-0066); a
    check with a sample keeps its `duration_s`, the number `check_durations` summed."""
    kept: list[FullCheckEntry] = []
    for entry in entries:
        row: FullCheckEntry = {"name": entry["name"], "passed": entry["passed"]}
        if entry.get("reused"):
            row["reused"] = True
        if entry.get("flake"):
            row["flake"] = True
        if entry["name"] in deferred:
            row["deferred"] = True
        if "duration_s" in entry:
            row["duration_s"] = entry["duration_s"]
        kept.append(row)
    return kept


def _section(state: TaskState, version: int, text: str) -> dict[str, str]:
    """The new section, and its previous run kept as history under the superseded name."""
    sections = {SECTION: text}
    previous = state.get("sections", {}).get(SECTION)
    if previous is not None:
        # The key is read from the section names, never from the text: a run that escalated
        # wrote a section and no record, so `version - 1` alone could name a key in use.
        free = rules.next_section_version(state, SECTION)
        sections[rules.superseded(SECTION, max(version - 1, free))] = previous
    return sections


def dirty_patch(branch: str, *, at: str) -> dict[str, Any]:
    """The task checkout holds uncommitted work the suite would be run over: no command
    runs, and nothing is cleaned, which could throw away what a human left there."""
    return rules.logged(
        {
            "current_role": DEVELOPER_INTEGRATOR.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation(
                "setup",
                f"the task checkout of {branch!r} has uncommitted changes, so the full suite "
                "would not be a run of the integrated head. Commit or discard them and resume.",
            ),
        },
        at=at,
    )
