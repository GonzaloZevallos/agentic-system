"""The landing steps that act without moving the head by themselves (GH-239, ADR-0048):
`fix`, `rerun`, `file_followups_and_land` and `diagnose`.

Each executor receives the step `landing.land` already recorded for the head, so none of
them decides again what to do about the refusal."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime

from ..domain import landing as landing_rules
from ..domain import presubmit, rules
from ..domain.roles import LANDING_DIAGNOSER
from ..domain.task import ChangeCategory, TaskState, apply
from . import escalate, landing_plan, landing_sync, merge
from .checkouts import task_base
from .landing_plan import LandingContext
from .resume import state_from
from .run_role import run_role

FOLLOWUP_LABEL = "agentic"


def _state(context: LandingContext) -> TaskState | None:
    """The task document as a state, or `None` when there is none."""
    loaded = context.ports.documents.load(context.task_id)
    return state_from(loaded) if loaded is not None else None


def _blocking_reason(raw: TaskState) -> tuple[str, ChangeCategory]:
    rows = [row for row in rules.open_findings(raw) if row["severity"] == "blocking"]
    lines = [f"- {row['location']}: {row['claim']}" for row in rows]
    category: ChangeCategory = rows[0]["category"] if rows else "process-suite-red"
    return "Open blocking findings:\n" + "\n".join(lines), category


def _red_reason(context: LandingContext, document: Mapping[str, object]) -> str:
    """The failing check names with their tails: the recorded local checks, or the CI runs.
    Read from the document: `merge_checks` is a key of it, not of the state."""
    recorded = document.get("merge_checks")
    record: Mapping[str, object] = recorded if isinstance(recorded, Mapping) else {}
    if context.step.refusal == "checks_failed" and record:
        return presubmit.failed_checks_markdown(record, context.head, neutralise=False)
    verdict = context.facts.ci_verdict
    failing = verdict.failing if verdict is not None else ()
    lines = [f"- {check.name}: {check.link}" for check in failing]
    return f"CI is red on {context.head}:\n" + "\n".join(lines)


def _resume(context: LandingContext) -> None:
    """The run that carries the reopened units: the spawner the verb was given, else the
    launcher's `agentic resume --issue N --yes`."""
    if context.resume is not None:
        landing_sync.spawn_resume(context)
        return
    number = context.ports.inbox.issue_of(context.task_id)
    if context.launcher is None or number is None:
        context.log(f"agentic: land: {context.task_id}: no way to spawn the resume")
        return
    argv = [
        context.argv0,
        "resume",
        "--issue",
        str(number),
        "--yes",
        "--product-dir",
        str(context.cfg.product_dir),
    ]
    pid = context.launcher.spawn(argv)
    context.log(f"agentic: land: {context.task_id}: resume -> pid {pid}")


def fix(context: LandingContext) -> None:
    """Reopens the step's units as `returned` with the open blocking rows, or the failing
    checks, as the reason, and resumes the task. Counts as a model pass in the `landing`
    list the step was recorded in, never against the pair's return limit."""
    document = context.ports.documents.load(context.task_id)
    if document is None:
        context.log(f"agentic: land: {context.task_id}: fix: no document")
        return
    raw = state_from(document)
    if context.step.refusal == "open_findings_blocking":
        reason, category = _blocking_reason(raw)
    else:
        reason, category = _red_reason(context, document), "process-suite-red"
    reopened = landing_sync.reopen(raw, context.step.units, reason, category, "returned")
    marked = landing_rules.with_cost_base(
        landing_plan.entries(raw), context.head, context.step.kind, raw.get("cost_usd", 0.0)
    )
    context.ports.documents.save({**reopened, "landing": marked})
    landing_sync.record_landing(context, marked)
    _resume(context)


def _remember_rerun(context: LandingContext, checks: list[str]) -> None:
    """Writes the names of the checks this rerun covers into its own `landing` entry, so
    the merge that follows a green can file one flake issue per name."""
    raw = context.ports.documents.load(context.task_id) or {}
    entries = [dict(entry) for entry in landing_plan.entries(raw)]
    for entry in entries:
        if entry.get("step") == "rerun" and entry.get("head") == context.head:
            entry["checks"] = checks
    context.ports.documents.record_closure(context.task_id, {"landing": entries})


def rerun(context: LandingContext) -> None:
    """Runs the red checks of this head again, once (the landing list's `rerun` entry for
    the head is what `landing_step` counts). `ci_failed` asks the platform to rerun the
    failed runs; `checks_failed` runs only the failed declared checks again in the verb's
    throwaway worktree, through `CheckoutPresubmit`, and updates `merge_checks`. The next
    tick decides again: green merges (and files the flake issues), red is a `fix`."""
    loaded = context.ports.documents.load(context.task_id) or {}
    reference = loaded.get("pull_request")
    if not reference:
        context.log(f"agentic: land: {context.task_id}: rerun: no pull request")
        return
    if context.step.refusal == "ci_failed":
        verdict = context.facts.ci_verdict
        failing = [check.name for check in verdict.failing] if verdict is not None else []
        try:
            runs = context.ports.tracker.rerun_failed_ci(str(reference), context.head)
        except ValueError as exc:
            context.log(f"agentic: land: {context.task_id}: rerun failed: {exc}")
            return
        _remember_rerun(context, failing)
        context.log(f"agentic: land: {context.task_id}: reran {len(runs)} run(s) on {context.head}")
        return
    recorded = loaded.get("merge_checks")
    names = presubmit.failed_names(recorded) if isinstance(recorded, Mapping) else []
    master = context.upstream.sha
    if master is None:
        context.log(f"agentic: land: {context.task_id}: rerun: no upstream")
        return
    raw = state_from(loaded)
    base = task_base(raw, context.ports)
    _remember_rerun(context, names)
    merge.run_checks(
        context.task_id,
        raw,
        context.head,
        master,
        base,
        context.cfg,
        context.ports,
        None,
        context.log,
        only=names or None,
    )


def _followup_body(raw: TaskState, task_id: str, lineage: int) -> str:
    rows = [row for row in rules.open_findings(raw) if row["severity"] == "important"]
    lines = [f"- `{row['location']}` ({row['category']}): {row['claim']}" for row in rows]
    return "\n".join(
        [
            landing_rules.followup_marker(task_id, lineage),
            f"Open `important` findings left on the approved head of {task_id}:",
            "",
            *lines,
        ]
    )


def file_followups_and_land(context: LandingContext) -> None:
    """Files one `agentic` follow-up issue for the task's open important findings (none
    when the task is itself a follow-up: it writes them into the pull request thread), and
    sets `followups_filed` so the next decision passes `open_findings`."""
    raw = _state(context)
    number = context.ports.inbox.issue_of(context.task_id)
    if raw is None or number is None:
        context.log(f"agentic: land: {context.task_id}: followups: no document")
        return
    bodies = context.ports.inbox.open_issue_bodies()
    own = [landing_rules.marker_lineage(body, context.task_id) for _, body in bodies]
    description = raw.get("description", "")
    parent = landing_rules.marker_parent(description)
    lineage = (parent[1] if parent else 0) + 1
    body = _followup_body(raw, context.task_id, lineage)
    if any(found is not None for found in own):
        context.log(f"agentic: land: {context.task_id}: follow-up already open")
    elif parent is not None:
        context.ports.tracker.comment(context.task_id, body)
    else:
        context.ports.tracker.open_issue(
            f"{landing_rules.FOLLOWUP_TITLE_PREFIX}{number}: open important findings",
            body,
            label=FOLLOWUP_LABEL,
        )
    context.ports.documents.record_closure(context.task_id, {"followups_filed": True})


def _stamp(context: LandingContext, **keys: object) -> None:
    """Writes `keys` on the last diagnose entry recorded for this head, through
    `record_closure` only, from the document as it is now: never a `save` of an earlier
    snapshot of the `landing` list."""
    loaded = context.ports.documents.load(context.task_id) or {}
    entries = [dict(entry) for entry in landing_plan.entries(loaded)]
    marked = [e for e in entries if e.get("step") == "diagnose" and e.get("head") == context.head]
    if not marked:
        return
    marked[-1].update(keys)
    context.ports.documents.record_closure(context.task_id, {"landing": entries})


def _escalate(context: LandingContext, reason: str) -> None:
    """The one way the landing escalates a task: `failure`, with no failure entries in the
    log (so `rules.auto_resumable` stays false), `needs-human` and the comment."""
    current = _state(context)
    if current is None:
        return
    escalated = apply(current, state_from(rules.logged(dict(rules.escalation("failure", reason)))))
    context.ports.documents.save(escalated)
    context.ports.tracker.label(context.task_id, escalate.NEEDS_HUMAN)
    context.ports.tracker.comment(
        context.task_id, escalate.comment(escalated, context.cfg, context.ports)
    )
    context.log(f"agentic: land: {context.task_id}: diagnose: escalated, {reason}")


def escalate_failed_diagnosis(context: LandingContext) -> None:
    """Escalates a task whose diagnosis never delivered and cannot be retried (a reason no
    wait cures, or the calendar spent), without running the role."""
    loaded = context.ports.documents.load(context.task_id) or {}
    attempts = landing_rules.diagnose_attempts(loaded)
    reason = attempts[-1].get("failure", "other") if attempts else "other"
    noun = "attempt" if len(attempts) == 1 else "attempts"
    _escalate(context, f"Landing Diagnoser did not deliver after {len(attempts)} {noun}: {reason}")


def _accounting(patch: TaskState) -> dict[str, object]:
    """What a failed run still owes the document: its cost, its runs and who ran. Never the
    failures, the pause log, the escalation or the status."""
    kept: dict[str, object] = {}
    if "cost_usd" in patch:
        kept["cost_usd"] = patch["cost_usd"]
    if "runs" in patch:
        kept["runs"] = patch["runs"]
    if "current_role" in patch:
        kept["current_role"] = patch["current_role"]
    return kept


def diagnose(context: LandingContext) -> None:
    """The landing's last step, once per task: the diagnoser reads why the merge keeps being
    refused. At restart depth 0 the pull request is closed and the issue starts over with
    `run --fresh`, carrying the depth and the cost the document records; from depth 1 the
    task escalates as `failure` and nothing is filed. A run that does not deliver is an
    attempt (GH-257): its reason goes on the entry and `land` retries on the shared calendar;
    only a reason no wait cures, or a spent calendar, escalates."""
    raw = _state(context)
    number = context.ports.inbox.issue_of(context.task_id)
    reference = raw.get("pull_request") if raw is not None else None
    if raw is None or not reference or number is None:
        context.log(f"agentic: land: {context.task_id}: diagnose: no document")
        return
    span = context.role_span(LANDING_DIAGNOSER) if context.role_span else nullcontext(None)
    with span as run:
        output, patch = asyncio.run(run_role(raw, LANDING_DIAGNOSER, context.cfg, context.ports))
        if run is not None:
            # The span stamps the step and the refusal this run was dispatched for.
            run.finish({**patch, "landing": raw.get("landing", [])})
    if output is None:
        context.ports.documents.save(apply(raw, state_from(_accounting(patch))))
        failures = patch.get("failures", [])
        last = failures[-1].get("reason", "other") if failures else "other"
        _stamp(context, failure=landing_rules.entry_of({"failure": last}).get("failure", "other"))
        context.log(f"agentic: land: {context.task_id}: diagnose: the role did not deliver")
        loaded = context.ports.documents.load(context.task_id) or {}
        if landing_rules.diagnose_retry(loaded, datetime.now(UTC)) == "escalate":
            escalate_failed_diagnosis(context)
        return
    saved = apply(raw, patch)
    context.ports.documents.save(saved)
    # Before anything else happens: a close or a spawn that fails must not read as undone.
    _stamp(context, delivered=True)
    reason = str(output.data.get("reason", ""))
    if landing_rules.restart_depth(raw) >= landing_rules.RESTART_DEPTH_MAX:
        _escalate(context, reason)
        return
    try:
        context.ports.tracker.close_pull_request(str(reference))
    except ValueError as exc:
        context.log(f"agentic: land: {context.task_id}: diagnose: close failed: {exc}")
        return
    # Only after the close: a close that failed leaves the task as it was, not marked restarted.
    carried = saved.get("cost_usd", 0.0) + saved.get("landing_cost_carried", 0.0)
    context.ports.documents.record_closure(
        context.task_id, {"landing_restarts": 1, "landing_cost_carried": carried}
    )
    context.ports.tracker.comment(context.task_id, f"Restarted by landing: {reason}")
    if context.launcher is None:
        context.log(f"agentic: land: {context.task_id}: no way to spawn the restart")
        return
    pid = context.launcher.spawn(
        [
            context.argv0,
            "run",
            "--issue",
            str(number),
            "--fresh",
            "--detach",
            "--product-dir",
            str(context.cfg.product_dir),
        ]
    )
    context.log(f"agentic: land: {context.task_id}: restart -> pid {pid}")
