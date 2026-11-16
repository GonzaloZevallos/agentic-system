"""The scores of one finished task, the identity they are written under and the time they
are stamped with (GH-273, ADR-0037 addendum).

Langfuse widgets aggregate score values only, so the numbers a task's document records
become scores on a carrier trace of their own. Pure: documents and times in, values out.
The live path (at finish and at close) and the backfill call these same functions, so a
number is never computed twice in two ways; nothing here reads the clock, because a score
is stamped with the task's own recorded times, never with the moment of writing.

Identity is deterministic because Langfuse cannot be read back under `events_only`: the
only way a second write is not a duplicate is that it carries the same ids. It includes
the start of the run that produced it, so a fresh re-run or another eval arm never
overwrites another run's scores."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, get_args, get_origin

from agentic.core.process.domain import roles, rules
from agentic.core.process.domain.landing import FOLLOWUP_TITLE_PREFIX
from agentic.core.process.domain.returns import before_pr, is_return
from agentic.core.process.domain.schemas import TaskType
from agentic.core.process.domain.task import (
    ChangeCategory,
    EscalationClass,
    FailureReason,
    TaskState,
)

from . import metrics, stage_report
from .stages import CycleStage, StageSplit, StageUnknown, stage_split

ScoreDataType = Literal["NUMERIC", "CATEGORICAL", "BOOLEAN"]

BASE_SCORE_NAMES: tuple[str, ...] = (
    "cost_usd",
    "cycle_h",
    "active_h",
    "returns",
    "failed_cost_usd",
    "delivered",
    "origin",
    "type",
)
"""The eight scores GH-273 wrote; their ids and order never move."""

ORIGINS: tuple[str, ...] = ("original", "followup")
"""Where a task came from: `_origin` reads nothing else."""

ModelFamily = Literal["opus", "sonnet", "haiku", "other"]
"""The model families a run's cost is grouped into. A model id is open-ended; the family is
closed, and `other` takes whatever no named member claims (GH-283)."""

_MODEL_PREFIX = "claude-"


def members(closed_set: object) -> tuple[str, ...]:
    """The members of a closed `Literal` set, or of a union of them (`ChangeCategory` is
    one: the finding categories plus `definition`)."""
    found: list[str] = []
    for arg in get_args(closed_set):
        found.extend(members(arg) if get_origin(arg) is not None else [str(arg)])
    return tuple(found)


def family_names(prefix: str, members: Iterable[str]) -> tuple[str, ...]:
    """The score names of one family: `prefix.member` for each member of a closed set."""
    return tuple(f"{prefix}.{member}" for member in members)


def model_family(name: str) -> ModelFamily:
    """The family of a model id: the segment right after `claude-` when it names a member
    (`claude-opus-5-5` is `opus`, `claude-haiku-4-5-20251001` is `haiku`); anything else
    (`<synthetic>`, another vendor, a bare `opus`) is `other`."""
    if not name.startswith(_MODEL_PREFIX):
        return "other"
    segment = name.removeprefix(_MODEL_PREFIX).split("-", 1)[0]
    if segment == "opus":
        return "opus"
    if segment == "sonnet":
        return "sonnet"
    if segment == "haiku":
        return "haiku"
    return "other"


FAMILIES: dict[str, tuple[str, ...]] = {
    "returns": family_names("returns", members(ChangeCategory)),
    "failures": family_names("failures", members(FailureReason)),
    "escalations": family_names("escalations", members(EscalationClass)),
    "cost_usd.role": family_names("cost_usd.role", dict.fromkeys(r.slug for r in roles.ROLES)),
    "delivered_cost_usd.type": family_names("delivered_cost_usd.type", members(TaskType)),
    "cost_usd.origin": family_names("cost_usd.origin", ORIGINS),
    "cost_usd.model": family_names("cost_usd.model", members(ModelFamily)),
    "stage_h": family_names("stage_h", members(CycleStage)),
    "slowest_stage": family_names("slowest_stage", members(CycleStage)),
}
"""The per-task families (ADR-0051): one name per member of a closed domain set, never a
literal typed beside the widget that reads it. `README` rows write a family as
`<prefix>.<Set>`; `tests/test_observability_scores.py` expands them from here."""

FIXED_SCORE_NAMES: tuple[str, ...] = (
    "delivered_cost_usd",
    "findings_important",
    "followups",
    "checks_red",
)

STAGE_SCORE_NAMES: tuple[str, ...] = ("cycle_active_h", "stage_split_known")
"""The two fixed scores the dashboard reads beside the `stage_h` family (GH-819), after the
older fixed names so none of them moves."""

SCORE_NAMES: tuple[str, ...] = (
    *BASE_SCORE_NAMES,
    *(name for names in FAMILIES.values() for name in names),
    *FIXED_SCORE_NAMES,
    *STAGE_SCORE_NAMES,
)
"""The closed list of score names; no other name can be produced."""

SCOREABLE_STATUSES: frozenset[str] = frozenset({"ready_for_review", "discarded"})
UNKNOWN = "unknown"
"""What `release` and `baseline` read when the document records none (the close's own)."""

_SECONDS_PER_HOUR = 3600.0
_IDENTITY_PREFIX = "agentic-task"


@dataclass(frozen=True)
class Score:
    """One score. `value` is a number for `NUMERIC`, `1.0`/`0.0` for `BOOLEAN` and the
    category for `CATEGORICAL`. `id` is empty until the score is given an identity."""

    name: str
    value: float | str
    data_type: ScoreDataType
    id: str = ""
    timestamp: datetime = field(kw_only=True)
    """When the score is dated; `score_time` alone chooses it."""

    def __post_init__(self) -> None:
        if self.name not in SCORE_NAMES:
            raise ValueError(f"{self.name!r} is not a task score")


@dataclass(frozen=True)
class TaskTime:
    """The span a task occupies, both ends aware."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class Dimensions:
    """What a widget filters by, read from the document."""

    release: str
    baseline: str
    arm: str


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Identity:
    """The ids of one task incarnation, derived by `identity` alone."""

    trace_id: str
    span_id: str

    def score_id(self, name: str) -> str:
        return _digest("agentic-score", self.trace_id, name)[:32]

    def event_id(self, score_id: str) -> str:
        return _digest("agentic-event", score_id)[:32]


def identity(product: str, task_id: str, start: datetime) -> Identity:
    """The ids of the carrier of the run of `task_id` in `product` that began at `start`.
    The same instant in any offset is the same identity."""
    seed = _digest(_IDENTITY_PREFIX, product, task_id, start.astimezone(UTC).isoformat())
    return Identity(trace_id=seed[:32], span_id=_digest("agentic-span", seed)[:16])


def _parse(value: object) -> datetime | None:
    """An ISO-8601 string as an aware datetime (naive reads as UTC); `None` when it is
    not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


MERGE_DATED_PREFIXES: tuple[str, ...] = ("stage_h.", "slowest_stage.")
MERGE_DATED_NAMES: tuple[str, ...] = ("cycle_h", *STAGE_SCORE_NAMES)


def merge_dated(name: str) -> bool:
    """Whether the score only exists with a merge, so it is dated at the merge instant."""
    return name in MERGE_DATED_NAMES or name.startswith(MERGE_DATED_PREFIXES)


def score_time(name: str, task_time: TaskTime, pr_merged_at: str | None) -> datetime | None:
    """The instant a score is dated at, the only place one is chosen (GH-819). A score that
    needs a merge (`cycle_h`, `stage_h.*`, `cycle_active_h`, `stage_split_known`,
    `slowest_stage.*`) is dated at the pull request's merge, so the weekly trend and the
    date range put the task in the week it landed; `None` when there is no readable merge.
    Every other score keeps the task's own end."""
    if merge_dated(name):
        return _parse(pr_merged_at)
    return task_time.end


def _runs(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(document.get("runs") or [])


def task_time(
    document: Mapping[str, Any], pr_created_at: str | None, issue_created_at: str | None
) -> TaskTime | None:
    """The task's own time: the earliest `started_at` and the latest `ended_at` of its
    runs; a document whose runs carry none (recorded before 2026-09-18) takes the pull
    request's `created_at`, then the issue's, for both ends. `None` when nothing is known;
    never the current time."""
    runs = _runs(document)
    starts = [t for r in runs if (t := _parse(r.get("started_at")))]
    ends = [t for r in runs if (t := _parse(r.get("ended_at")))]
    if starts:
        return TaskTime(min(starts), max(ends or starts))
    if ends:
        return TaskTime(min(ends), max(ends))
    for fallback in (pr_created_at, issue_created_at):
        if (moment := _parse(fallback)) is not None:
            return TaskTime(moment, moment)
    return None


def scoreable(document: Mapping[str, Any]) -> bool:
    """Whether the document is a finished invocation: `ready_for_review` or `discarded`.
    A paused or crashed one writes nothing."""
    return document.get("status") in SCOREABLE_STATUSES


def dimensions(document: Mapping[str, Any]) -> Dimensions:
    """Release, baseline and arm as the document records them, never as the writing
    process sees them."""
    version = document.get("system_version")
    baseline = document.get("system_baseline")
    return Dimensions(
        release=version if isinstance(version, str) and version else UNKNOWN,
        baseline=baseline if isinstance(baseline, str) and baseline else UNKNOWN,
        arm=metrics.arm_of(document),
    )


def _active_hours(document: Mapping[str, Any]) -> float | None:
    """Run time without queue wait, in hours; `None` when there is no run or any run lacks
    a readable timestamp (never `0` in its place)."""
    runs = _runs(document)
    if not runs:
        return None
    total = 0.0
    for run in runs:
        started, ended = _parse(run.get("started_at")), _parse(run.get("ended_at"))
        if started is None or ended is None:
            return None
        total += (ended - started).total_seconds() - float(run.get("queue_wait_s") or 0.0)
    return total / _SECONDS_PER_HOUR


def stage_outcome(
    document: Mapping[str, Any],
    *,
    issue_created_at: str | None,
    pr_created_at: str | None,
    pr_merged_at: str | None,
) -> StageSplit | StageUnknown:
    """The stage split of the document between the issue's creation and the merge, from the
    three tracker strings read only with `_parse` (an unreadable one is unknown, never guessed).
    The seconds are `stages.stage_split`'s; nothing here recomputes them."""
    return stage_split(
        document,
        issue_created_at=_parse(issue_created_at),
        pr_created_at=_parse(pr_created_at),
        pr_merged_at=_parse(pr_merged_at),
    )


def stage_row(
    task_id: str,
    document: Mapping[str, Any],
    *,
    issue_created_at: str | None,
    pr_created_at: str | None,
    pr_merged_at: str | None,
) -> stage_report.StageRow:
    """The report row of a document. Never raises on a legacy document: runs that are not a
    list of mappings leave `active_h` as `None`."""
    try:
        active_h = _active_hours(document)
    except (AttributeError, TypeError, ValueError, OverflowError):
        active_h = None
    return stage_report.StageRow(
        task_id,
        stage_outcome(
            document,
            issue_created_at=issue_created_at,
            pr_created_at=pr_created_at,
            pr_merged_at=pr_merged_at,
        ),
        active_h,
    )


def _stage_hours(split: StageSplit | StageUnknown) -> dict[str, float]:
    """`stage_h.<stage>` for every name of the family, or none when the split is unknown."""
    if isinstance(split, StageUnknown):
        return {}
    hours: dict[str, float] = {str(stage): h for stage, h in stage_report.hours(split).items()}
    return {name: hours[name.removeprefix("stage_h.")] for name in FAMILIES["stage_h"]}


def _slowest_stage(stage_hours: Mapping[str, float], cycle_h: float | None) -> dict[str, float]:
    """`slowest_stage.<stage>: cycle_h` under the stage with the most hours; a tie goes to
    the first stage in `CycleStage` order (the family's own order). Nothing without hours."""
    if not stage_hours or cycle_h is None:
        return {}
    names = FAMILIES["stage_h"]
    slowest = max(names, key=lambda name: (stage_hours[name], -names.index(name)))
    return {f"slowest_stage.{slowest.removeprefix('stage_h.')}": cycle_h}


def _active_cycle(stage_hours: Mapping[str, float], cycle_h: float | None) -> float | None:
    """The cycle without the two waits a person or the account cause, from the same hours
    as `stage_h`; `None` without a split or a cycle."""
    if not stage_hours or cycle_h is None:
        return None
    return cycle_h - stage_hours["stage_h.human_wait"] - stage_hours["stage_h.account_limit"]


def _cycle_hours(issue_created_at: str | None, pr_merged_at: str | None) -> float | None:
    created, merged = _parse(issue_created_at), _parse(pr_merged_at)
    if created is None or merged is None:
        return None
    return (merged - created).total_seconds() / _SECONDS_PER_HOUR


def _list(value: object) -> list[Any]:
    """A recorded register as a list; anything else (a hand-edited document) is none."""
    return value if isinstance(value, list) else []


def _entries(value: object) -> list[Mapping[str, Any]]:
    return [e for e in _list(value) if isinstance(e, Mapping)]


def _counts(members: Iterable[object], family: str) -> dict[str, float]:
    """`family.member: occurrences` for each member of the family that occurred; one that
    did not, or one outside the closed set, leaves no entry."""
    tally: dict[str, float] = {}
    for member in members:
        name = f"{family}.{member}"
        if name in FAMILIES[family]:
            tally[name] = tally.get(name, 0.0) + 1.0
    return tally


def _returns_by_category(document: Mapping[str, Any]) -> dict[str, float]:
    """The categories of `change_history`, without the landing's own reopens (the same
    rule `metrics.changes_by_category` follows), so the parts sum to `returns` whenever
    the history is complete."""
    history = (e.get("category") for e in _entries(document.get("change_history")) if is_return(e))
    return _counts(history, "returns")


def _failures_by_reason(document: Mapping[str, Any]) -> dict[str, float]:
    return _counts((f.get("reason") for f in _entries(document.get("failures"))), "failures")


def _escalations_by_class(document: Mapping[str, Any]) -> dict[str, float]:
    log = _entries(document.get("pause_log"))
    return _counts((e.get("value") for e in log if e.get("kind") == "escalation"), "escalations")


def _cost_by_role(document: Mapping[str, Any]) -> dict[str, float]:
    spent: dict[str, float] = {}
    for run in _runs(document):
        name = f"cost_usd.role.{run.get('slug')}"
        if name in FAMILIES["cost_usd.role"]:
            spent[name] = spent.get(name, 0.0) + float(run.get("cost_usd") or 0.0)
    return spent


def _cost_by_model(document: Mapping[str, Any]) -> dict[str, float]:
    """The delivering attempt's model usage: every run's `cost_by_model`, summed into the
    family of each model id. A run without it adds nothing, so a document where no run
    has it emits none of the family."""
    spent: dict[str, float] = {}
    for run in _runs(document):
        by_model = run.get("cost_by_model")
        if not isinstance(by_model, Mapping):
            continue
        for model, cost in by_model.items():
            name = f"cost_usd.model.{model_family(str(model))}"
            spent[name] = spent.get(name, 0.0) + float(cost or 0.0)
    return spent


def _findings_important(document: Mapping[str, Any]) -> float | None:
    """Important findings, folded the way the findings table folds them (one row per
    location and claim, whatever pass or subtask raised it); `None` without a `findings`
    register."""
    if not isinstance(document.get("findings"), list):
        return None
    state: TaskState = {
        "id": str(document.get("id") or ""),
        "title": str(document.get("title") or ""),
        "description": "",
        "findings": _list(document.get("findings")),
        "settled_findings": _list(document.get("settled_findings")),
        "approval_settled": _list(document.get("approval_settled")),
    }
    rows = rules.findings_rows(state)
    return float(sum(1 for row in rows if row["severity"] == "important"))


def _followups(document: Mapping[str, Any]) -> float | None:
    """Follow-ups the landing filed: `1` when `followups_filed` is set, `0` once a
    `landing` record written under a pull request shows the landing ran and set none (the
    pass before the pull request is no landing run), `None` on a document the landing
    never touched (the absence of the flag there is not knowledge)."""
    if document.get("followups_filed") is True:
        return 1.0
    entries = document.get("landing")
    ran = any(
        isinstance(entry, Mapping) and not before_pr(entry)
        for entry in (entries if isinstance(entries, list) else [])
    )
    return 0.0 if ran else None


def _checks_red(document: Mapping[str, Any]) -> float | None:
    """Merge checks that failed, from `merge_checks.checks[].passed is False` (the record
    `merge.py` writes at the merge step, ADR-0045). The Developer's presubmit runs are not
    in the document, so they are not counted. `None` without a readable record."""
    record = document.get("merge_checks")
    checks = record.get("checks") if isinstance(record, Mapping) else None
    if not isinstance(checks, list):
        return None
    return float(sum(1 for c in checks if isinstance(c, Mapping) and c.get("passed") is False))


def task_scores(
    document: Mapping[str, Any],
    *,
    task_time: TaskTime,
    pr_merged_at: str | None = None,
    issue_created_at: str | None = None,
    pr_created_at: str | None = None,
    identity: Identity | None = None,
) -> list[Score]:
    """The scores the document and the tracker reads support, in `SCORE_NAMES` order.
    A score whose input is missing is left out, never written as `0` or `false`. With an
    `identity` each score carries its id, and each carries the `score_time` it is dated at;
    a score that needs a merge is left out when there is none. The `stage_h` family is all
    ten hours from one `StageSplit` or none: an unknown split, or no merge, writes nothing,
    and `cycle_active_h` and `slowest_stage.*` come from that same split. `stage_split_known`
    says which of the two a merged task got."""
    cost = float(document.get("cost_usd") or 0.0)
    delivered = _delivered(document)
    cycle_h = _cycle_hours(issue_created_at, pr_merged_at)
    split = stage_outcome(
        document,
        issue_created_at=issue_created_at,
        pr_created_at=pr_created_at,
        pr_merged_at=pr_merged_at,
    )
    stage_hours = _stage_hours(split)
    task_type, origin = document.get("type") or None, _origin(document)
    values: dict[str, tuple[float | str | None, ScoreDataType]] = {
        "cost_usd": (cost, "NUMERIC"),
        "cycle_h": (cycle_h, "NUMERIC"),
        "active_h": (_active_hours(document), "NUMERIC"),
        "returns": (metrics.changes_requested_total(document), "NUMERIC"),
        "failed_cost_usd": (
            sum(float(f.get("cost_usd") or 0.0) for f in _entries(document.get("failures"))),
            "NUMERIC",
        ),
        "delivered": (delivered, "BOOLEAN"),
        "origin": (origin, "CATEGORICAL"),
        "type": (task_type, "CATEGORICAL"),
        "delivered_cost_usd": (cost if delivered == 1.0 else None, "NUMERIC"),
        "findings_important": (_findings_important(document), "NUMERIC"),
        "followups": (_followups(document), "NUMERIC"),
        "checks_red": (_checks_red(document), "NUMERIC"),
        "cycle_active_h": (_active_cycle(stage_hours, cycle_h), "NUMERIC"),
        "stage_split_known": (
            None if _parse(pr_merged_at) is None else float(bool(stage_hours)),
            "BOOLEAN",
        ),
    }
    numeric: dict[str, float] = {
        **_returns_by_category(document),
        **_failures_by_reason(document),
        **_escalations_by_class(document),
        **_cost_by_role(document),
        **_cost_by_model(document),
    }
    if delivered == 1.0:
        numeric[f"delivered_cost_usd.type.{task_type}"] = cost
    numeric[f"cost_usd.origin.{origin}"] = cost
    numeric.update(stage_hours)
    numeric.update(_slowest_stage(stage_hours, cycle_h))
    values.update({name: (value, "NUMERIC") for name, value in numeric.items()})
    scores: list[Score] = []
    for name in SCORE_NAMES:
        if name not in values or (value := values[name][0]) is None:
            continue
        if (stamp := score_time(name, task_time, pr_merged_at)) is None:
            continue
        scores.append(
            Score(
                name=name,
                value=value,
                data_type=values[name][1],
                id=identity.score_id(name) if identity else "",
                timestamp=stamp,
            )
        )
    return scores


def _delivered(document: Mapping[str, Any]) -> float | None:
    outcome = document.get("delivery_outcome")
    if outcome not in get_args(metrics.DeliveryOutcome):
        return None
    return 0.0 if outcome == "closed" else 1.0


def _origin(document: Mapping[str, Any]) -> str | None:
    title = document.get("title")
    if not title:
        return None
    return ORIGINS[1] if str(title).startswith(FOLLOWUP_TITLE_PREFIX) else ORIGINS[0]
