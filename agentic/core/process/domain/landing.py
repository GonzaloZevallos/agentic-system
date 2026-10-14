"""Aterrizaje (GH-239, ADR-0048): what the system does with an approved pull request that
`rules.auto_merge_decision` refused to merge, instead of leaving it for a human.

`auto_merge_decision` stays the only place that decides *whether* to merge;
`landing_step` is the only place that decides *what to do instead*, from the refusal and
the budgets already spent. No adapter, poller, CLI or executor chooses a recovery action
from a refusal by itself. Pure: facts in, a step out; the I/O of each step is the
application's."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from . import ci, rules
from .task import LandingEntry, TaskState

LandingStepKind = Literal[
    "wait",
    "sync_master",
    "re_review",
    "fix",
    "rerun",
    "file_followups_and_land",
    "diagnose",
    "none",
]
"""What the landing does about a refusal: `wait` (the next tick decides again), `sync_master`
(merge the upstream into the task branch), `re_review` (a Tech Lead review of the head),
`fix` (a Developer pass on `units`), `rerun` (the CI or the checks, once per head),
`file_followups_and_land` (file the important findings, then merge), `diagnose` (the
terminal path: the landing diagnoser, then a restart) and `none` (nothing the system can
do: the human's)."""

LANDING_PASSES_MAX = 3
"""Model passes (`re_review`, `fix`, a conflicting `sync_master`) per task."""

LANDING_SYNCS_MAX = 6
"""Clean `sync_master` per task."""

RESTART_DEPTH_MAX = 1
"""Restarts of one issue by the landing; a second exhaustion escalates."""


@dataclass(frozen=True)
class LandingStep:
    """`kind` is the step, `refusal` the reason it answers. `units` is the subtasks a `fix`
    reopens; empty means the reviewed unit of the document."""

    kind: LandingStepKind
    refusal: rules.MergeRefusal
    units: tuple[str, ...] = ()


@dataclass(frozen=True)
class LandingSpent:
    """What the task document says is already spent: model `passes` and clean `syncs`
    (counted from the `landing` list), `reruns_on_head` (entries of kind `rerun` on the
    current head), whether the diagnosis ran, and the seconds waited on an undecidable
    gate (`None`: no anchor)."""

    passes: int
    syncs: int
    reruns_on_head: int
    diagnosed: bool
    waited_s: float | None


def landing_step(
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    spent: LandingSpent,
    blocking_owners: tuple[str, ...],
) -> LandingStep:
    """The step for `refusal`. A model pass or a sync past its budget is the `diagnose`;
    a task already diagnosed has no step left (`none`). `blocking_owners` is the units that
    own the open blocking records (`blocking_owners`)."""

    def step(kind: LandingStepKind, units: tuple[str, ...] = ()) -> LandingStep:
        return LandingStep(kind, refusal, units)

    def pass_(kind: LandingStepKind, units: tuple[str, ...] = ()) -> LandingStep:
        return step(kind, units) if spent.passes < LANDING_PASSES_MAX else step("diagnose")

    if spent.diagnosed:
        return step("none")
    match refusal:
        case "not_ready" | "pr_not_open" | "eval_set":
            return step("none")
        case "eval_set_unreadable" | "ci_missing" | "ci_unreadable":
            waited = spent.waited_s
            return (
                step("diagnose")
                if waited is not None and waited >= ci.CI_WAIT_MAX_S
                else step("wait")
            )
        case "not_approved" | "head_not_reviewed":
            return pass_("re_review")
        case "open_findings_blocking":
            return pass_("fix", blocking_owners)
        case "open_findings":
            return step("file_followups_and_land")
        case "ci_failed":
            return step("rerun") if spent.reruns_on_head == 0 else pass_("fix", blocking_owners)
        case "checks_failed":
            if facts.failed_step == "conflict":
                return _sync(step, spent, conflicting=True)
            return step("rerun") if spent.reruns_on_head == 0 else pass_("fix", blocking_owners)
        case "attempts_exhausted":
            return _sync(step, spent, conflicting=False)
        case "merge_failed" | "adr_number_taken":
            return step("diagnose")
        case "master_moved":
            return step("wait")


def _sync(
    step: Callable[..., LandingStep], spent: LandingSpent, *, conflicting: bool
) -> LandingStep:
    """`sync_master` while the sync budget lasts; a sync known to conflict is also a model
    pass and needs that budget too."""
    if spent.syncs >= LANDING_SYNCS_MAX or (conflicting and spent.passes >= LANDING_PASSES_MAX):
        return step("diagnose")
    return step("sync_master")


def blocking_owners(state: TaskState) -> tuple[str, ...]:
    """The subtasks that own an open `blocking` record, in the order the table first raised
    them: the units a `fix` reopens, wherever the approval stands (a sibling's blocking
    record is not settled by the integration's approval). A name that is not a subtask of
    the document is no owner."""
    known = state.get("subtasks", {})
    owners: list[str] = []
    for row in rules.open_findings(state):
        if row["severity"] != "blocking":
            continue
        for name in row["subtask"].split(", "):
            if name in known and name not in owners:
                owners.append(name)
    return tuple(owners)


def review_carries(
    conflicted: bool, merge_paths: Iterable[str], branch_paths: Iterable[str]
) -> bool:
    """Whether the Tech Lead's verdict on the branch stands for the head after a sync: the
    merge was clean and the paths it brought in are disjoint from the branch's own."""
    if conflicted:
        return False
    return set(merge_paths).isdisjoint(branch_paths)


def already_dispatched(landing: Sequence[Mapping[str, object]], head: str, step: str) -> bool:
    """Whether `(head, step)` is already in the document's `landing` list: the idempotency
    key, written before the dispatch."""
    return any(entry.get("head") == head and entry.get("step") == step for entry in landing)


def landing_open(state: TaskState) -> bool:
    """A landing pass is in flight: the last `landing` entry is not resolved and some
    subtask is reopened for it (`rules.landing_open`, where `resume_target` reads it)."""
    return rules.landing_open(state)


def entry_of(raw: Mapping[str, object]) -> LandingEntry:
    """The `landing` entry `raw` says, each known key kept when it has its type."""
    out: LandingEntry = {}
    if isinstance(head := raw.get("head"), str):
        out["head"] = head
    if isinstance(step := raw.get("step"), str):
        out["step"] = step
    if isinstance(refusal := raw.get("refusal"), str):
        out["refusal"] = refusal
    if isinstance(at := raw.get("at"), str):
        out["at"] = at
    if isinstance(resolved := raw.get("resolved_at"), str):
        out["resolved_at"] = resolved
    if isinstance(cost := raw.get("cost_usd"), int | float) and not isinstance(cost, bool):
        out["cost_usd"] = float(cost)
    if isinstance(base := raw.get("cost_base"), int | float) and not isinstance(base, bool):
        out["cost_base"] = float(base)
    return out


def with_cost_base(
    entries: Sequence[Mapping[str, object]], head: str, step: str, cost_base: float
) -> list[LandingEntry]:
    """`entries` with `cost_base` set on the entry keyed `(head, step)`."""
    out = [entry_of(entry) for entry in entries]
    for entry in out:
        if entry.get("head") == head and entry.get("step") == step:
            entry["cost_base"] = cost_base
    return out


def resolve_open(
    entries: Sequence[Mapping[str, object]], at: str, cost_now: float
) -> list[LandingEntry]:
    """`entries` with the last one, when unresolved, closed at `at`: its `cost_usd` is
    `cost_now` less the `cost_base` it was written with. An already resolved last entry,
    or none, leaves the list as it was."""
    out = [entry_of(entry) for entry in entries]
    if not out or "resolved_at" in out[-1]:
        return out
    spent = cost_now - out[-1].get("cost_base", cost_now)
    out[-1]["resolved_at"] = at
    out[-1]["cost_usd"] = round(max(spent, 0.0), 4)
    return out


def followup_marker(task_id: str, lineage: int) -> str:
    """The first line of a follow-up issue body, which the deduplication reads back."""
    return f"<!-- agentic:followup task={task_id} lineage={lineage} -->"


def marker_lineage(body: str, task_id: str) -> int | None:
    """The lineage in `task_id`'s follow-up marker in `body`, or `None`. Matched byte for
    byte on the marker's own shape, never through the tracker's search."""
    found = re.search(
        rf"<!-- agentic:followup task={re.escape(task_id)} lineage=([0-9]+) -->", body
    )
    return int(found.group(1)) if found else None


def restart_depth(raw: Mapping[str, object]) -> int:
    """How many times the landing restarted this issue, from the task document."""
    value = raw.get("landing_restarts", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def marker_parent(body: str) -> tuple[str, int] | None:
    """`(task id, lineage)` of the follow-up marker in `body`, whichever task wrote it: a
    follow-up issue carries its parent's id, so a task whose own request holds one is
    itself a follow-up, and files nothing further (GH-239). `None` without a marker."""
    found = re.search(r"<!-- agentic:followup task=(\S+) lineage=([0-9]+) -->", body)
    return (found.group(1), int(found.group(2))) if found else None


def flake_marker(check: str) -> str:
    """The first line of a flake issue body, which the deduplication reads back: one open
    issue per check name, whichever task saw it flake."""
    return f"<!-- agentic:flake check={check} -->"
