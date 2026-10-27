"""The landing steps that act without moving the head by themselves (GH-239, ADR-0048):
`fix`, `fix_important`, `rerun`, `file_followups_and_land` and `diagnose`.

Each executor receives the step `landing.land` already recorded for the head, so none of
them decides again what to do about the refusal."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime

from ..domain import landing as landing_rules
from ..domain import plan_settlement, presubmit, rules
from ..domain.copy import copy_location_parts
from ..domain.roles import LANDING_DIAGNOSER
from ..domain.task import (
    VERDICT_KEY,
    ChangeCategory,
    EscalationClass,
    FindingRecord,
    PlanSettledRow,
    TaskState,
    apply,
)
from . import escalate, landing_plan, landing_sync, merge
from .checkouts import task_base
from .landing_plan import LandingContext
from .poll import RESTARTED_BY_LANDING
from .resume import state_from
from .run_role import run_role

TRIAGE_LABEL = "needs-triage"
"""The one label the landing puts on an issue it opens from findings (ADR-0053): a person
decides whether it enters the queue. Never `agentic`."""


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
    _reopen_and_resume(context, raw, reason, category)


def _reopen_and_resume(
    context: LandingContext, raw: TaskState, reason: str, category: ChangeCategory
) -> None:
    """The reopening both Developer passes share: the step's units `returned` with `reason`,
    the entry carrying its cost base, the `landing` list written through `record_landing`,
    then the resume. `landing_sync.reopen` leaves `changes_requested` alone."""
    reopened = landing_sync.reopen(raw, context.step.units, reason, category, "returned")
    marked = landing_rules.with_cost_base(
        landing_plan.entries(raw), context.head, context.step.kind, raw.get("cost_usd", 0.0)
    )
    context.ports.documents.save({**reopened, "landing": marked})
    landing_sync.record_landing(context, marked)
    _resume(context)


def _reason_line(row: FindingRecord) -> str:
    """One row of the reason. A text row names the place it points at and the corrected text
    to apply, never its identifier; any other row reads `location: claim`, as ever."""
    parts = copy_location_parts(row["location"])
    if parts is None:
        return f"- {row['location']}: {row['claim']}"
    line = f"- {parts[2]}: {row['claim']}"
    proposed = row.get("proposed", "").strip()
    return f"{line} Corrected text: {proposed}" if proposed else line


def important_reason(raw: TaskState) -> tuple[str, ChangeCategory]:
    """The reason the final important pass hands the Developer: the task's own open
    `important` rows by unit, then the plan-scope ones, from the one partition."""
    part = rules.important_partition(raw)
    lines = ["Open important findings (final pass before merge, not a return):"]
    rows: list[FindingRecord] = []
    for unit, owned in part.owned.items():
        lines.append(f"Unit `{unit}`:")
        lines.extend(_reason_line(row) for row in owned)
        rows.extend(owned)
    if part.plan:
        lines.append("Plan:")
        lines.extend(_reason_line(row) for row in part.plan)
        rows.extend(part.plan)
    category: ChangeCategory = rows[0]["category"] if rows else "process-suite-red"
    return "\n".join(lines), category


_important_reason = important_reason
"""The name the landing's tests first knew `important_reason` by."""


def fix_important(context: LandingContext) -> None:
    """The one final pass on the task's own open `important` findings (GH-294): reopens the
    step's units as `returned` and resumes the task, so the graph runs the Developer and an
    incremental review. Not a return: the entry is marked `by: "landing"` and
    `changes_requested` is never touched. It counts when `deliver` resolves the entry."""
    document = context.ports.documents.load(context.task_id)
    if document is None:
        context.log(f"agentic: land: {context.task_id}: fix_important: no document")
        return
    raw = state_from(document)
    reason, category = important_reason(raw)
    _reopen_and_resume(context, raw, reason, category)


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


def _followup_body(rows: Sequence[FindingRecord], task_id: str, lineage: int) -> str:
    lines = [f"- `{row['location']}` ({row['category']}): {row['claim']}" for row in rows]
    return "\n".join(
        [
            landing_rules.followup_marker(task_id, lineage),
            f"Open `important` findings outside the scope of {task_id}:",
            "",
            *lines,
        ]
    )


def _open_triage_issue(context: LandingContext, number: int, body: str) -> None:
    """Opens the one `needs-triage` issue, creating the label if it is missing. Any
    `ValueError` (`TrackerCallFailed` included) from the label or the issue degrades to a
    comment on the pull request with the same body; a comment that fails too is logged."""
    tracker = context.ports.tracker
    try:
        tracker.ensure_label(TRIAGE_LABEL)
        tracker.open_issue(
            f"{landing_rules.FOLLOWUP_TITLE_PREFIX}{number}: open important findings",
            body,
            label=TRIAGE_LABEL,
        )
    except ValueError as exc:
        context.log(f"agentic: land: {context.task_id}: followups: issue not opened: {exc}")
        try:
            tracker.comment(context.task_id, body)
        except ValueError as failed:
            context.log(f"agentic: land: {context.task_id}: followups: comment failed: {failed}")


def file_followups_and_land(context: LandingContext) -> None:
    """Files the one `needs-triage` follow-up issue for the task's open `outside` findings
    (`rules.important_partition`; none when the task is itself a follow-up: it writes them
    into the pull request thread, and none at all when no `outside` row is open), and sets
    `followups_filed` so the next decision passes `open_findings`."""
    raw = _state(context)
    number = context.ports.inbox.issue_of(context.task_id)
    if raw is None or number is None:
        context.log(f"agentic: land: {context.task_id}: followups: no document")
        return
    outside = rules.important_partition(raw).outside
    if outside:
        bodies = context.ports.inbox.open_issue_bodies()
        own = [landing_rules.marker_lineage(body, context.task_id) for _, body in bodies]
        parent = landing_rules.marker_parent(raw.get("description", ""))
        lineage = (parent[1] if parent else 0) + 1
        body = _followup_body(outside, context.task_id, lineage)
        if any(found is not None for found in own):
            context.log(f"agentic: land: {context.task_id}: follow-up already open")
        elif parent is not None:
            context.ports.tracker.comment(context.task_id, body)
        else:
            _open_triage_issue(context, number, body)
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


def _escalate(
    context: LandingContext,
    reason: str,
    cls: EscalationClass = "failure",
    questions: Sequence[str] = (),
) -> None:
    """The one way the landing escalates a task: `failure` unless said otherwise, with no
    failure entries in the log (so `rules.auto_resumable` stays false), `needs-human` and the
    comment, which lists `questions` for the human when there are any."""
    current = _state(context)
    if current is None:
        return
    patch: dict[str, object] = dict(rules.escalation(cls, reason))
    if questions:
        patch["questions_for_human"] = list(questions)
    escalated = apply(current, state_from(rules.logged(patch)))
    context.ports.documents.save(escalated)
    context.ports.tracker.label(context.task_id, escalate.NEEDS_HUMAN)
    context.ports.tracker.comment(
        context.task_id, escalate.comment(escalated, context.cfg, context.ports)
    )
    context.log(f"agentic: land: {context.task_id}: {context.step.kind}: escalated, {reason}")


def settle(context: LandingContext) -> None:
    """Writes the `plan_settled` rows the live `PM: final validation` approval covers
    (`plan_settlement.pending`, the same list the decision read) into the document and
    resolves the step's entry. Nothing else: no pull request change, no model, no pass."""
    document = context.ports.documents.load(context.task_id)
    if document is None:
        context.log(f"agentic: land: {context.task_id}: settle: no document")
        return
    raw = state_from(document)
    rows: list[PlanSettledRow] = plan_settlement.pending(raw)
    patch = state_from({"plan_settled": rows})
    cost = document.get("cost_usd", 0.0)
    resolved = landing_rules.resolve_open(
        landing_plan.entries(document),
        (context.clock or _now)(),
        cost if isinstance(cost, int | float) else 0.0,
    )
    context.ports.documents.save({**apply(raw, patch), "landing": resolved})
    landing_sync.record_landing(context, resolved)
    context.log(f"agentic: land: {context.task_id}: settled {len(rows)} PM final row(s)")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def escalate_ledger(context: LandingContext) -> None:
    """The one question the landing asks when only `PM: final validation` ledger rows block an
    approved, green head and no live approval settles them (GH-374): each still-open row as
    `version: claim`, and the way out. The entry is resolved: it is asked once per head."""
    raw = _state(context)
    if raw is None:
        return
    rows = [
        f"{row['version']}: {row['claim']}"
        for row in rules.open_findings(raw)
        if row["severity"] == "blocking" and row["scope"] == "plan"
    ]
    question = (
        "These `PM: final validation` acceptance rows are still open on a head that is approved "
        "and green, and no approval of PM settles them:\n"
        + "\n".join(f"- {line}" for line in rows)
        + "\nMerge the pull request by hand, or decide on this issue, as with any escalation."
    )
    _escalate(
        context,
        f"{len(rows)} PM final validation row(s) block an approved, green head",
        "questions",
        [question],
    )


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


def _landed_numbers(saved: Mapping[str, object]) -> list[str]:
    """The pull requests of the slices the landing already merged, as `#N` (the whole
    reference when it has no trailing number), in the order they landed."""
    landed = saved.get("landed_slices")
    found: list[str] = []
    for item in landed if isinstance(landed, list) else []:
        reference = item.get("pull_request") if isinstance(item, Mapping) else None
        if isinstance(reference, str) and reference:
            found.append(landing_rules.pull_request_label(reference))
    return found


def _restart_notice(saved: TaskState, reason: str, carried: float) -> str:
    """The comment a landing restart leaves on the issue (GH-297): the closed `restart_why`
    of the latest diagnose entry (with `(integration work)` when the entry says the
    integrator's paths blocked the slice), the diagnoser's reason, what the task has cost so
    far and the slices already landed. It keeps the `RESTARTED_BY_LANDING` prefix; a document
    without the closed reason reads as before, plus the cost."""
    diagnoses = [e for e in landing_plan.entries(saved) if e.get("step") == "diagnose"]
    entry = diagnoses[-1] if diagnoses else {}
    why = entry.get("restart_why")
    label = ""
    if isinstance(why, str) and why:
        marker = " (integration work)" if entry.get("integration_work") is True else ""
        label = f"{why}{marker}: "
    tail = f"cost so far: ${carried:.2f}"
    if numbers := _landed_numbers(saved):
        tail += f"; landed slices: {', '.join(numbers)}"
    return f"{RESTARTED_BY_LANDING}{label}{reason} ({tail})"


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
    verdict = landing_rules.verdict_of(output.data.get(VERDICT_KEY))
    # Before anything else happens: a close or a spawn that fails must not read as undone.
    _stamp(context, delivered=True, **({} if verdict is None else {VERDICT_KEY: verdict}))
    reason = str(output.data.get("reason", ""))
    depth = landing_rules.restart_depth(raw)
    # The one decision: `after_diagnosis` alone turns the verdict into an action, never the
    # reason or the Markdown (GH-330).
    stamped = context.ports.documents.load(context.task_id) or {}
    spent = landing_plan.spent(stamped, context.head, None, context.upstream.sha)
    ledger = plan_settlement.ledger_only(context.step.refusal, context.facts, state_from(stamped))
    action = landing_rules.after_diagnosis(verdict, spent, depth, ledger_only=ledger)
    if action == "sync_master":
        context.log(
            f"agentic: land: {context.task_id}: diagnose: verdict {verdict}, "
            "the next round syncs master on this pull request"
        )
        return
    if action == "escalate":
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
    notice = _restart_notice(saved, reason, carried)
    try:
        context.ports.tracker.comment(context.task_id, notice)
    except ValueError as exc:
        # The notice informs, it does not gate: the fresh run starts without it.
        context.log(f"agentic: land: {context.task_id}: comment failed: {exc}")
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
