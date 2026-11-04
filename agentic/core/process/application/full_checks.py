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
once more.

In that run a check that timed out and that the stack maps to a CI job (`ci_covers`, GH-453)
is deferred when `presubmit.deferred_verdict` says so: its row is marked `deferred`, it is no
failure and no pass, and the merge asks that job for the verdict."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import anyio
from pydantic import TypeAdapter

from agentic.shared.configuration import Configuration

from ..domain import ci, presubmit, rules, speculation
from ..domain.check_schedule import CheckPriority
from ..domain.ci import CiCheck, CiReport, CiUnreadable, DispatchedRun, FullTierVerdict
from ..domain.ci_push import PushKind, push_failure_markdown
from ..domain.presubmit import CheckResult, CiCover, CiDispatch
from ..domain.roles import DEVELOPER_INTEGRATOR
from ..domain.task import (
    WAITING_ON_HUMAN,
    CiCause,
    FallbackCause,
    FullCheckEntry,
    FullChecksRecord,
    TaskState,
)
from . import ci_flakes, ci_push
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
}
# The same causes when the verdict was asked of a dispatched run: `{ref}` is the workflow file.
_DISPATCH_DETAIL: dict[CiCause, str] = {
    **_CI_DETAIL,
    "gh_unavailable": "`gh` could not be run for the whole {min:g} min wait on {ref}",
    "gh_unreadable": "`gh` answered something unreadable for the whole {min:g} min wait on {ref}",
    "no_run": "no dispatched run of {ref} for that head after {min:g} min, though it was "
    "dispatched twice",
    "not_started": "the declared CI jobs of the dispatched {ref} did not start "
    "(startup failure or cancelled)",
    "dispatch_refused": "`gh` refused to dispatch {ref} for that head",
    "jobs_missing": "the dispatched {ref} completed without a declared job",
    "unknown_names": "no check run carries a declared job name",
}


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


Sleep = Callable[[float], Awaitable[None]]
PushSleep = Callable[[float], None]
Clock = Callable[[], float]
Now = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _head_trust(ports: Ports, checkout: Checkout, base: str) -> tuple[str, bool]:
    """The integrated head and whether the product's own CI may judge it: `False` when the
    change touches the CI definition or the diff cannot be read (fail closed,
    `rules.ci_untrusted`). The one place the two are read, for the node and for
    `full_checks_mode`."""
    head = ports.workspace.full_head(checkout.path)
    try:
        paths: tuple[str, ...] | None = ports.workspace.changed_paths(base, head)
    except GitError:
        paths = None
    return head, rules.ci_untrusted(paths) is False


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
    be worth running beside the roles."""
    base = task_base(state, ports)
    if not ports.stack.ci(base):
        return "local"
    head, trusted = _head_trust(ports, checkout, base)
    if not trusted:
        return "wait" if ports.stack.ci_dispatch(base) is not None else "local"
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
    if outcome == "untrusted":
        return await _local_full_checks(state, cfg, ports, task, base, outcome)
    if isinstance(outcome, _NoVerdict) and outcome.cause == "unknown_names":
        head = workspace.full_head(task.path)
        plan_only = rules.ci_gate_names(names, state.get("ci_required") or []).plan_only
        return finish(
            state, rules.ci_planning_defect(head, _unknown_text(outcome, head, plan_only)), ports
        )
    if isinstance(outcome, _NoVerdict):
        head = workspace.full_head(task.path)
        table = _CI_DETAIL if outcome.workflow is None else _DISPATCH_DETAIL
        ref = outcome.workflow or rules.ci_ref(state["id"])
        detail = table[outcome.cause].format(ref=ref, min=round(outcome.waited_s / 60, 1))
        patch = rules.ci_unavailable(outcome.cause, head, detail + _git_said(outcome))
        if outcome.git_said and outcome.push_attempts and outcome.push_kind:
            # Git's words reach the section only through `push_failure_markdown`, which
            # scrubs them; the older run stays as history under its own free `(vN)` key.
            patch["sections"] = _section(
                state,
                0,
                push_failure_markdown(
                    outcome.cause, ref, outcome.push_attempts, outcome.push_kind, outcome.git_said
                ),
            )
        return finish(state, patch, ports)
    return finish(state, outcome, ports)


def _git_said(outcome: _NoVerdict) -> str:
    """What git said, as the tail of the escalation's detail. Added after the `str.format` of
    the detail, never inside it: git's text is a third party's and may hold braces."""
    if not (outcome.git_said and outcome.push_attempts and outcome.push_kind):
        return ""
    tries = f"{outcome.push_attempts} attempt(s) ({outcome.push_kind})"
    return f" after {tries}; git said: {outcome.git_said}"


def _publish_failed(exc: GitError, waited: float) -> _NoVerdict:
    """The no-verdict of a push that failed. A `PushFailed` brings what git said; a `GitError`
    that is not one is today's bare `publish_failed`."""
    if isinstance(exc, PushFailed):
        return _NoVerdict(
            "publish_failed",
            waited,
            git_said=exc.said,
            push_attempts=exc.attempts,
            push_kind=exc.kind,
        )
    return _NoVerdict("publish_failed", waited)


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
) -> TaskState:
    """The suite run on this machine. `fallback` is why a `ci:` product ended up here: the
    record and the section carry it. `None` for a product without `ci:`, whose record gets
    no new key."""
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
        patch = rules.full_checks_return(state, record, cfg.max_changes_requested_per_pair)
        text = text or f"Failed on `{head}`: no check output was recorded."
        if lines:
            text += "\n\n" + "\n".join(lines)
    if fallback is not None:
        record["ran_on"] = "local"
        record["ci_fallback"] = fallback
        text = f"CI not used ({fallback}): ran locally.\n\n{text}"
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
) -> dict[str, Any] | _NoVerdict | Literal["untrusted"]:
    """The patch of a run decided on GitHub, `untrusted` for running the local tier, or the
    `_NoVerdict` GitHub gave no verdict for. When no run starts within the grace period the
    ref is published once more (GH-462). The
    paths are read from the workspace only and the head is published to the task's own CI
    ref; the wait takes no check slot and runs no command (ADR-0070). Each push is retried
    by what git said (GH-487); the one that needed it is noted on the record."""
    workspace = ports.workspace
    head, trusted = _head_trust(ports, task, base)
    gate_names = rules.ci_gate_names(names, state.get("ci_required") or []).names
    if not trusted:
        if (dispatch := ports.stack.ci_dispatch(base)) is None:
            return "untrusted"
        return await _dispatched_full_checks(
            state, cfg, ports, head, gate_names, dispatch, sleep, clock
        )
    ref = rules.ci_ref(state["id"])
    speculative = False
    pushes: list[Pushed] = []
    pushed = state.get("ci_speculation")
    if pushed and _pushed_for(state, head):
        # A review already pushed this very commit (GH-465): its run is waited for, not
        # pushed again. A run that never came, or was cancelled, is today's path from the
        # republish on, and only the wait that follows it may escalate.
        decided, read, waited = await _speculated_wait(
            pushed["pushed_at"], cfg, ports, ref, head, gate_names, sleep, clock, now
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
                return _publish_failed(exc, waited)
            verdict, read, again = await _wait_ci(ports, ref, head, gate_names, cfg, sleep, clock)
            waited += again
        else:
            verdict = decided
    else:
        try:
            pushes.append(await _push(lambda: workspace.publish_ci_ref(task.path, ref), push_sleep))
        except GitError as exc:
            return _publish_failed(exc, 0.0)
        verdict, read, waited = await _wait_ci(ports, ref, head, gate_names, cfg, sleep, clock)
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
                return _publish_failed(exc, waited)
            verdict, read, again = await _wait_ci(ports, ref, head, gate_names, cfg, sleep, clock)
            waited += again
    if verdict.state == "unreachable":
        reported = ci.reported_names(read) if isinstance(read, CiReport) else ()
        return _NoVerdict(
            verdict.cause or "no_run", waited, unknown=verdict.unknown, reported=reported
        )
    assert isinstance(read, CiReport)  # a verdict other than `unreachable` came from a report
    if verdict.state == "green":
        patch = _ci_green(state, head, gate_names, read, waited)
    else:
        patch = _ci_red(state, cfg, head, gate_names, verdict, read, waited)
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
) -> tuple[FullTierVerdict | None, CiReport | CiUnreadable, float]:
    """The wait on the run a review pushed for `head` (GH-465), from one read of the ref. The
    verdict is `None` when that run cannot be counted on and the caller publishes again: the
    event never started a run (`speculation.event_lost`), or the run was cancelled or never
    appeared, which is not a verdict of the suite (`no_run`, `not_started`). Any other end,
    unreadable CI included, is the verdict as the wait gave it."""
    first = await anyio.to_thread.run_sync(ports.tracker.branch_ci, ref)
    has_run = (
        isinstance(first, CiReport)
        and rules.same_commit(first.head_sha, head)
        and any(check.name in names for check in first.checks)
    )
    if speculation.event_lost(pushed_at, now(), cfg.ci_start_grace_s, has_run):
        return None, first, 0.0
    verdict, read, waited = await _wait_ci(ports, ref, head, names, cfg, sleep, clock, first)
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
) -> tuple[FullTierVerdict, CiReport | CiUnreadable, float]:
    """Read the CI of `ref` every `CI_POLL_S` until a verdict that is not `waiting`, counted
    from the first read: bounded by `cfg.ci_start_grace_s` while no run exists on `head` and by
    `cfg.check_timeout_s` once one does (`ci.full_tier_verdict`). `first` is a read already
    taken, used for the first round instead of asking again."""
    started = clock()
    previous: CiReport | CiUnreadable | None = None
    while True:
        read = (
            first
            if first is not None
            else await anyio.to_thread.run_sync(ports.tracker.branch_ci, ref)
        )
        first = None
        waited = clock() - started
        verdict = ci.full_tier_verdict(
            read,
            head,
            names,
            waited,
            cfg.check_timeout_s,
            cfg.ci_start_grace_s,
            _same_complete_read(previous, read, head),
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
    head: str,
    names: tuple[str, ...],
    dispatch: CiDispatch,
    sleep: Sleep,
    clock: Clock,
) -> dict[str, Any] | _NoVerdict:
    """An untrusted head judged by the base branch's own workflow (GH-462): dispatched on the
    repository's default branch, the one the pull request targets, with the head sha as its
    input. A refusal ends the call at once: no wait, no second dispatch. No run after the
    grace period is dispatched once more, and the second wait is bounded by the grace again."""
    waited = 0.0
    for attempt in range(1, ci.CI_DISPATCH_ATTEMPTS + 1):
        # A loose lower bound for the listing only: the run is found by its event, workflow
        # and the sha in its title, never by comparing this instant with GitHub's.
        since = (datetime.now(UTC) - timedelta(seconds=ci.CI_DISPATCH_SKEW_S)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        refused = await anyio.to_thread.run_sync(
            ports.tracker.dispatch_ci, dispatch.workflow, "", {dispatch.sha_input: head}
        )
        if refused is not None:
            return _NoVerdict(refused.cause, waited, dispatch.workflow)
        verdict, runs, took = await _wait_dispatched(
            ports, dispatch.workflow, since, head, names, cfg, sleep, clock
        )
        waited += took
        if (
            not (verdict.state == "unreachable" and verdict.cause == "no_run")
            or attempt == ci.CI_DISPATCH_ATTEMPTS
        ):
            break
    if verdict.state == "unreachable":
        return _NoVerdict(verdict.cause or "no_run", waited, dispatch.workflow)
    run = ci.chosen_run(runs, head) if isinstance(runs, tuple) else None
    assert run is not None  # a verdict other than `unreachable` came from a chosen run
    read = CiReport(head, None, None, None, run.jobs, run.status == "completed")
    if verdict.state == "green":
        patch = _ci_green(state, head, names, read, waited)
    else:
        patch = _ci_red(state, cfg, head, names, verdict, read, waited)
    return _dispatched_record(patch, run)


async def _wait_dispatched(
    ports: Ports,
    workflow: str,
    since: str,
    head: str,
    names: tuple[str, ...],
    cfg: Configuration,
    sleep: Sleep,
    clock: Clock,
) -> tuple[FullTierVerdict, tuple[DispatchedRun, ...] | CiUnreadable, float]:
    """`_wait_ci` for a dispatched run: the listing is read every `CI_POLL_S` until a verdict
    that is not `waiting`, bounded by the grace period while no run of `head` is listed and by
    `cfg.check_timeout_s` once one is. An unreadable listing keeps the check timeout."""
    started = clock()
    while True:
        read = await anyio.to_thread.run_sync(ports.tracker.dispatched_ci, workflow, since)
        waited = clock() - started
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
    return next((check.link for check in report.checks if check.name == name and check.link), "")


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
) -> dict[str, Any]:
    """A red job, or a bound that ran out. A job still pending beside a red one is not a
    failure of its own and gets no row; at the bound it does, and the section names it."""
    timed_out = verdict.state == "timed_out"
    red = {check.name: check for check in reversed(verdict.failing)}
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
    patch = rules.full_checks_return(state, record, cfg.max_changes_requested_per_pair)
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


def dirty_patch(branch: str) -> dict[str, Any]:
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
        }
    )
