"""System metrics over a task's state (the Medición glossary, `core/measurement/CONTEXT.md`).
Pure functions, so the telemetry adapter stays thin and each definition is a unit test.

Rework is a role run that repeats work already done in the task: a delivery with
`version > 1` (it follows a return) or a failed attempt (paid, no delivery). Internal
returns never reach the state, and parallel Developers will carry their own subtask
version, so neither counts.
"""

from __future__ import annotations

import hashlib

from agentic.core.process.domain.task import RoleRun, TaskState


def is_rework(run: RoleRun) -> bool:
    return run["version"] > 1


def rework_cost_usd(state: TaskState) -> float:
    """Dollars spent on rework: repeated deliveries plus every failed attempt."""
    runs = sum(r["cost_usd"] for r in state.get("runs", []) if is_rework(r))
    failures = sum(f["cost_usd"] for f in state.get("failures", []))
    return runs + failures


def returns_total(state: TaskState) -> int:
    return sum(state.get("returns", {}).values())


def returns_by_category(state: TaskState) -> dict[str, int]:
    """Returns of the task by their category (ADR-0016). A task without returns still
    leaves one sample, `none` at 0;
    returns the count has but the history lacks (a task recorded before it) are
    `unknown`."""
    counts: dict[str, int] = {}
    history = state.get("return_history", [])
    for entry in history:
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    missing = returns_total(state) - len(history)
    if missing > 0:
        counts["unknown"] = missing
    return counts or {"none": 0}


def return_categories(patch: TaskState) -> list[str]:
    """The categories of the returns one role run made, in order: a review node may
    return several subtasks in one run."""
    return [entry["category"] for entry in patch.get("return_history", [])]


def prompts_fingerprint(state: TaskState) -> str:
    """One short hash for the set of prompts the task ran, order-independent, so runs
    against the same prompts share a label. Empty when no prompt ran."""
    prompts = state.get("prompts", {})
    if not prompts:
        return ""
    joined = ",".join(f"{slug}:{prompts[slug]}" for slug in sorted(prompts))
    return hashlib.sha256(joined.encode()).hexdigest()[:8]
