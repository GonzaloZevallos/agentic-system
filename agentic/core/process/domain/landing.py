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
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from itertools import zip_longest
from typing import Any, Literal, Protocol, get_args

from . import ci, presubmit, rules
from .adr_numbers import Renaming
from .document import HUMAN
from .roles import LANDING_DIAGNOSER
from .task import (
    UPSTREAM_KEY,
    VERDICT_KEY,
    VERDICT_SYNC,
    Carried,
    DiagnosisVerdict,
    FailureReason,
    LandedSlice,
    LandedSliceRef,
    LandingEntry,
    ReleasePatch,
    SlicesClosedPatch,
    Subtask,
    SubtaskStatus,
    TaskState,
)

LandingStepKind = Literal[
    "wait",
    "sync_master",
    "re_review",
    "fix",
    "fix_important",
    "rerun",
    "file_followups_and_land",
    "renumber",
    "land_slice",
    "diagnose",
    "settle",
    "escalate_ledger",
    "escalate",
    "none",
]
"""What the landing does about a refusal (`settle` and `escalate_ledger`, GH-374, spend no model
and no pass: `settle` writes the `plan_settled` rows the live PM approval covers, `escalate_ledger`
asks the human once about the `PM: final validation` rows it does not cover). `wait` (the next tick
decides again), `sync_master` (merge the upstream into the task branch), `re_review` (a Tech
Lead review of the head), `fix` (a Developer pass on `units`), `fix_important` (the one final
pass on the task's own open `important` findings, GH-294), `rerun` (the CI or the checks, once
per head), `file_followups_and_land` (file the `outside` findings, then merge), `renumber`
(rename the ADR whose number master took and rewrite the references inside the branch's own work,
GH-229), `land_slice` (GH-297: land the approved units that apply cleanly on master as a
slice of their own, then go on with the rest; `LandingStep.units` is the slice), `diagnose`
(the terminal path: the landing diagnoser, then a restart; `restart_why` says why no slice
was possible), `escalate` (GH-371: every failing required check is also red on the base branch,
so the task escalates to the human with no model run) and `none` (nothing the system can do: the
human's)."""

RestartWhy = Literal["single_unit", "no_clean_slice", "slice_refused", "unreadable"]
"""Why a landing that exhausted its budgets restarts instead of landing a slice (GH-297):
`single_unit` (fewer than two approved units still to land, nothing to split), `no_clean_slice`
(no strict prefix of them is clean against master), `slice_refused` (the only clean ones were
refused twice) and `unreadable` (the facts could not be read). Closed: the restart comment
names one of them."""

FOLLOWUP_TITLE_PREFIX = "Follow-up of #"
"""How the title of a follow-up issue begins (`landing_fix` composes it; measurement reads
it to tell a follow-up task from an original one)."""

CONFLICTING_SYNC_REFUSALS: tuple[rules.MergeRefusal, ...] = ("checks_failed", "merge_conflict")
"""The refusals under which a recorded `sync_master` was a conflicting one, hence a model pass."""


def run_kind_of(step: LandingStepKind | None, refusal: rules.MergeRefusal | None) -> rules.RunKind:
    """The kind a `land` child registers under for the step it is about to carry out
    (`None` for the recoveries that have no step). Only `land_checks` or `land_model`; a
    step this table does not name is `land_model`, the kind that counts against the cap.
    `sync_master` is always `land_model`, even a clean one: it reopens the task and spawns a
    resume when the merge overlaps the reviewed paths, conflicts at run time, or a sync or
    push fails, and none of that is known before it runs. `refusal` is kept for a step whose
    kind may depend on it. A `sync_master` after a `sync` verdict of the diagnoser (GH-330) is
    the same step and the same kind."""
    match step:
        case "rerun" | "file_followups_and_land":
            return "land_checks"
        case "fix_important":
            return "land_model"
        case "land_slice" | "settle" | "escalate" | "escalate_ledger":
            return "land_checks"
        case _:
            return "land_model"


def run_kind_admits(
    registered: rules.RunKind, step: LandingStepKind | None, refusal: rules.MergeRefusal | None
) -> bool:
    """Whether a `land` child registered as `registered` may carry out `step` (`None` for
    the recoveries). A `land_model` child may do anything; any other kind only a step that
    `run_kind_of` says spends no model, so a child the poller did not count against the cap
    never starts a pass that would have."""
    return registered == "land_model" or run_kind_of(step, refusal) == "land_checks"


LANDING_PASSES_MAX = 3
"""Model passes per task, counted in `LandingSpent.passes`: `re_review` and `fix` only. A
`sync_master`, conflicting or not, is never charged here (GH-330): master churn the task does
not control must not spend the task's own budget for its reviews and fixes."""

LANDING_CONFLICT_SYNCS_MAX = 2
"""Conflicting `sync_master` (answering `CONFLICTING_SYNC_REFUSALS`) per upstream sha, counted
in `LandingSpent.conflicting_syncs_for_upstream` inside the `sync_epoch`; declared, not measured.
A new master sha starts a fresh budget. A verdict sync (`LANDING_VERDICT_SYNCS_MAX`) is exempt."""

LANDING_VERDICT_SYNCS_MAX = 1
"""`sync_master` per upstream sha that follows a `sync` verdict of the diagnoser, counted in
`LandingSpent.verdict_syncs_for_upstream`; declared, not measured. A second diagnosis for the
same sha reads as `unlandable`: the verdict is a guess, and it is trusted once."""

LANDING_IMPORTANT_PASSES_MAX = rules.LANDING_IMPORTANT_PASSES_MAX
"""`fix_important` passes per task (GH-294), before or after the pull request, read as
`LandingSpent.important_passes` (`rules.important_pass_spent`) and charged to
`LANDING_PASSES_MAX` not at all; declared, not measured. Re-exported from `rules`, which
owns the budget and the question of whether it is spent."""

LANDING_SYNCS_MAX = 6
"""`sync_master` of every kind per task, clean, conflicting or following a verdict, counted in
`LandingSpent.syncs` inside the `sync_epoch`: the ceiling across master moves."""

LANDING_RENUMBERS_MAX = 3
"""`renumber` steps per task (GH-229), counted from the `landing` list and charged neither to
`LANDING_SYNCS_MAX` nor to `LANDING_PASSES_MAX`; declared, not measured. A fourth collision
is the `diagnose`: no further push."""

LANDING_UNSYNCED_MAX = 2
"""`sync_master` passes closed without their upstream (`lacks`) per upstream sha; declared, not
measured. The second is the `diagnose`: a branch that cannot take master in is not retried."""

entry_closed = rules.entry_closed
"""Whether a `landing` entry is finished, re-exported from `rules`, which owns the reading."""

RESTART_DEPTH_MAX = 1
"""Restarts of one issue by the landing; a second exhaustion escalates."""

LANDING_RESUME_SPAWNS_MAX = 2
"""Resume children spawned per landing entry; a third look at one reopening is the human's."""

LANDING_RESUME_GRACE_S = 300
"""Seconds a spawned resume has to record its first run before it is presumed dead; declared,
not measured."""


@dataclass(frozen=True)
class LandingStep:
    """`kind` is the step, `refusal` the reason it answers. `units` is the subtasks a `fix`
    reopens; empty means the reviewed unit of the document."""

    kind: LandingStepKind
    refusal: rules.MergeRefusal
    units: tuple[str, ...] = ()
    # Only on a `diagnose` that `slice_or_restart` decided: the reason no slice was landed.
    restart_why: RestartWhy | None = None


@dataclass(frozen=True)
class LandingSpent:
    """What the task document says is already spent: model `passes` (`re_review` and `fix`
    only) and `syncs` of every kind (counted from the `landing` list), `reruns_on_head`
    (entries of kind `rerun` on the current head), the delivered `diagnoses`, and the seconds
    waited on an undecidable gate (`None`: no anchor); `renumbers` is the `renumber` entries on
    the list."""

    passes: int
    syncs: int
    reruns_on_head: int
    # Delivered `diagnose` entries on the list; `last_verdict` is what the latest one reads as.
    diagnoses: int
    waited_s: float | None
    # `renumber` entries across all heads (GH-229): the cap's own counter.
    renumbers: int = 0
    # `sync_master` entries closed with `lacks` equal to the current upstream sha (GH-267).
    unsynced_for_upstream: int = 0
    # `fix_important` entries on the list (GH-294): the final pass's own counter.
    important_passes: int = 0
    # GH-330, all by the current upstream sha and inside the `sync_epoch`: conflicting syncs
    # (`LANDING_CONFLICT_SYNCS_MAX`) and syncs that followed a `sync` verdict
    # (`LANDING_VERDICT_SYNCS_MAX`).
    conflicting_syncs_for_upstream: int = 0
    verdict_syncs_for_upstream: int = 0
    # The latest delivered diagnosis says `sync` for the current upstream sha and no
    # `sync_master` for it has been recorded since.
    verdict_sync_due: bool = False
    # What the latest delivered diagnosis reads as (`None`: none, or none inside the epoch).
    # A `sync` already served for its own sha, or without a sha to sync to, reads as
    # `unlandable`; so does an entry written before verdicts existed.
    last_verdict: DiagnosisVerdict | None = None
    # The recorded `merge_checks` of the current head holds a red check whose last row is the
    # gate's own rerun (ADR-0066): the failure already repeated, so the landing's `rerun` would
    # be a third run. `False` for a record written before the gate reran, which keeps `rerun`.
    checks_rerun_on_head: bool = False
    # GH-395: `sync_master` entries answering `ci_blocked_by_conflict` on the current head and
    # upstream sha (the pair): the one sync a conflicting head's missing CI is owed.
    conflict_ci_syncs_on_pair: int = 0

    @property
    def unlandable(self) -> bool:
        """A diagnosis was delivered and the latest one does not name a sync: the human's."""
        return self.diagnoses > 0 and self.last_verdict != VERDICT_SYNC


def landing_step(
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    spent: LandingSpent,
    blocking_owners: tuple[str, ...],
    *,
    important_units: tuple[str, ...] = (),
    slicing: Slice | RestartWhy | Callable[[], SliceInputs] | None = None,
    ledger_only: bool = False,
    pending: Sequence[object] = (),
    escalated: bool = False,
) -> LandingStep:
    """The step for `refusal`. `ledger_only` (`plan_settlement.ledger_only`), `pending`
    (`plan_settlement.pending`) and `escalated` (an `escalate_ledger` entry for this head and
    refusal is recorded) are computed by the caller: a ledger-only `open_findings_blocking` is
    `settle` while `pending` is not empty, then one `escalate_ledger`, then `none`; and an
    exhausted budget under it is that escalation, never a slice or a restart. A model pass or a
    sync past its budget is the exhaustion: the `land_slice` when `slicing` is a `Slice`, else
    the `diagnose` carrying `slicing` as its `restart_why` (`None`: as before GH-297). A task
    whose latest diagnosis is `unlandable` has no step left (`none`); one whose diagnosis said
    `sync` and has not been synced yet (`verdict_sync_due`) gets the `sync_master` whatever the
    refusal, within the task-wide and unsynced budgets and exempt from the per-sha conflict cap.
    `blocking_owners` is the units that own the open blocking records (`blocking_owners`);
    `important_units` the ones the final important pass reopens (`important_owners`); `slicing`
    is what the exhaustion decides a slice from: a ready `slice_or_restart` answer, or a
    provider of the `SliceInputs` that is called, and handed to `slice_or_restart`, only when a
    budget has run out, so a healthy landing reads no git for it. This is the only place
    `slice_or_restart`
    is called."""

    # Whatever the caller computed, only this refusal is ledger state (invariant: every other
    # refusal keeps its output).
    ledger_only = ledger_only and refusal == "open_findings_blocking"

    def step(kind: LandingStepKind, units: tuple[str, ...] = ()) -> LandingStep:
        return LandingStep(kind, refusal, units)

    def ask_once() -> LandingStep:
        return step("none") if escalated else step("escalate_ledger")

    def exhausted() -> LandingStep:
        """The one place every budget that ran out ends (invariant: nothing else lists
        the conditions of a slice or a restart)."""
        if ledger_only:
            return ask_once()
        answer = slice_or_restart(slicing()) if callable(slicing) else slicing
        if isinstance(answer, Slice):
            return step("land_slice", answer.units)
        return LandingStep("diagnose", refusal, restart_why=answer)

    def pass_(kind: LandingStepKind, units: tuple[str, ...] = ()) -> LandingStep:
        return step(kind, units) if spent.passes < LANDING_PASSES_MAX else exhausted()

    if spent.unlandable:
        return step("none")
    if spent.verdict_sync_due:
        return _verdict_sync(step, exhausted, spent)
    match refusal:
        case (
            "not_ready"
            | "pr_not_open"
            | "eval_set"
            | "perf_unverified"
            | "criteria_over_cap"
            | "criteria_unreadable"
        ):
            return step("none")
        case "eval_set_unreadable" | "ci_missing" | "ci_unreadable":
            waited = spent.waited_s
            return (
                exhausted() if waited is not None and waited >= ci.CI_WAIT_MAX_S else step("wait")
            )
        case "ci_blocked_by_conflict":
            # GitHub runs no CI on a head that conflicts with its base: never a wait. One
            # sync per (head, upstream) pair, off the conflict cap (a confirmed conflict's);
            # a second one for the same pair is the diagnose.
            if spent.conflict_ci_syncs_on_pair >= 1:
                return exhausted()
            return _sync(step, exhausted, spent, conflicting=False)
        case "not_approved" | "head_not_reviewed":
            return pass_("re_review")
        case "open_findings_blocking":
            if ledger_only:
                return step("settle") if pending else ask_once()
            return pass_("fix", blocking_owners)
        case "open_findings":
            if (
                facts.open_important > 0
                and not facts.important_pass_done
                and spent.important_passes < LANDING_IMPORTANT_PASSES_MAX
            ):
                return step("fix_important", important_units)
            return step("file_followups_and_land")
        case "ci_failed":
            if _red_on_base_only(facts):
                return step("escalate")
            return step("rerun") if spent.reruns_on_head == 0 else pass_("fix", blocking_owners)
        case "checks_failed":
            if facts.failed_step == "conflict":
                return _sync(step, exhausted, spent, conflicting=True)
            if spent.checks_rerun_on_head:
                return pass_("fix", blocking_owners)
            return step("rerun") if spent.reruns_on_head == 0 else pass_("fix", blocking_owners)
        case "merge_conflict":
            return _sync(step, exhausted, spent, conflicting=True)
        case "attempts_exhausted":
            return _sync(step, exhausted, spent, conflicting=False)
        case "adr_number_taken":
            # A clean renaming has its own capped step; anything else -- an ambiguous result
            # included -- is the diagnose, so no Developer ever picks an ADR number.
            if isinstance(facts.adr_collision, Renaming) and (
                spent.renumbers < LANDING_RENUMBERS_MAX
            ):
                return step("renumber")
            return exhausted()
        case "merge_failed":
            return exhausted()
        case "master_moved":
            return step("wait")


def _red_on_base_only(facts: rules.MergeFacts) -> bool:
    """Whether every failing required check is also red on the base branch (GH-371): the
    refusal is then not the task's to fix. False without a verdict to cover or a name red on
    the base."""
    if not facts.ci_red_on_base or facts.ci_verdict is None or not facts.ci_verdict.failing:
        return False
    return {check.name for check in facts.ci_verdict.failing} <= set(facts.ci_red_on_base)


def _sync(
    step: Callable[..., LandingStep],
    exhausted: Callable[[], LandingStep],
    spent: LandingSpent,
    *,
    conflicting: bool,
) -> LandingStep:
    """`sync_master` while the sync budgets last: the task-wide one, the unsynced one and, for
    a sync known to conflict, the per-sha conflict one. No pass budget is asked (GH-330)."""
    if (
        spent.syncs >= LANDING_SYNCS_MAX
        or spent.unsynced_for_upstream >= LANDING_UNSYNCED_MAX
        or (conflicting and spent.conflicting_syncs_for_upstream >= LANDING_CONFLICT_SYNCS_MAX)
    ):
        return exhausted()
    return step("sync_master")


def _verdict_sync(
    step: Callable[..., LandingStep], exhausted: Callable[[], LandingStep], spent: LandingSpent
) -> LandingStep:
    """The `sync_master` a `sync` verdict asks for: only the task-wide and the unsynced budgets
    can refuse it, never the per-sha conflict one."""
    if spent.syncs >= LANDING_SYNCS_MAX or spent.unsynced_for_upstream >= LANDING_UNSYNCED_MAX:
        return exhausted()
    return step("sync_master")


def after_diagnosis(
    verdict: DiagnosisVerdict | None,
    spent: LandingSpent,
    restart_depth: int,
    ledger_only: bool = False,
) -> Literal["sync_master", "restart", "escalate"]:
    """What a delivered diagnosis leads to, the only place a verdict becomes an action
    (GH-330): `sync_master` for a `sync` verdict while the sha's verdict-sync budget and the
    task-wide and unsynced ones last; otherwise `restart` while `restart_depth` is under
    `RESTART_DEPTH_MAX`, else `escalate`. `unlandable` and a missing verdict (a document
    written before verdicts) take the second path. A `ledger_only` refusal (GH-374) never
    restarts: no code change resolves ledger state, so it escalates."""
    if (
        verdict == VERDICT_SYNC
        and spent.verdict_sync_due
        and spent.verdict_syncs_for_upstream < LANDING_VERDICT_SYNCS_MAX
        and spent.syncs < LANDING_SYNCS_MAX
        and spent.unsynced_for_upstream < LANDING_UNSYNCED_MAX
    ):
        return "sync_master"
    if ledger_only:
        return "escalate"
    return "restart" if restart_depth < RESTART_DEPTH_MAX else "escalate"


def blocking_owners(state: TaskState) -> tuple[str, ...]:
    """The subtasks that own an open `blocking` record, in the order the table first raised
    them: the units a `fix` reopens, wherever the approval stands (a sibling's blocking
    record is not settled by the integration's approval). A name that is not a subtask of
    the document is no owner."""
    owners: list[str] = []
    for row in rules.open_findings(state):
        if row["severity"] != "blocking":
            continue
        owners.extend(name for name in rules.row_owners(state, row) if name not in owners)
    return tuple(owners)


def important_owners(state: TaskState) -> tuple[str, ...]:
    """The units the final important pass reopens: the owners of the task's own open
    `important` rows (`rules.important_partition`) and, when a `plan` row is open, the
    reviewed unit. An `outside` row owns none."""
    part = rules.important_partition(state)
    units = list(part.owned)
    if part.plan and (reviewed := rules.reviewed_unit_name(state)) and reviewed not in units:
        units.append(reviewed)
    return tuple(units)


def review_carries(
    conflicted: bool,
    merge_paths: Iterable[str],
    branch_paths: Iterable[str],
    landed_paths: Iterable[str] = (),
) -> bool:
    """Whether the Tech Lead's verdict on the branch stands for the head after a sync: the
    merge was clean and the paths it brought in are disjoint from the branch's own. The
    paths a landed slice of this very task brought (`landed_paths`, GH-297) are not master's
    doing and do not count against the branch."""
    if conflicted:
        return False
    return (set(merge_paths) - set(landed_paths)).isdisjoint(branch_paths)


SLICE_REFUSALS_MAX = 2
"""Times one exact set of units may be refused as a slice before `slice_or_restart` stops
proposing it (GH-297); declared, not measured."""


@dataclass(frozen=True)
class Slice:
    """A set of approved units the landing merges on their own (GH-297): a strict prefix of
    the pending ones in topological order, closed under `depends_on`."""

    units: tuple[str, ...]


@dataclass(frozen=True)
class SliceUnit:
    """An approved unit and the units it depends on, as the plan says."""

    name: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class SliceInputs:
    """The facts `slice_or_restart` decides from, gathered by the application. `approved` is
    the approved units in plan order; `carries` says per unit whether `review_carries` holds
    against what master changed since its base; `integration_paths` is what the integrator's
    work touches (`None`: it could not be built); `unit_paths` what each unit touches;
    `landed` the units whose `reviewed_head` is already an ancestor of master (`slice_landed`);
    `refused` the failed `land_slice` entries per set of units (`refused_slices`);
    `blocking_units` the units that own an open blocking row; `readable` whether every fact
    above could be read."""

    approved: tuple[SliceUnit, ...]
    carries: Mapping[str, bool]
    integration_paths: frozenset[str] | None
    unit_paths: Mapping[str, frozenset[str]]
    landed: frozenset[str]
    refused: Mapping[tuple[str, ...], int]
    blocking_units: frozenset[str]
    readable: bool


def _pending(inputs: SliceInputs) -> list[SliceUnit]:
    return [unit for unit in inputs.approved if unit.name not in inputs.landed]


def _topological(pending: Sequence[SliceUnit], landed: frozenset[str]) -> list[SliceUnit]:
    """`pending` with every unit after the ones it depends on, plan order among the free
    ones. A unit whose dependency is neither landed nor pending (not approved), or that is
    in a cycle, never comes out: nothing that needs it can be landed either."""
    done = set(landed)
    ordered: list[SliceUnit] = []
    waiting = list(pending)
    while True:
        ready = next((u for u in waiting if all(d in done for d in u.depends_on)), None)
        if ready is None:
            return ordered
        ordered.append(ready)
        done.add(ready.name)
        waiting.remove(ready)


def _clean(inputs: SliceInputs, unit: SliceUnit) -> bool:
    paths = inputs.unit_paths.get(unit.name)
    return (
        inputs.integration_paths is not None
        and paths is not None
        and inputs.carries.get(unit.name) is True
        and unit.name not in inputs.blocking_units
        and paths.isdisjoint(inputs.integration_paths)
    )


def slice_or_restart(inputs: SliceInputs) -> Slice | RestartWhy:
    """The one place a landing decides to land a slice or to restart (GH-297). The slice is
    the largest strict prefix of the pending units (approved, not landed), in topological
    order, whose units all carry, own no open blocking row, touch none of the integrator's
    paths and were not refused `SLICE_REFUSALS_MAX` times as that exact set. Otherwise the
    closed reason: `unreadable`, `single_unit`, `no_clean_slice`, or `slice_refused` when
    the clean prefixes were all refused."""
    if not inputs.readable:
        return "unreadable"
    pending = _pending(inputs)
    if len(pending) < 2:
        return "single_unit"
    if inputs.integration_paths is None:
        return "no_clean_slice"
    ordered = _topological(pending, inputs.landed)
    clean = 0
    while clean < len(ordered) and _clean(inputs, ordered[clean]):
        clean += 1
    candidates = [
        tuple(unit.name for unit in ordered[:size])
        for size in range(min(clean, len(pending) - 1), 0, -1)
    ]
    if not candidates:
        return "no_clean_slice"
    for units in candidates:
        if _refusals(inputs.refused, units) < SLICE_REFUSALS_MAX:
            return Slice(units)
    return "slice_refused"


def _refusals(refused: Mapping[tuple[str, ...], int], units: tuple[str, ...]) -> int:
    return sum(count for key, count in refused.items() if set(key) == set(units))


def integration_blocked(inputs: SliceInputs) -> bool:
    """Whether some pending unit touches the integrator's paths: what the restart comment
    calls `(integration work)`. Unknown integration paths block nothing."""
    if inputs.integration_paths is None:
        return False
    return any(
        not inputs.unit_paths.get(unit.name, frozenset()).isdisjoint(inputs.integration_paths)
        for unit in _pending(inputs)
    )


def slice_landed(units: Iterable[str], ancestry: Mapping[str, bool]) -> bool:
    """Whether every unit's `reviewed_head` is an ancestor of master (`ancestry` per unit,
    from `Workspace.is_ancestor`): git's word, never `landed_slices` alone. A unit whose
    ancestry was not read, or no unit at all, has not landed."""
    names = tuple(units)
    return bool(names) and all(ancestry.get(name) is True for name in names)


def abandoned_by_slices(
    units: Sequence[str],
    ancestry: Mapping[str, bool | None],
    *,
    pr_abandoned: bool,
    status: str | None,
    escalated: str | None,
    pull_request: str | None,
    abandoned_ref: str,
) -> Literal["close", "rerun"] | None:
    """What the poller does with a task whose pull request was read closed unmerged
    (`pr_abandoned`) and which landed slices (GH-334). `units` are the plan's subtasks,
    `integration` excluded; `ancestry` says per unit whether its `reviewed_head` is an
    ancestor of master (`None`: git could not be read). Git's word, never `landed_slices`
    alone: any unit with unread ancestry, or no unit at all, decides nothing, so an unread
    ancestry never closes. `close` when every unit landed. `rerun` otherwise, only for a
    task still `ready_for_review`, not escalated, whose document points at the very pull
    request read closed; a task a person or another path moved on is left alone."""
    if not pr_abandoned or not units:
        return None
    states = [ancestry.get(unit) for unit in units]
    if any(state is None for state in states):
        return None
    if all(state is True for state in states):
        return "close"
    if status == "ready_for_review" and not escalated and pull_request == abandoned_ref:
        return "rerun"
    return None


def slice_refs(landed: Sequence[LandedSlice] | None) -> list[str]:
    """The pull requests of the slices in a `landed_slices` list, in the order they landed;
    the unit ids of a record that kept no pull request. Whatever a hand-edited document holds
    that is not a record is skipped."""
    refs: list[str] = []
    for item in landed or ():
        if not isinstance(item, Mapping):
            continue
        reference, units = item.get("pull_request"), item.get("units")
        if isinstance(reference, str) and reference:
            refs.append(reference)
        elif isinstance(units, list):
            refs.extend(unit for unit in units if isinstance(unit, str))
    return refs


def slices_closed(refs: Sequence[str], at: str) -> SlicesClosedPatch:
    """The document patch that closes a task whose every unit landed through slices (GH-334):
    `closed_at` and `closed_by_slices` (the slices' pull requests). Never `delivery_outcome`:
    nobody judged a delivery, so no reader may count it as one."""
    return {"closed_at": at, "closed_by_slices": list(refs)}


def slice_facts(
    slice_: Slice,
    state: TaskState,
    *,
    task_id: str,
    slice_head: str | None,
    built_clean: bool,
    contains_reviewed: Mapping[str, bool],
    carries: Mapping[str, bool],
    pr_state: str | None,
    pr_head: str | None,
    in_eval_set: bool | None,
    touched_paths: tuple[str, ...] | None,
    current_master: str | None,
    attempts: int = 0,
    checks_head: str | None = None,
    checks_master: str | None = None,
    checks_passed: bool | None = None,
    merge_failures: int = 0,
    merge_failure: rules.MergeFailure | None = None,
    failed_step: presubmit.FailedStep | None = None,
    ci_required: tuple[str, ...] = (),
    ci_plan_only: tuple[str, ...] = (),
    ci_head: str | None = None,
    ci_verdict: ci.CiVerdict | None = None,
    ci_unreadable: ci.CiUnreadable | None = None,
    ci_fresh: bool | None = None,
    ci_waited_s: float | None = None,
    followups_filed: bool = False,
    important_pass_done: bool = False,
) -> rules.MergeFacts:
    """The `MergeFacts` of a slice's pull request, the only builder of them (GH-297);
    `rules.auto_merge_decision` alone decides whether it merges. The head stands as reviewed
    only when every unit is approved, carries, has its `reviewed_head` contained in the slice
    and the slice built clean; a unit that does not carry leaves `head_not_reviewed`. Only the
    rows owned by the slice's units are counted, and `open_outside` is 0: a row of a unit left
    out is that unit's. `pr_state`/`pr_head` are the slice pull request's, the checks and the
    CI are over the slice's own paths, `in_eval_set` is the task's."""
    units = slice_.units
    subtasks = state.get("subtasks", {})
    approved = bool(units) and all(
        subtasks.get(name, {}).get("status") == "approved" for name in units
    )
    reviewed = (
        approved
        and built_clean
        and all(carries.get(name) is True and contains_reviewed.get(name) is True for name in units)
    )
    owned = [
        row for row in rules.open_findings(state) if set(rules.row_owners(state, row)) & set(units)
    ]
    perf_convened, perf_verdict, perf_head = rules.perf_merge_facts(state)
    return rules.MergeFacts(
        task_id=task_id,
        status=state.get("status"),
        reviewed_unit_approved=approved,
        reviewed_head=slice_head if reviewed else None,
        open_blocking=sum(1 for row in owned if row["severity"] == "blocking"),
        open_important=sum(1 for row in owned if row["severity"] == "important"),
        open_outside=0,
        important_pass_done=important_pass_done,
        pr_state=pr_state,
        pr_head=pr_head,
        in_eval_set=in_eval_set,
        touched_paths=touched_paths,
        ci_untrusted=rules.ci_untrusted(touched_paths),
        attempts=attempts,
        checks_head=checks_head,
        checks_master=checks_master,
        checks_passed=checks_passed,
        current_master=current_master,
        merge_failures=merge_failures,
        merge_failure=merge_failure,
        failed_step=failed_step,
        ci_required=ci_required,
        ci_plan_only=ci_plan_only,
        ci_head=ci_head,
        ci_verdict=ci_verdict,
        ci_unreadable=ci_unreadable,
        ci_fresh=ci_fresh,
        ci_waited_s=ci_waited_s,
        followups_filed=followups_filed,
        perf_convened=perf_convened,
        perf_verdict=perf_verdict,
        perf_head=perf_head,
    )


def refused_slices(entries: Sequence[Mapping[str, object]]) -> dict[tuple[str, ...], int]:
    """The failed `land_slice` entries of the `landing` list per set of units (`slice_units`
    in the order recorded): finished without `resolved_at`. Input of `SliceInputs.refused`."""
    counts: dict[tuple[str, ...], int] = {}
    for entry in entries:
        units = entry.get("slice_units")
        if (
            entry.get("step") == "land_slice"
            and rules.entry_closed(entry)
            and not entry.get("resolved_at")
            and isinstance(units, list)
            and all(isinstance(unit, str) for unit in units)
        ):
            key = tuple(str(unit) for unit in units)
            counts[key] = counts.get(key, 0) + 1
    return counts


def sync_epoch(entries: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """The `landing` entries a sync budget counts (GH-297): those after the last resolved
    `land_slice` entry, since a landed slice gives the rest a master worth syncing with
    again. `passes` is not counted from here, ever; with no `land_slice` entry this is
    every entry."""
    last = max(
        (
            index
            for index, entry in enumerate(entries)
            if entry.get("step") == "land_slice" and entry.get("resolved_at")
        ),
        default=-1,
    )
    return list(entries[last + 1 :])


@dataclass(frozen=True)
class LineChange:
    """One changed line of a rewrite: the `path` it is in, the line as it was (`old`) and as
    it is (`new`), either empty when the line was added or removed."""

    path: str
    old: str
    new: str


def line_changes(path: str, old: str, new: str) -> list[LineChange]:
    """The lines that differ between two texts of `path`, paired in order; the side a line
    has no counterpart on is empty."""
    before, after = old.splitlines(), new.splitlines()
    found: list[LineChange] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            found.extend(
                LineChange(path, x, y)
                for x, y in zip_longest(before[i1:i2], after[j1:j2], fillvalue="")
            )
    return found


def renumber_carries(changes: Sequence[LineChange]) -> bool:
    """Whether the Tech Lead's verdict stands for the head after a renumber (GH-229): every
    changed line is under `docs/` or is, on both sides, a `#` comment line once its leading
    whitespace is stripped. A docstring, a string literal or a trailing comment is code that
    runs or is read by a tool, so it does not carry. The twin of `review_carries`, which
    decides the same for a sync merge."""

    def comment(line: str) -> bool:
        return not line.strip() or line.lstrip().startswith("#")

    return all(
        change.path.startswith("docs/") or (comment(change.old) and comment(change.new))
        for change in changes
    )


def already_dispatched(
    landing: Sequence[Mapping[str, object]], head: str, step: str, upstream: str | None
) -> bool:
    """Whether `(head, step)` is already in the document's `landing` list: the idempotency
    key, written before the dispatch. A diagnose entry that never delivered is an attempt
    (`diagnose_retry`), not a taken step; so is a sync closed with `lacks` (GH-267), which
    the unsynced budget counts instead. A `sync_master` entry blocks only when its `upstream`
    is also `upstream` (GH-330): a sync for an earlier master sha never blocks the one for the
    current sha; an entry without an `upstream` (written before) blocks on its head. A delivered
    diagnose whose verdict is `sync` never blocks: the next diagnosis is the second look at the
    sha. A `land_slice` is never taken for good (GH-297): an open one is continued by its
    executor, a refused one is retried until `SLICE_REFUSALS_MAX`, and a landed one leaves
    other units to land on the same head."""

    def taken(entry: Mapping[str, object]) -> bool:
        if entry.get("head") != head or entry.get("step") != step or "lacks" in entry:
            return False
        if step == "diagnose":
            return entry.get("delivered") is True and entry.get(VERDICT_KEY) != VERDICT_SYNC
        if step == "sync_master":
            recorded = entry.get(UPSTREAM_KEY)
            return not isinstance(recorded, str) or recorded == upstream
        return True

    return step != "land_slice" and any(taken(entry) for entry in landing)


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
    for key in (
        UPSTREAM_KEY,
        "closed_at",
        "lacks",
        "slice_pull_request",
        "restart_why",
        "released_at",
    ):
        if isinstance(text := raw.get(key), str):
            out[key] = text
    if isinstance(cost := raw.get("cost_usd"), int | float) and not isinstance(cost, bool):
        out["cost_usd"] = float(cost)
    if isinstance(base := raw.get("cost_base"), int | float) and not isinstance(base, bool):
        out["cost_base"] = float(base)
    if isinstance(checks := raw.get("checks"), list) and all(isinstance(c, str) for c in checks):
        out["checks"] = [c for c in checks if isinstance(c, str)]
    if isinstance(spawns := raw.get("resume_spawns"), int) and not isinstance(spawns, bool):
        out["resume_spawns"] = spawns
    if isinstance(spawned := raw.get("resume_spawned_at"), str):
        out["resume_spawned_at"] = spawned
    if isinstance(units := raw.get("slice_units"), list) and all(isinstance(u, str) for u in units):
        out["slice_units"] = [u for u in units if isinstance(u, str)]
    if (verdict := verdict_of(raw.get(VERDICT_KEY))) is not None:
        out[VERDICT_KEY] = verdict
    if isinstance(delivered := raw.get("delivered"), bool):
        out["delivered"] = delivered
    if raw.get("before_pr") is True:
        out["before_pr"] = True
    if isinstance(passed := raw.get("checks_passed"), bool):
        out["checks_passed"] = passed
    if isinstance(work := raw.get("integration_work"), bool):
        out["integration_work"] = work
    if (failure := _failure_of(raw.get("failure"))) is not None:
        out["failure"] = failure
    return out


def _failure_of(value: object) -> FailureReason | None:
    """`value` as a `FailureReason`, `None` when it is not one: the closed set is the type's
    own, never a second list."""
    for reason in get_args(FailureReason):
        if value == reason:
            found: FailureReason = reason
            return found
    return None


def verdict_of(value: object) -> DiagnosisVerdict | None:
    """`value` as a `DiagnosisVerdict`, `None` when it is not one: the closed set is the
    type's own, never a second list."""
    for verdict in get_args(DiagnosisVerdict):
        if value == verdict:
            found: DiagnosisVerdict = verdict
            return found
    return None


def diagnose_attempts(raw: Mapping[str, object]) -> list[LandingEntry]:
    """The diagnose entries of the document's `landing` list that never delivered, after the
    last one that did or that a human's decision released (`released_at`, GH-328): the
    attempts the retry calendar counts, oldest first."""
    entries = raw.get("landing")
    kept = (
        [entry_of(e) for e in entries if isinstance(e, Mapping)]
        if isinstance(entries, list)
        else []
    )
    diagnoses = [e for e in kept if e.get("step") == "diagnose"]
    closed = [i for i, e in enumerate(diagnoses) if e.get("delivered") or e.get("released_at")]
    after = diagnoses[closed[-1] + 1 :] if closed else diagnoses
    return [e for e in after if not e.get("delivered")]


def diagnose_retry(
    raw: Mapping[str, object], now: datetime
) -> Literal["none", "due", "waiting", "escalate"]:
    """What the landing does about a diagnosis that did not deliver (GH-257): `none` when no
    attempt is waiting on one, `escalate` when the last attempt has no external `failure` or
    the calendar is spent, else `due` or `waiting` on the shared retake calendar
    (`rules.retake_calendar`), measured from the last attempt's own `at`. No run delivers
    between two attempts, so the chain is the flat 429 calendar and never the hourly week.
    Timestamps are compared as aware datetimes, never as strings: an unreadable or naive one
    waits."""
    attempts = diagnose_attempts(raw)
    if not attempts:
        return "none"
    last = attempts[-1]
    reason = last.get("failure")
    if reason is None or reason not in rules.EXTERNAL_REASONS:
        return "escalate"
    count = len(attempts)
    limit = rules.auto_resume_limit(_landing_view(raw), reason)
    since = _aware(last.get("at"))
    candidate = rules.ResumeCandidate(0, reason, since or now, count, limit, count)
    if rules.resume_exhausted(candidate):
        return "escalate"
    if since is None or now.tzinfo is None or now.utcoffset() is None:
        return "waiting"
    schedule, prior, slots = rules.retake_calendar(reason, count, count, limit)
    due = rules.next_attempt_at(since, prior, slots, now, schedule=schedule)
    return "due" if due is not None else "waiting"


def unescalated(raw: Mapping[str, object]) -> dict[str, Any]:
    """The one patch that clears a landing step's escalation: the task is back at the status
    it had before it (`rules.resumed_status`) and the epoch is closed with a `resumed` entry.
    Never writes `cost_usd`. No other module builds it."""
    view = _landing_view(raw)
    if isinstance(pull_request := raw.get("pull_request"), str):
        view["pull_request"] = pull_request
    return {
        "escalated": None,
        "escalation_class": None,
        "status": rules.resumed_status(view),
        "pause_log": [{"kind": "resumed"}],
    }


def released(raw: Mapping[str, object], decision: str, at: str) -> ReleasePatch:
    """The one patch that releases a landing escalation on a human's `decision` (GH-328):
    `unescalated`, `released_at` stamped on the last diagnose entry (so `diagnose_attempts`
    starts counting again from the next one), and the decision as one more `### Decision` of
    the `Human` section, which the Landing Diagnoser reads. Written for `task.apply`: the
    `pause_log` marker is appended by its reducer, `sections` merges, `landing` is replaced
    whole. Typed as a `ReleasePatch` so `apply` takes it as it is. `{}` when the document is not
    escalated, so a second call writes nothing. Never writes `cost_usd`. No other module
    builds it."""
    reason = raw.get("escalated")
    if not reason:
        return {}
    cleared = unescalated(raw)
    entries = raw.get("landing")
    written = (
        [entry_of(e) for e in entries if isinstance(e, Mapping)]
        if isinstance(entries, list)
        else []
    )
    last = max((i for i, e in enumerate(written) if e.get("step") == "diagnose"), default=None)
    if last is not None:
        written[last] = {**written[last], "released_at": at}
    return {
        "escalated": cleared["escalated"],
        "escalation_class": cleared["escalation_class"],
        "status": cleared["status"],
        "pause_log": cleared["pause_log"],
        "landing": written,
        "sections": {HUMAN: rules.human_section(_with_sections(raw), str(reason), decision)},
    }


def _with_sections(raw: Mapping[str, object]) -> TaskState:
    """`raw` as the state `rules.human_section` reads: its `sections` and questions only."""
    state: TaskState = {"id": "", "title": "", "description": ""}
    sections = raw.get("sections")
    if isinstance(sections, Mapping):
        state["sections"] = {str(k): v for k, v in sections.items() if isinstance(v, str)}
    return state


ESCALATED_COMMENT = "Escalated: "
"""How an escalation comment starts. A copy of `application.escalate.ESCALATED`, which a test
pins: the domain cannot import the application."""


class Commented(Protocol):
    """A tracker comment as `escalated_after` reads it: `application.ports.IssueComment`
    satisfies it as it is."""

    @property
    def body(self) -> str: ...

    @property
    def created_at(self) -> str: ...


def _diagnose_entries(raw: Mapping[str, object]) -> list[Mapping[str, object]]:
    entries = raw.get("landing")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, Mapping) and e.get("step") == "diagnose"]


def diagnose_at(raw: Mapping[str, object]) -> object:
    """The `at` of the document's last diagnose entry, as written (`None` without one)."""
    entries = _diagnose_entries(raw)
    return entries[-1].get("at") if entries else None


def _closing_epoch(raw: Mapping[str, object]) -> list[Mapping[str, object]] | None:
    """The `pause_log` entries after the last `resumed`, `None` when the log is not a list of
    mappings."""
    log = raw.get("pause_log")
    if not isinstance(log, list) or not all(isinstance(e, Mapping) for e in log):
        return None
    last = max((i for i, e in enumerate(log) if e.get("kind") == "resumed"), default=-1)
    return log[last + 1 :]


def _stranded_failure(raw: Mapping[str, object]) -> FailureReason | None:
    """The last failure of the epoch being closed, when the epoch is exactly the stranded
    shape: one or more `failure` entries, each external and from the diagnoser's own
    section, and an `escalation` entry that reads `failure`; `None` for any other epoch (a
    real escalation, another role's failure)."""
    epoch = _closing_epoch(raw)
    if epoch is None:
        return None
    failures = [e for e in epoch if e.get("kind") == "failure"]
    escalations = [e for e in epoch if e.get("kind") == "escalation"]
    if not failures or not escalations:
        return None
    if any(e.get("value") != "failure" for e in escalations):
        return None
    if any(
        e.get("section") != LANDING_DIAGNOSER.name or e.get("value") not in rules.EXTERNAL_REASONS
        for e in failures
    ):
        return None
    return _failure_of(failures[-1].get("value"))


def stranded_diagnose(raw: Mapping[str, object], escalated_after: bool) -> bool:
    """A document GH-257 stranded: the Landing Diagnoser's failed run escalated through the
    graph's own patch, outside the graph, and nobody was told (ADR-0048, addendum). True only
    for the whole legacy shape: waiting on a human as class `failure`, a pull request, the
    diagnoser the current role, the last diagnose entry with neither `delivered` nor
    `failure`, and an epoch of external failures of the diagnoser alone ending in the
    escalation. `escalated_after` (`escalated_after`) says a comment already told a human, so
    the stop is real and stays."""
    if escalated_after:
        return False
    pull_request = raw.get("pull_request")
    if (
        raw.get("status") != "waiting_on_human"
        or raw.get("escalation_class") != "failure"
        or not isinstance(pull_request, str)
        or not pull_request
        or raw.get("current_role") != LANDING_DIAGNOSER.name
    ):
        return False
    entries = _diagnose_entries(raw)
    if not entries or "delivered" in entries[-1] or "failure" in entries[-1]:
        return False
    return _stranded_failure(raw) is not None


def escalated_after(comments: Iterable[Commented], at: object, prefix: str) -> bool:
    """Whether a comment starting with `prefix` (the escalation's) was created after `at`:
    a human was told of this stop and it is theirs. Aware datetimes are compared, never
    strings; a naive or unreadable time on `at` or on a matching comment gives True, so the
    document stays escalated."""
    mine = [c for c in comments if c.body.startswith(prefix)]
    if not mine:
        return False
    since = _aware(at)
    if since is None:
        return True
    for comment in mine:
        created = _aware(comment.created_at.replace("Z", "+00:00"))
        if created is None or created > since:
            return True
    return False


def migration(raw: Mapping[str, object]) -> dict[str, Any]:
    """The one write that frees a stranded document (`stranded_diagnose`): `unescalated`,
    plus the failure of the epoch it closes stamped on the last diagnose entry, so the
    ordinary retry takes it from there. `record_closure` replaces a key whole, so the
    `pause_log` is the history with the `resumed` marker appended. Never writes `cost_usd`."""
    patch = unescalated(raw)
    reason = _stranded_failure(raw)
    entries = raw.get("landing")
    written = (
        [dict(e) for e in entries if isinstance(e, Mapping)] if isinstance(entries, list) else []
    )
    last = max((i for i, e in enumerate(written) if e.get("step") == "diagnose"), default=None)
    if last is not None and reason is not None:
        written[last] = {**written[last], "failure": reason}
    log = raw.get("pause_log")
    patch["pause_log"] = [*(log if isinstance(log, list) else []), *patch["pause_log"]]
    patch["landing"] = written
    return patch


def _aware(value: object) -> datetime | None:
    """`value` as an aware datetime, or `None` when it is not an ISO string with an offset."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _status_of(value: object) -> SubtaskStatus | None:
    match value:
        case "pending":
            return "pending"
        case "delivered":
            return "delivered"
        case "approved":
            return "approved"
        case "returned":
            return "returned"
    return None


def _landing_view(raw: Mapping[str, object]) -> TaskState:
    """The part of the document `rules.landing_open` reads -- the `landing` entries and each
    subtask's status -- as a `TaskState`, so the one definition of an open landing is reused.
    The entries are the live ones (`rules.live_landing`): what came before a restoration is
    not part of the view, so neither `landing_open` nor `resume_owed` reads it."""
    state: TaskState = {"id": "", "title": "", "description": ""}
    state["landing"] = [entry_of(entry) for entry in rules.live_landing(raw)]
    units = raw.get("subtasks")
    subtasks: dict[str, Subtask] = {}
    for name, unit in units.items() if isinstance(units, Mapping) else []:
        if isinstance(unit, Mapping) and (status := _status_of(unit.get("status"))):
            subtasks[str(name)] = {"status": status}
    state["subtasks"] = subtasks
    return state


def resume_owed(raw: Mapping[str, object], now: datetime) -> bool:
    """A reopened landing whose resume child is owed: the pass is open, the task is not
    escalated, fewer than `LANDING_RESUME_SPAWNS_MAX` children were spawned for the last entry,
    the last one is older than `LANDING_RESUME_GRACE_S` and no run started since it. A timestamp
    that is present but unparseable or naive gives False: it cannot be shown that the child
    is gone. Timestamps are compared as aware datetimes, never as strings."""
    state = _landing_view(raw)
    if not rules.landing_open(state) or raw.get("escalated"):
        return False
    last = state["landing"][-1]
    if last.get("resume_spawns", 0) >= LANDING_RESUME_SPAWNS_MAX:
        return False
    spawned_at = last.get("resume_spawned_at")
    if spawned_at is None:
        return True
    spawned = _aware(spawned_at)
    if spawned is None or now - spawned < timedelta(seconds=LANDING_RESUME_GRACE_S):
        return False
    runs = raw.get("runs")
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, Mapping) or run.get("started_at") is None:
            continue
        started = _aware(run["started_at"])
        if started is None or started >= spawned:
            return False
    return True


def with_cost_base(
    entries: Sequence[Mapping[str, object]], head: str, step: str, cost_base: float
) -> list[LandingEntry]:
    """`entries` with `cost_base` set on the last entry, when it is keyed `(head, step)`;
    an earlier entry is never written to (GH-267)."""
    out = [entry_of(entry) for entry in entries]
    if out and out[-1].get("head") == head and out[-1].get("step") == step:
        out[-1]["cost_base"] = cost_base
    return out


def resolve_open(
    entries: Sequence[Mapping[str, object]], at: str, cost_now: float
) -> list[LandingEntry]:
    """`entries` with the last one, when unresolved, closed at `at`: its `cost_usd` is
    `cost_now` less the `cost_base` it was written with. An already closed last entry,
    or none, leaves the list as it was."""
    out = [entry_of(entry) for entry in entries]
    if not out or entry_closed(out[-1]):
        return out
    out[-1]["resolved_at"] = at
    out[-1]["cost_usd"] = _spent_since_base(out[-1], cost_now)
    return out


def close_unsynced(
    entries: Sequence[Mapping[str, object]], at: str, cost_now: float, lacks: str
) -> list[LandingEntry]:
    """`resolve_open`'s twin for a sync whose delivered head lacks its upstream (GH-267): the
    last entry, when not closed, gets `closed_at`, `lacks` and its `cost_usd`; never
    `resolved_at`."""
    out = [entry_of(entry) for entry in entries]
    if not out or entry_closed(out[-1]):
        return out
    out[-1]["closed_at"] = at
    out[-1]["lacks"] = lacks
    out[-1]["cost_usd"] = _spent_since_base(out[-1], cost_now)
    return out


def _spent_since_base(entry: LandingEntry, cost_now: float) -> float:
    return round(max(cost_now - entry.get("cost_base", cost_now), 0.0), 4)


def sync_verdict(
    entry: Mapping[str, object], contains: bool | None
) -> Literal["synced", "unsynced", "not_a_sync"]:
    """What a delivered `sync_master` entry achieved (GH-267): `contains` is the answer to
    whether the delivered head holds the entry's `upstream`. `not_a_sync` for another step or an
    entry without a string `upstream` (written before this rule: it keeps resolving as it did).
    Only `True` is `synced`; `False` and `None` (unreadable) are `unsynced`. Pure: no git."""
    if entry.get("step") != "sync_master" or not isinstance(entry.get(UPSTREAM_KEY), str):
        return "not_a_sync"
    return "synced" if contains is True else "unsynced"


def merge_owner(
    merge_head: str | None,
    upstream: str | None,
    head: str | None,
    entries: Sequence[Mapping[str, object]],
) -> Literal["none", "system", "foreign"]:
    """Whose merge a worktree's MERGE_HEAD is (GH-267): `none` without one, `system` when it is
    the round's `upstream`, the PR `head` or any `upstream` or `head` an entry recorded (through
    `rules.same_commit`), else `foreign`. Pure: no git."""
    if merge_head is None:
        return "none"
    known = [upstream, head]
    for entry in entries:
        known.extend(v for v in (entry.get(UPSTREAM_KEY), entry.get("head")) if isinstance(v, str))
    return "system" if any(rules.same_commit(merge_head, k) for k in known) else "foreign"


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


def pull_request_label(reference: str) -> str:
    """A pull request as `#N` when its reference (the URL `Tracker.open_pull_request`
    returns, or `#N` already) ends in a number, else the reference as it is: the one
    spelling every message about a landed slice uses."""
    tail = reference.rstrip("/").rsplit("/", 1)[-1].lstrip("#")
    return f"#{tail}" if tail.isdigit() else reference


def carry_over(raw: Mapping[str, object]) -> Carried | None:
    """What the archived document `raw` hands the fresh run of a `--fresh` start (GH-239,
    GH-297): the restart depth and cost, the Landing Diagnoser's section (only when the
    landing restarted it), every human decision (the `Human` section whole, restart or not:
    a `--fresh` start is the case that matters) and the slices already landed. Read from the
    document, never counted from issue comments; `None` when nothing is carried."""
    restarts = restart_depth(raw)
    cost = raw.get("landing_cost_carried", 0.0)
    spent = float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else 0.0
    sections = raw.get("sections")
    held = sections if isinstance(sections, Mapping) else {}
    diagnosis = held.get(LANDING_DIAGNOSER.name, "") if restarts else ""
    human = held.get(HUMAN, "")
    carried = Carried(
        restarts=restarts,
        cost=spent,
        diagnosis=diagnosis if isinstance(diagnosis, str) else "",
        human=human if isinstance(human, str) else "",
        landed=_landed_refs(raw.get("landed_slices")),
    )
    if not (restarts or carried.human):
        return None
    return carried


def _landed_refs(value: object) -> tuple[LandedSliceRef, ...]:
    """The readable entries of a `landed_slices` list: a `units` list of strings and a
    `pull_request` string; whatever else a hand-edited document holds is skipped."""
    if not isinstance(value, list):
        return ()
    refs: list[LandedSliceRef] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        units, pull_request = item.get("units"), item.get("pull_request")
        if (
            isinstance(units, list)
            and all(isinstance(unit, str) for unit in units)
            and isinstance(pull_request, str)
        ):
            refs.append(LandedSliceRef(tuple(units), pull_request))
    return tuple(refs)


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
