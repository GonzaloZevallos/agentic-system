"""The landing steps that act without moving the head by themselves (GH-239, ADR-0048):
`fix`, `rerun`, `file_followups_and_land` and `diagnose`.

Each executor receives the step `landing.land` already recorded for the head, so none of
them decides again what to do about the refusal."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import nullcontext

from ..domain import landing as landing_rules
from ..domain import presubmit, rules
from ..domain.roles import LANDING_DIAGNOSER
from ..domain.task import ChangeCategory, TaskState, apply, from_document
from . import landing_sync
from .landing_plan import LandingContext
from .run_role import run_role

FOLLOWUP_LABEL = "agentic"


def _state(context: LandingContext) -> TaskState | None:
    """The task document as a state, or `None` when there is none."""
    loaded = context.ports.documents.load(context.task_id)
    return from_document(loaded) if loaded is not None else None


def _blocking_reason(raw: TaskState) -> tuple[str, ChangeCategory]:
    rows = [row for row in rules.open_findings(raw) if row["severity"] == "blocking"]
    lines = [f"- {row['location']}: {row['claim']}" for row in rows]
    category: ChangeCategory = rows[0]["category"] if rows else "process-suite-red"
    return "Open blocking findings:\n" + "\n".join(lines), category


def _red_reason(context: LandingContext, raw: TaskState) -> str:
    """The failing check names with their tails: the recorded local checks, or the CI runs."""
    recorded = raw.get("merge_checks")
    record: Mapping[str, object] = recorded if isinstance(recorded, Mapping) else {}
    if context.step.refusal == "checks_failed" and record:
        return presubmit.failed_checks_markdown(record, context.head, neutralise=False)
    verdict = context.facts.ci_verdict
    failing = verdict.failing if verdict is not None else ()
    lines = [f"- {check.name}: {check.link}" for check in failing]
    return f"CI is red on {context.head}:\n" + "\n".join(lines)


def _resume(context: LandingContext) -> None:
    """The run that carries the reopened units: `agentic resume --issue N --yes`."""
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
    raw = _state(context)
    if raw is None:
        context.log(f"agentic: land: {context.task_id}: fix: no document")
        return
    if context.step.refusal == "open_findings_blocking":
        reason, category = _blocking_reason(raw)
    else:
        reason, category = _red_reason(context, raw), "process-suite-red"
    patch = landing_sync.reopen(raw, context.step.units, reason, category, "returned")
    context.ports.documents.save(apply(raw, patch))
    _resume(context)


def rerun(context: LandingContext) -> None:
    """`ci_failed`: asks the platform to rerun the failed runs of this head, once (the
    landing list's `rerun` entry for the head is what `landing_step` counts). The next tick
    reads the CI again; a red one is then a `fix`."""
    reference = (context.ports.documents.load(context.task_id) or {}).get("pull_request")
    if context.step.refusal != "ci_failed" or not reference:
        context.log(f"agentic: land: {context.task_id}: rerun of local checks is not built yet")
        return
    try:
        runs = context.ports.tracker.rerun_failed_ci(str(reference), context.head)
    except ValueError as exc:
        context.log(f"agentic: land: {context.task_id}: rerun failed: {exc}")
        return
    context.log(f"agentic: land: {context.task_id}: reran {len(runs)} run(s) on {context.head}")


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
            f"Follow-up of #{number}: open important findings", body, label=FOLLOWUP_LABEL
        )
    context.ports.documents.record_closure(context.task_id, {"followups_filed": True})


def diagnose(context: LandingContext) -> None:
    """The landing's last step, once per task: the diagnoser reads why the merge keeps being
    refused. At restart depth 0 the pull request is closed and the issue starts over with
    `run --fresh`, carrying the depth and the cost the document records; from depth 1 the
    task escalates as `failure` and nothing is filed."""
    raw = _state(context)
    number = context.ports.inbox.issue_of(context.task_id)
    reference = raw.get("pull_request") if raw is not None else None
    if raw is None or not reference or number is None:
        context.log(f"agentic: land: {context.task_id}: diagnose: no document")
        return
    span = context.role_span(LANDING_DIAGNOSER) if context.role_span else nullcontext(None)
    with span as run:
        output, patch = asyncio.run(run_role(raw, LANDING_DIAGNOSER, context.cfg, context.ports))
        # The span stamps the step and the refusal this run was dispatched for.
        patch["landing"] = raw.get("landing", [])
        if run is not None:
            run.finish(patch)
    if output is None:
        context.ports.documents.save(apply(raw, patch))
        context.log(f"agentic: land: {context.task_id}: diagnose: the role did not deliver")
        return
    reason = str(output.data.get("reason", ""))
    if landing_rules.restart_depth(raw) >= landing_rules.RESTART_DEPTH_MAX:
        patch |= rules.escalation("failure", reason)
        context.ports.documents.save(apply(raw, patch))
        context.log(f"agentic: land: {context.task_id}: diagnose: escalated, {reason}")
        return
    saved = apply(raw, patch)
    context.ports.documents.save(saved)
    carried = saved.get("cost_usd", 0.0) + saved.get("landing_cost_carried", 0.0)
    context.ports.documents.record_closure(
        context.task_id, {"landing_restarts": 1, "landing_cost_carried": carried}
    )
    try:
        context.ports.tracker.close_pull_request(str(reference))
    except ValueError as exc:
        context.log(f"agentic: land: {context.task_id}: diagnose: close failed: {exc}")
        return
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
