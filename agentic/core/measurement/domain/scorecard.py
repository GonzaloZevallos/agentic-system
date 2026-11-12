"""The scorecard (the Medición glossary, `core/measurement/CONTEXT.md`: *Scorecard*,
*Línea de base*): ADR-0028's four figures over one baseline's own task documents, and
the verdict that reads them in order. Pure domain -- no filesystem, no subprocess, no
port: `bucket_by_baseline` groups already-parsed documents by baseline bucket, taking
the git-reading resolver (`adapters.otel.telemetry.resolve_baseline`) as an injected
callable rather than calling it itself, so the grouping stays as testable as every
other function here -- `cli._bucket_documents` owns only the glob and the parse
(Tech Lead review, subtask `scorecard`).

Not inside `evaluation.py`, which owns the *Set de evaluación* and its per-task line:
the glossary says the scorecard is "una consulta sobre las muestras, no una muestra",
a different concept with a different reader (`CONTEXT.md`'s own `Scorecard` entry).

**Import direction, stated so it is not discovered:** this module imports `metrics`;
`evaluation` imports this module. One arrow, no cycle -- `task_figures` cannot accept
`evaluation.TaskDocument` for that reason, and declares its own document shape below,
structurally identical to it on every key this module reads.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from agentic.core.process.domain import cost_history, size_budget
from agentic.core.process.domain.task import RoleRun, TaskState

from . import cycle, metrics, planning

# The three failure reasons a scorecard's "own failures" excludes (ADR-0029, extended
# by issue #137's own `network`), read here and nowhere else in this diff: every other
# module that needs the exclusion imports this constant rather than spelling the three
# strings again -- the drift the `Scorecard` entry of `agentic/core/measurement/
# CONTEXT.md` names when it says "quedan afuera los motivos externos (`account_limit`,
# el límite de la cuenta del proveedor; `upstream`, su caída; y `network`, la red del
# lado del sistema caída, nunca leída de la prosa del error) y quedan adentro los
# nuestros, el tope por intento (`limit`) incluido". That entry is the statement this
# tuple is a copy of; nothing here restates its reasoning, only its members. A
# separate copy from `core.process.domain.rules.EXTERNAL_REASONS`, never imported: the
# two tuples read the same literals on purpose, so a document's own `FailureReason`
# string decides each independently, with no import between the two contexts' rule
# modules.
EXTERNAL_FAILURE_REASONS = ("account_limit", "upstream", "network", "disk_full")

# "Tareas terminadas" (ADR-0008, 2026-09-18 second addendum): a Corrida that reached a
# terminal status, whether or not a human has judged its pull request yet. Own failures
# per task and rework are denominated on these; autonomy and cost per delivery are
# denominated on judged deliveries instead (`closed_at` present), a different, wider
# set that a task never reaches without first passing through one of these two.
TERMINATED_STATUSES = ("ready_for_review", "discarded")

# The smallest judged denominator at which autonomy prints a rate (ADR-0045): the size
# of the evaluation set. Auto-merged deliveries carry no human verdict and leave the
# judged set, so the sample shrinks as auto-merge grows; below this many judged tasks
# `eval compare` shows `n/m` and no rate.
AUTONOMY_MIN_JUDGED = 5

# The outcomes that landed without a human verdict, read off the class mapping so no
# outcome string is spelled here.
LANDED_UNJUDGED = frozenset(
    outcome for outcome, kind in metrics.OUTCOME_CLASS.items() if kind == "landed_unjudged"
)


class ScorecardDocument(TaskState, total=False):
    """The closure keys `task_figures` reads beyond `TaskState`, declared again here
    rather than imported from `evaluation.TaskDocument` (module docstring, "Import
    direction"): every one of them is written once, by `agentic close`, through
    `TaskDocuments.record_closure` as a single scalar string -- `str`-typed here for
    the same reason `evaluation.TaskDocument` types them `str`, matching what
    `record_closure`'s `_scalar_line` actually writes at runtime rather than an `int`
    or a `DeliveryOutcome` literal that never occurs on disk."""

    closed_at: str
    delivery_outcome: metrics.DeliveryOutcome
    human_commits: str
    human_lines_changed: str
    time_to_merge_seconds: str
    system_commit: str
    system_baseline: str


@dataclass(frozen=True)
class TaskFigures:
    """The per-task read `task_figures` returns, from one document alone: no `issue`,
    no `score`, no `Inbox`, no `gh` -- those stay `evaluation.py`'s own (module
    docstring). `human_commits`/`human_lines_changed`/`time_to_merge_seconds` are
    `None` for a task `agentic close` has not recorded yet, or one closed before that
    field existed; never a fabricated zero."""

    cost_usd: float
    rework_usd: float
    failures_by_reason: dict[str, int]
    status: str
    closed_at: str | None
    delivery_outcome: str | None
    human_commits: int | None
    human_lines_changed: int | None
    time_to_merge_seconds: float | None
    system_commit: str | None
    system_baseline: str | None
    # The document's own `runs[]`, carried so `role_usage.by_role` reads the same
    # bucket `bucket_by_baseline` grouped (GH-292); empty for a document with none.
    runs: Sequence[RoleRun] = ()
    # The plan's own estimate (`estimate`, never the revisions: the column measures how well
    # the plan guessed) and the files the pull request changed. `None` for a document
    # from before ADR-0054 or one not closed yet: no estimate, never a zero.
    estimate_cost_usd: float | None = None
    estimate_files: int | None = None
    actual_files: int | None = None
    # The planned and the measured hand-written size (GH-592), read through `size_budget`;
    # `None` for a document from before the keys or one not delivered yet.
    planned_size: size_budget.PlanSize | None = None
    actual_size: size_budget.ActualSize | None = None
    # The arm the task ran under (`metrics.arm_of`, never the arm's name), the experiment its
    # assignment names, and its time: `cycle_class` decides whether the task enters
    # `cycle_h`/`active_h` at all, the seconds are `None` when a bound is missing.
    arm: str = "default"
    experiment_id: str | None = None
    cycle_class: cycle.CycleClass = "excluded"
    cycle_s: float | None = None
    active_s: float | None = None
    # The cycle split by class (GH-467), each `None` when unknown, never a zero:
    # `cycle.time_split` computes every interval, this module only carries the seconds.
    failed_s: float | None = None
    waiting_s: float | None = None
    paused_external_s: float | None = None
    paused_human_s: float | None = None
    paused_s: float | None = None
    unattributed_s: float | None = None
    # What planning cost and returned, for `eval compare`'s planning block (GH-472).
    planning_facts: planning.TaskPlanning | None = None


def task_figures(document: ScorecardDocument) -> TaskFigures:
    """The per-task read, from `document` alone. Every field absent from a document
    written before it existed reads through a `.get()` with the default named beside
    it here, never a fabricated zero (Tech Lead plan, subtask `scorecard`)."""
    human_commits = document.get("human_commits")
    human_lines = document.get("human_lines_changed")
    time_to_merge = document.get("time_to_merge_seconds")
    plan = document.get("estimate")
    actual_files = document.get("actual_files")
    assignment = document.get("assignment")
    split = cycle.time_split(document)
    paused_parts = (split.paused_external_s, split.paused_human_s)
    return TaskFigures(
        cost_usd=document.get("cost_usd", 0.0),
        rework_usd=metrics.rework_cost_usd(document),
        failures_by_reason=metrics.failures_by_reason(document.get("failures", [])),
        status=document.get("status", "open"),
        # A task its slices carried to master (GH-334) was closed without anyone judging a
        # delivery: it counts as no delivery here, whatever else is read off it.
        closed_at=None if document.get("closed_by_slices") else document.get("closed_at"),
        delivery_outcome=document.get("delivery_outcome"),
        human_commits=int(human_commits) if human_commits is not None else None,
        human_lines_changed=int(human_lines) if human_lines is not None else None,
        time_to_merge_seconds=float(time_to_merge) if time_to_merge is not None else None,
        system_commit=document.get("system_commit"),
        system_baseline=document.get("system_baseline"),
        runs=document.get("runs", []),
        estimate_cost_usd=cost_history.declared_estimate(plan) if plan else None,
        estimate_files=int(plan["files"]) if plan else None,
        actual_files=int(actual_files) if actual_files is not None else None,
        planned_size=size_budget.PlanSize.from_frontmatter(document),
        actual_size=size_budget.ActualSize.from_frontmatter(document),
        arm=metrics.arm_of(document),
        experiment_id=assignment.get("experiment") if assignment else None,
        cycle_class=cycle.cycle_class(document),
        cycle_s=cycle.cycle_seconds(document),
        active_s=cycle.active_seconds(document),
        failed_s=split.failed_s,
        waiting_s=split.waiting_s,
        paused_external_s=split.paused_external_s,
        paused_human_s=split.paused_human_s,
        paused_s=None if None in paused_parts else sum(p or 0.0 for p in paused_parts),
        unattributed_s=split.unattributed_s,
        planning_facts=planning.task_planning(document),
    )


def _own_failures(figures: TaskFigures) -> int:
    """This task's own failed attempts: every reason `EXTERNAL_FAILURE_REASONS`
    does not name, `limit` included (ADR-0029; the `Scorecard` glossary entry)."""
    return sum(
        count
        for reason, count in figures.failures_by_reason.items()
        if reason not in EXTERNAL_FAILURE_REASONS
    )


ParsedDocument = tuple[str, ScorecardDocument] | None
"""One task document as `cli._bucket_documents` already parsed it: `(task_id,
document)` on success, `None` for one `document.parse` itself rejected -- malformed
frontmatter (`yaml.YAMLError`), a missing frontmatter block or an unreadable file
(`ValueError`/`OSError`) alike. The caller catches all three and hands this module the
single sentinel; `bucket_by_baseline` never sees, and never needs to see, which one it
was."""


def bucket_by_baseline(
    parsed: Sequence[ParsedDocument], resolve: Callable[[str | None], str]
) -> tuple[dict[str, list[tuple[str, TaskFigures]]], int]:
    """Groups already-parsed task documents by baseline bucket (Tech Lead plan/review,
    subtask `scorecard`): a document's own `system_baseline` wins outright and
    `resolve` is not consulted at all -- a `bl-*` tag, or the literal `untagged`, is
    its own bucket, never folded into `pre-baseline` or `unknown`. A document with no
    `system_baseline` of its own hands its `system_commit` to `resolve` (the caller's
    own `telemetry.resolve_baseline`, injected so this stays pure and testable without
    git): whatever bucket name `resolve` answers, `pre-baseline`/`unknown`/a resolved
    tag alike. `None` in `parsed` -- a document `document.parse` itself rejected -- is
    counted in the returned int and left out of every bucket, never raised here."""
    buckets: dict[str, list[tuple[str, TaskFigures]]] = {}
    unreadable = 0
    for entry in parsed:
        if entry is None:
            unreadable += 1
            continue
        task_id, document = entry
        bucket = document.get("system_baseline") or resolve(document.get("system_commit"))
        buckets.setdefault(bucket, []).append((task_id, task_figures(document)))
    return buckets, unreadable


@dataclass(frozen=True)
class Figure:
    """One scorecard figure: `value` is `None` -- printed `n/a` -- when `denominator`
    is `0`, and is then excluded from the verdict (invariant: no ratio in this diff
    divides without a guarded, printed empty case). `denominator` and `excluded` are
    printed beside every `value` (Tech Lead plan, standard 7): `excluded` is how many
    of the bucket's own documents this figure's denominator left out."""

    value: float | None
    denominator: int
    excluded: int


def _is_judged(figures: TaskFigures) -> bool:
    """A delivery a human has judged: closed, and whose outcome class is not
    `landed_unjudged` (an `auto_merged` delivery landed without a human verdict,
    ADR-0045). An outcome the mapping does not know, or none, keeps today's reading."""
    return bool(figures.closed_at) and figures.delivery_outcome not in LANDED_UNJUDGED


def _ratio(numerator: int, denominator: int, excluded: int) -> Figure:
    if denominator == 0:
        return Figure(None, denominator, excluded)
    return Figure(numerator / denominator, denominator, excluded)


def _average(values: Sequence[float], excluded: int) -> Figure:
    if not values:
        return Figure(None, 0, excluded)
    return Figure(sum(values) / len(values), len(values), excluded)


def _median(values: Sequence[float], excluded: int) -> Figure:
    if not values:
        return Figure(None, 0, excluded)
    return Figure(statistics.median(values), len(values), excluded)


@dataclass(frozen=True)
class Scorecard:
    """ADR-0028's four figures, in order, over one baseline's own documents
    (`core/measurement/CONTEXT.md`'s `Scorecard` entry): autonomy, own failures per
    task, cost per delivery, rework per task. `failures_by_reason` is the per-reason
    breakdown over the same terminated Corridas `own_failures_per_task` reads, for the
    unconditional metric-2 caveat `eval compare` prints beside it (Tech Lead plan: `sdk`
    counts as ours on both sides, a run predating ADR-0029 recorded its 429s as `sdk`
    and is never reclassified). `time_to_merge_known`/`judged` is the diagnostic
    coverage line ("time to merge known for N of M judged deliveries")."""

    autonomy: Figure
    own_failures_per_task: Figure
    cost_per_delivery: Figure
    rework_per_task: Figure
    failures_by_reason: dict[str, int]
    time_to_merge_known: int
    judged: int
    # Informative only (ADR-0054), enters no verdict: the median of actual over the plan's
    # estimate, in cost and in files, over the closed documents that carry both sides of
    # each; `n/a` when none does.
    cost_over_estimate: Figure = Figure(None, 0, 0)
    files_over_estimate: Figure = Figure(None, 0, 0)
    # Informative only (GH-592), enters no verdict: the median of the measured over the planned
    # hand-written files and lines (`size_budget.divergence`), over the closed documents that
    # carry both records and planned more than 0 of it; `n/a` when none does.
    hand_files_over_plan: Figure = Figure(None, 0, 0)
    hand_lines_over_plan: Figure = Figure(None, 0, 0)
    # Informative only (GH-470), enter no verdict: the medians, in hours, of the cycle and of
    # the active time over the tasks `cycle.cycle_class` lets in and whose bound is known;
    # `cycle_closed`/`cycle_open` split the cycle's `n` by how its end was found.
    cycle_h: Figure = Figure(None, 0, 0)
    active_h: Figure = Figure(None, 0, 0)
    # Informative only (GH-467), enter no verdict: the medians, in hours, of the failed, waiting,
    # paused (and its external and human parts) and unattributed time, over the tasks whose
    # figure is known. They are not a partition: the medians of the rows do not add up to
    # `cycle_h`.
    failed_h: Figure = Figure(None, 0, 0)
    waiting_h: Figure = Figure(None, 0, 0)
    paused_h: Figure = Figure(None, 0, 0)
    paused_external_h: Figure = Figure(None, 0, 0)
    paused_human_h: Figure = Figure(None, 0, 0)
    unattributed_h: Figure = Figure(None, 0, 0)
    cycle_closed: int = 0
    cycle_open: int = 0
    active_closed: int = 0
    active_open: int = 0


def _hours(
    figures: Sequence[TaskFigures], seconds: Callable[[TaskFigures], float | None]
) -> Figure:
    """The median, in hours, of one time figure over the tasks where it is known; a task with
    `None` is counted in `excluded`, never as a zero."""
    known = [s / 3600 for f in figures if (s := seconds(f)) is not None]
    return _median(known, excluded=len(figures) - len(known))


def build(figures: Sequence[TaskFigures]) -> Scorecard:
    """The scorecard over one baseline's own `TaskFigures` (already grouped by the
    caller: this function never resolves a baseline itself). `judged` deliveries are
    the ones with a `closed_at` whose outcome class is not `landed_unjudged`: an
    `auto_merged` delivery carries no human verdict, so it is out of autonomy and of
    cost per delivery (ADR-0045). Autonomy prints no rate (`value is None`) while fewer
    than `AUTONOMY_MIN_JUDGED` deliveries are judged; `denominator` still says `n`.
    `terminated` Corridas are the ones whose `status` is `TERMINATED_STATUSES`, and
    they include the auto-merged ones, so own failures and rework still count them. A
    task that has not reached either yet is simply excluded, counted in `excluded`,
    never a zero pretending to be a measurement."""
    total = len(figures)
    judged = [f for f in figures if _is_judged(f)]
    terminated = [f for f in figures if f.status in TERMINATED_STATUSES]

    autonomy = _ratio(
        numerator=sum(1 for f in judged if f.delivery_outcome == "merged"),
        denominator=len(judged),
        excluded=total - len(judged),
    )
    if autonomy.denominator < AUTONOMY_MIN_JUDGED:
        autonomy = Figure(None, autonomy.denominator, autonomy.excluded)
    cost_per_delivery = _average(values=[f.cost_usd for f in judged], excluded=total - len(judged))
    own_failures_per_task = _average(
        values=[float(_own_failures(f)) for f in terminated], excluded=total - len(terminated)
    )
    rework_per_task = _average(
        values=[f.rework_usd for f in terminated], excluded=total - len(terminated)
    )
    cost_ratios = [
        f.cost_usd / f.estimate_cost_usd
        for f in figures
        if f.closed_at is not None and f.estimate_cost_usd
    ]
    files_ratios = [
        f.actual_files / f.estimate_files
        for f in figures
        if f.closed_at is not None and f.estimate_files and f.actual_files is not None
    ]
    cost_over_estimate = _median(cost_ratios, excluded=total - len(cost_ratios))
    files_over_estimate = _median(files_ratios, excluded=total - len(files_ratios))
    gaps = [
        size_budget.divergence(f.planned_size, f.actual_size)
        for f in figures
        if f.closed_at is not None and f.planned_size is not None and f.actual_size is not None
    ]
    hand_files = [g.hand_files for g in gaps if g.hand_files is not None]
    hand_lines = [g.hand_lines for g in gaps if g.hand_lines is not None]
    cycled = [f for f in figures if f.cycle_class != "excluded" and f.cycle_s is not None]
    actives = [f for f in figures if f.cycle_class != "excluded" and f.active_s is not None]
    cycle_h = _median([(f.cycle_s or 0.0) / 3600 for f in cycled], excluded=total - len(cycled))
    active_h = _median([(f.active_s or 0.0) / 3600 for f in actives], excluded=total - len(actives))
    by_reason: dict[str, int] = {}
    for f in terminated:
        for reason, count in f.failures_by_reason.items():
            by_reason[reason] = by_reason.get(reason, 0) + count
    return Scorecard(
        autonomy=autonomy,
        own_failures_per_task=own_failures_per_task,
        cost_per_delivery=cost_per_delivery,
        rework_per_task=rework_per_task,
        failures_by_reason=by_reason,
        time_to_merge_known=sum(1 for f in judged if f.time_to_merge_seconds is not None),
        judged=len(judged),
        cost_over_estimate=cost_over_estimate,
        files_over_estimate=files_over_estimate,
        hand_files_over_plan=_median(hand_files, excluded=total - len(hand_files)),
        hand_lines_over_plan=_median(hand_lines, excluded=total - len(hand_lines)),
        cycle_h=cycle_h,
        active_h=active_h,
        failed_h=_hours(figures, lambda f: f.failed_s),
        waiting_h=_hours(figures, lambda f: f.waiting_s),
        paused_h=_hours(figures, lambda f: f.paused_s),
        paused_external_h=_hours(figures, lambda f: f.paused_external_s),
        paused_human_h=_hours(figures, lambda f: f.paused_human_s),
        unattributed_h=_hours(figures, lambda f: f.unattributed_s),
        cycle_closed=sum(1 for f in cycled if f.cycle_class == "closed"),
        cycle_open=sum(1 for f in cycled if f.cycle_class == "open"),
        active_closed=sum(1 for f in actives if f.cycle_class == "closed"),
        active_open=sum(1 for f in actives if f.cycle_class == "open"),
    )


VerdictOutcome = Literal["improves", "worse", "ties", "undecidable"]


@dataclass(frozen=True)
class Verdict:
    outcome: VerdictOutcome
    reason: str


def _direction(before: float | None, after: float | None) -> Literal["down", "up", "flat", "n/a"]:
    """How `after` moved against `before`, or `n/a` when either side has nothing to
    compare (an empty denominator, already `n/a` in its own `Figure`) -- excluded from
    the verdict, never treated as a tie or as a drop (invariant)."""
    if before is None or after is None:
        return "n/a"
    if after < before:
        return "down"
    if after > before:
        return "up"
    return "flat"


def verdict(frm: Scorecard, frm_baseline: str, to: Scorecard, to_baseline: str) -> Verdict:
    """ADR-0028's own rule: `to` improves over `from` when autonomy does not drop and
    own failures per task drop; cost per delivery and rework per task break a tie.
    Autonomy `n/a` on either side -- fewer than `AUTONOMY_MIN_JUDGED` judged
    deliveries to read a clean merge off --
    makes the whole verdict undecidable, naming the baseline that has none (checked
    `frm` first): never "improves", never "worse" for a scorecard with nothing to
    compare (Tech Lead plan; the round-1 straddle inference is rejected, see the plan's
    `Invariants`). Every other figure that reads `n/a` is left out of the leg it would
    have decided, never coerced into a direction."""
    if frm.autonomy.value is None:
        return Verdict(
            "undecidable",
            f"no autonomy rate under {frm_baseline}: under {AUTONOMY_MIN_JUDGED} judged",
        )
    if to.autonomy.value is None:
        return Verdict(
            "undecidable",
            f"no autonomy rate under {to_baseline}: under {AUTONOMY_MIN_JUDGED} judged",
        )

    autonomy_dir = _direction(frm.autonomy.value, to.autonomy.value)
    failures_dir = _direction(frm.own_failures_per_task.value, to.own_failures_per_task.value)

    if autonomy_dir != "down" and failures_dir == "down":
        return Verdict("improves", "autonomy held or rose, own failures per task dropped")
    if autonomy_dir == "down" and failures_dir == "up":
        return Verdict("worse", "autonomy dropped, own failures per task rose")

    for name, before_after in (
        ("cost per delivery", (frm.cost_per_delivery.value, to.cost_per_delivery.value)),
        ("rework per task", (frm.rework_per_task.value, to.rework_per_task.value)),
    ):
        direction = _direction(*before_after)
        if direction == "down":
            return Verdict("improves", f"autonomy and own failures tied; {name} dropped")
        if direction == "up":
            return Verdict("worse", f"autonomy and own failures tied; {name} rose")
    return Verdict("ties", "no figure moved enough to decide")
