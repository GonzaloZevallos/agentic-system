"""System metrics over a task's state (the Medición glossary, `core/measurement/CONTEXT.md`).
Pure functions, so the telemetry adapter stays thin and each definition is a unit test.

Rework is a role run that repeats work already done in the task: a delivery with
`version > 1` (it follows a return) or a failed attempt (paid, no delivery). Internal
returns never reach the state, and parallel Developers will carry their own subtask
version, so neither counts.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from agentic.core.process.domain.task import Failure, RoleRun, TaskState

# `rules.spent_by_phase`'s own floor: below it, a residual is rounding noise, not
# money nothing accounted for.
RESIDUAL_FLOOR_USD = 0.005


def is_rework(run: RoleRun) -> bool:
    return run["version"] > 1


def rework_cost_usd(state: TaskState) -> float:
    """Dollars spent on rework: repeated deliveries plus every failed attempt."""
    runs = sum(r["cost_usd"] for r in state.get("runs", []) if is_rework(r))
    failures = sum(f["cost_usd"] for f in state.get("failures", []))
    return runs + failures


def changes_requested_total(state: TaskState) -> int:
    return sum(state.get("changes_requested", {}).values())


def changes_by_category(state: TaskState) -> dict[str, int]:
    """Returns of the task by their category (ADR-0016). A task without returns still
    leaves one sample, `none` at 0;
    returns the count has but the history lacks (a task recorded before it) are
    `unknown`."""
    counts: dict[str, int] = {}
    history = state.get("change_history", [])
    for entry in history:
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    missing = changes_requested_total(state) - len(history)
    if missing > 0:
        counts["unknown"] = missing
    return counts or {"none": 0}


def change_categories(patch: TaskState) -> list[str]:
    """The categories of the returns one role run made, in order: a review node may
    return several subtasks in one run."""
    return [entry["category"] for entry in patch.get("change_history", [])]


def role_slug(run: RoleRun) -> str:
    """`Role.slug`: `unknown` for a run recorded before this field existed, so a panel
    grouping by it never drops the sample."""
    return run.get("slug") or "unknown"


def target_run(patch: TaskState) -> RoleRun | None:
    """The run record a per-run `RoleRun.finish` patch's own verdict (`escalated`,
    `status`) describes: the last run whose section this patch actually wrote. `None`
    when no run in the patch matches — a pure escalation, or an empty patch — and the
    verdict is then the residual's alone."""
    sections = patch.get("sections", {})
    for run in reversed(patch.get("runs", [])):
        if run.get("section", run["role"]) in sections:
            return run
    return None


def role_run_outcome(patch: TaskState, run: RoleRun) -> str:
    """`delivered` for every run of the patch except the one `target_run` names, which
    takes the patch's own verdict: `escalated` when the patch escalated, `returned`
    when a verifier sent it back, `delivered` otherwise. Every other run in a multi-run
    patch (an interview's earlier rounds) is `delivered` regardless of how the patch as
    a whole ended — a role broke later, not that one."""
    if run is not target_run(patch):
        return "delivered"
    if patch.get("escalated"):
        return "escalated"
    if patch.get("status") == "returned":
        return "returned"
    return "delivered"


def residual_outcome(patch: TaskState) -> str:
    """The outcome of the money no run record carries. `failed`: nothing in the patch
    delivered, so a role broke outright. `escalated`: a run did deliver (`target_run`
    found one, and carries its own outcome already), and the escalation this residual
    represents is a separate fact of the same patch — open questions, a task cap hit
    after a delivery already happened."""
    return "escalated" if target_run(patch) is not None else "failed"


def residual_cost_usd(patch: TaskState) -> float:
    """What of the patch's `cost_usd` no run record accounts for: an escalation's own
    failed attempts, or a node that raised with nothing recorded at all. Floored at
    zero so a reader never sees a negative cost from rounding."""
    total = patch.get("cost_usd", 0.0)
    accounted = sum(run["cost_usd"] for run in patch.get("runs", []))
    return max(total - accounted, 0.0)


def has_residual_sample(patch: TaskState) -> bool:
    """Whether this patch needs a residual sample: its unaccounted cost clears the
    rounding floor, or it has no run at all to represent it — an empty patch (a node
    that raised outside its use case's own failure handling) still leaves one
    zero-cost sample, so a role's `cost_usd` panel never silently misses a run."""
    return residual_cost_usd(patch) >= RESIDUAL_FLOOR_USD or not patch.get("runs")


def residual_rework(patch: TaskState) -> bool:
    """The residual is rework whenever the patch paid for a failed attempt: paid,
    undelivered work is rework by definition (`rework_cost_usd`)."""
    return bool(patch.get("failures"))


def failures_by_reason(failures: list[Failure]) -> dict[str, int]:
    """How many of a patch's failed attempts share each reason: `agentic.role.failures`
    groups on this, one sample per reason with the count as its value. `other` covers a
    failure recorded before `reason` existed too."""
    counts: dict[str, int] = {}
    for failure in failures:
        reason = failure.get("reason", "other")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def failure_cost_by_reason(failures: list[Failure]) -> dict[str, float]:
    """What a patch's failed attempts cost, by reason: `agentic.role.failure_cost_usd`,
    documented as not additive with `cost_usd` — the same dollar already counts once,
    inside a run's own cost or inside the residual."""
    totals: dict[str, float] = {}
    for failure in failures:
        reason = failure.get("reason", "other")
        totals[reason] = totals.get(reason, 0.0) + failure["cost_usd"]
    return totals


def run_duration_s(run: RoleRun) -> float | None:
    """Wall time of one run inside its concurrency slot: `ended_at` minus `started_at`,
    both ISO-8601. `None` for a run recorded before either field existed, which tells
    the adapter not to emit a sample for it."""
    started, ended = run.get("started_at"), run.get("ended_at")
    if not started or not ended:
        return None
    return (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()


def prompts_fingerprint(state: TaskState) -> str:
    """One short hash for the set of prompts the task ran, order-independent, so runs
    against the same prompts share a label. Empty when no prompt ran."""
    prompts = state.get("prompts", {})
    if not prompts:
        return ""
    joined = ",".join(f"{slug}:{prompts[slug]}" for slug in sorted(prompts))
    return hashlib.sha256(joined.encode()).hexdigest()[:8]
