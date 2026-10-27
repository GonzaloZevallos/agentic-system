"""System metrics over a task's state (the Medición glossary, `core/measurement/CONTEXT.md`).
Pure functions, so the telemetry adapter stays thin and each definition is a unit test.

Rework is a role run that repeats work already done in the task: a delivery with
`version > 1` (it follows a return) or a failed attempt (paid, no delivery). Internal
returns never reach the state, and parallel Developers will carry their own subtask
version, so neither counts. Measured in money (`docs/product/rework.md`; ADR-0008,
2026-09-18 second addendum): the whole `cost_usd` of a returned run, only the failed-
attempt money of a first delivery, plus whatever of the task's `cost_usd` no run
accounts for at all. Each dollar counts once, never twice: a failed attempt's cost
already lives inside the delivering run's own `cost_usd` (`run_role.py`), so a
`run_rework_usd` reader never adds it a second time on top.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from agentic.core.process.domain import rules
from agentic.core.process.domain.returns import is_return
from agentic.core.process.domain.task import (
    ChangesRequested,
    Failure,
    InterviewStats,
    PullRequest,
    PullRequestCommit,
    RoleRun,
    TaskState,
)
from agentic.shared.configuration import Configuration

# The two `Configuration` switches `arm_label` joins, in the order every writer uses
# (`experiment_snapshot`'s own order, minus `reviewer_model`: free text, not one of the
# "three enums" the arm is the product of -- ADR-0030). A subsequence of
# `EXPERIMENT_KEYS`, checked by `test_metrics.py`, so the two vocabularies cannot drift
# apart on order.
ARM_KEYS = ("review_mode", "skip_ephemeral")

# Below this, a residual is floating-point noise (an interview's runs summed in a
# different order), not money nothing accounted for: `has_residual_sample`'s own
# emission gate (ADR-0008, 2026-09-18 second addendum). Not a rounding tolerance — that
# job belongs to `RESIDUAL_FLOOR_USD` alone.
RESIDUAL_EPSILON_USD = 1e-9
# The rounding tolerance `rules.spent_by_phase` and the rework acceptance test compare
# with: it stopped gating emission (ADR-0008, 2026-09-18 second addendum) because it is
# a per-patch number and the promise it backs — the task's `rework_usd` equals the sum
# of its role samples — is per task, so many small patches could each hide up to this
# much and never clear the floor on their own.
RESIDUAL_FLOOR_USD = 0.005


def is_rework(run: RoleRun) -> bool:
    return run["version"] > 1


def run_rework_usd(run: RoleRun) -> float:
    """What of this run's own `cost_usd` is rework (ADR-0008, 2026-09-18 second
    addendum): the whole thing when the run is a return (`is_rework`), else only what
    its own failed attempts cost (`RoleRun.failed_cost_usd`, already counted once inside
    `cost_usd` by `run_with_retry`) — a `version == 1` run's delivering call is not
    rework. `0.0` for a run recorded before this task, which under-reports that run's
    failure money on purpose rather than guess it back."""
    return run["cost_usd"] if is_rework(run) else run.get("failed_cost_usd", 0.0)


def rework_cost_usd(state: TaskState) -> float:
    """Dollars spent on rework (`docs/product/rework.md`): the sum of `run_rework_usd`
    over every run, plus whatever of `cost_usd` no run accounts for — a task's own
    residual, which is rework by the same definition (`residual_rework`). One dollar
    counted once: a returned run's failed attempts never add on top of its own
    `cost_usd`, since `run_rework_usd` already picked the whole run over the
    `failed_cost_usd` fact for it."""
    runs = state.get("runs", [])
    rework = sum(run_rework_usd(r) for r in runs)
    unaccounted = max(state.get("cost_usd", 0.0) - sum(r["cost_usd"] for r in runs), 0.0)
    return rework + unaccounted


def changes_requested_total(state: Mapping[str, Any]) -> int:
    return sum(state.get("changes_requested", {}).values())


def _returned(state: TaskState) -> list[ChangesRequested]:
    """The return history without the landing's own reopens (`by: "landing"`, GH-239): a
    sync or a re-review is the system moving a pull request, not a role returning work, and
    `returns` does not count it either."""
    return [e for e in state.get("change_history", []) if is_return(e)]


def changes_by_category(state: TaskState) -> dict[str, int]:
    """Returns of the task by their category (ADR-0016). A task without returns still
    leaves one sample, `none` at 0;
    returns the count has but the history lacks (a task recorded before it) are
    `unknown`."""
    counts: dict[str, int] = {}
    history = _returned(state)
    for entry in history:
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    missing = changes_requested_total(state) - len(history)
    if missing > 0:
        counts["unknown"] = missing
    return counts or {"none": 0}


def change_categories(patch: TaskState) -> list[str]:
    """The categories of the returns one role run made, in order: a review node may
    return several subtasks in one run."""
    return [entry["category"] for entry in _returned(patch)]


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
    floating-point-noise epsilon, or it has no run at all to represent it — an empty
    patch (a node that raised outside its use case's own failure handling) still leaves
    one zero-cost sample, so a role's `cost_usd` panel never silently misses a run. Gated
    on `RESIDUAL_EPSILON_USD`, not `RESIDUAL_FLOOR_USD` (ADR-0008, 2026-09-18 second
    addendum): the floor is a per-task rounding tolerance, and gating emission on it here
    would let many small patches each hide up to that much money with none of them
    clearing it alone."""
    return residual_cost_usd(patch) >= RESIDUAL_EPSILON_USD or not patch.get("runs")


def residual_rework(patch: TaskState) -> bool:
    """Every residual is rework (ADR-0008, 2026-09-18 second addendum): a dollar no run
    record accounts for is a dollar that produced no delivery, which is the glossary's
    own definition of rework, whether or not this patch also carries a failed attempt of
    its own."""
    return True


def residual_phase(patch: TaskState) -> str:
    """The residual's own `phase` label, in cascade, never absent: the phase of the run
    this patch delivered (`target_run`), else the phase of the section its first failed
    attempt was writing, else the literal `"unknown"` (ADR-0008, 2026-09-18 second
    addendum) — an omitted label and one with a value are different series, and nobody
    sums them back together after the fact."""
    if (run := target_run(patch)) is not None:
        return rules.phase_of(run.get("section", run["role"]))
    failures = patch.get("failures", [])
    if failures and (section := failures[0].get("section")):
        return rules.phase_of(section)
    return "unknown"


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


def interview_questions(stats: InterviewStats) -> dict[str, int]:
    """One interview's questions by destination (`agentic.interview.questions{to}`):
    one to the interviewee per completed round, plus what went to the consultant
    (`questions_to_pm`) and to the human (`questions_to_human`)."""
    return {
        "interviewee": stats["rounds"],
        "pm": stats["questions_to_pm"],
        "human": stats["questions_to_human"],
    }


def invocation_returns_by_category(patch: TaskState) -> dict[str, int]:
    """Returns this one invocation (`run` or `resume`) recorded, by category: its own
    slice of `change_history` (what `task.delta` computed between the state that
    entered the invocation and the one it left with), with no `unknown`/`none`
    placeholder — unlike `changes_by_category`, this reads exact entries, one
    invocation's own, never a counter without the history to explain it."""
    counts: dict[str, int] = {}
    for entry in _returned(patch):
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    return counts


DeliveryOutcome = Literal["merged", "merged_with_changes", "auto_merged", "closed"]
OutcomeClass = Literal["human_kept", "landed_unjudged", "closed"]
# What each outcome says about the delivery, so no consumer decides by a literal of its
# own: `human_kept` carries a human's verdict, `landed_unjudged` landed without one
# (ADR-0045), `closed` never landed.
OUTCOME_CLASS: dict[DeliveryOutcome, OutcomeClass] = {
    "merged": "human_kept",
    "merged_with_changes": "human_kept",
    "auto_merged": "landed_unjudged",
    "closed": "closed",
}


def _is_human_commit(commit: PullRequestCommit, system_author_email: str) -> bool:
    """Whether this is the human's own work, not the system's, for
    `human_commits`/`human_lines_changed`/`delivery_outcome`: the system's git author
    email (`Configuration.GIT_AUTHOR_EMAIL`) is never among the commit's authors, and
    the commit is not a merge -- `parent_count < 2`. A merge is any commit with two or
    more parents, whatever its title: a hand-written headline (resolving a conflict
    while merging master, say) is still a merge. A one-parent commit titled "Merge …"
    (a hand-made squash, for example) counts as human work all the same -- the title
    is data on `PullRequestCommit`, never a criterion here. A GitHub login is never
    consulted: a human's own account would satisfy it just the same as the system's
    commits, which is exactly what this discriminates against."""
    if system_author_email in commit.author_emails:
        return False
    return commit.parent_count < 2


def delivery_outcome(
    pull_request: PullRequest, system_author_email: str, *, auto_merge_head: str | None = None
) -> DeliveryOutcome:
    """How the delivery ended: `closed` when the pull request never merged at all;
    `auto_merged` when it merged and the task document's `auto_merge_head` -- the
    intent the system wrote before it called the merge -- is the very head that
    merged (`None` and `""` are no intent); otherwise the human's verdict:
    `merged_with_changes` when it merged with at least one human commit on it, `merged`
    when it merged with none."""
    if pull_request.merged_at is None:
        return "closed"
    if auto_merge_head and auto_merge_head == pull_request.head_oid:
        return "auto_merged"
    if human_commits(pull_request, system_author_email) > 0:
        return "merged_with_changes"
    return "merged"


def human_commits(pull_request: PullRequest, system_author_email: str) -> int:
    """How many of the pull request's commits are the human's own: not the system's git
    author email, and not a merge commit -- `parent_count < 2`, whatever its title."""
    return sum(
        1 for commit in pull_request.commits if _is_human_commit(commit, system_author_email)
    )


def human_lines_changed(pull_request: PullRequest, system_author_email: str) -> int:
    """`additions + deletions` of exactly the commits `human_commits` counts: `0` for a
    pull request the human merged as the system delivered it."""
    return sum(
        commit.additions + commit.deletions
        for commit in pull_request.commits
        if _is_human_commit(commit, system_author_email)
    )


def time_to_merge_seconds(pull_request: PullRequest) -> float | None:
    """Seconds from the pull request's own `created_at` to its `merged_at`; `None` for
    one that never merged, since there is nothing to measure."""
    if pull_request.merged_at is None:
        return None
    created = datetime.fromisoformat(pull_request.created_at)
    merged = datetime.fromisoformat(pull_request.merged_at)
    return (merged - created).total_seconds()


def arm_label(experiment: Mapping[str, str]) -> str:
    """The canonical form of an `experiment` snapshot (`shared.configuration.
    experiment_snapshot`), the single point that turns it into the `arm` label
    (ADR-0030): `default` when both `ARM_KEYS` switches equal their `Configuration`
    field default, else `review_mode=<v>,skip_ephemeral=<v>` in `ARM_KEYS` order, one
    key always beside the other so a reader never has to know which switch a bare
    `review_mode=light` implies for the one not shown. A key missing from `experiment`
    reads as that field's own default, resolved from `Configuration.model_fields`
    here and nowhere else -- never from a `"full"`/`"False"` literal, so a default
    that moves someday moves this function's answer with it, never falls out of step
    with it. `reviewer_model` is not one of `ARM_KEYS`: it is free text, not an enum,
    and stays out of the label (ADR-0030) so the label's cardinality stays the closed
    product of `review_mode` x `skip_ephemeral`."""
    defaults = Configuration.model_fields
    resolved = {key: experiment.get(key, str(defaults[key].default)) for key in ARM_KEYS}
    if all(resolved[key] == str(defaults[key].default) for key in ARM_KEYS):
        return "default"
    return ",".join(f"{key}={resolved[key]}" for key in ARM_KEYS)


def prompts_fingerprint(state: TaskState) -> str:
    """One short hash for the set of prompts the task ran, order-independent, so runs
    against the same prompts share a label. Empty when no prompt ran."""
    prompts = state.get("prompts", {})
    if not prompts:
        return ""
    joined = ",".join(f"{slug}:{prompts[slug]}" for slug in sorted(prompts))
    return hashlib.sha256(joined.encode()).hexdigest()[:8]
