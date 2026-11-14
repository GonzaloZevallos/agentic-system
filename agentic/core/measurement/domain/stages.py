"""The stage claims of a task's cycle (GH-750, step 1): a task document turned into ranked,
clipped intervals, each saying which stage of the cycle it claims. A later step sweeps the
claims into seconds per stage; nothing calls this module yet.

Pure domain, no I/O, one pass over runs, failures, pauses and CI records. Every instant read
from the document enters through `cycle.moment`, so an unreadable one is unknown, never
guessed. The only role-to-stage map in the code is `ROLE_STAGES`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, get_args

from . import cycle, parallel_factor

CycleStage = Literal[
    "queue",
    "definition",
    "plan",
    "development",
    "review",
    "checks",
    "human_wait",
    "account_limit",
    "landing",
    "other",
]
# What an `other` claim is: the time no stage owns (`gap`), a run with no readable role
# (`legacy_role`), a pause that was an outage (`outage_pause`) or the reflector's run.
OtherCause = Literal["gap", "legacy_role", "outage_pause", "reflector"]
StageUnknownCause = Literal[
    "no_merge",
    "no_issue_created",
    "no_pr_created",
    "pause_open",
    "failure_unbounded",
    "ci_wait_unknown",
    "run_unbounded",
    "bounds_reversed",
]


@dataclass(frozen=True)
class StageUnknown:
    """The claims cannot be made: `cause` says which input is missing or unreadable. Unknown,
    never an empty list and never zero."""

    cause: StageUnknownCause


@dataclass(frozen=True)
class Claim:
    """One interval claimed for a stage. The lower the `rank`, the stronger the claim; ties
    between equal ranks are settled by `key`. `cause` is set for an `other` claim only."""

    start: datetime
    end: datetime
    rank: int
    stage: CycleStage
    cause: OtherCause | None


# The stage of every role, keyed by `Role.slug`: the only role-to-stage map in the code. A test
# pins the keys to `roles.ROLES`, so a new role fails until it has a stage.
ROLE_STAGES: dict[str, CycleStage] = {
    **dict.fromkeys(
        (
            "pjm",
            "product_analyst_interviewer",
            "pm_interviewee",
            "product_analyst",
            "pm",
            "ux_designer",
            "ux_lead",
            "ui_designer",
            "ui_lead",
            "pm_consolidator",
            "tech_writer",
            "product_analyst_bug",
            "pm_bug",
        ),
        "definition",
    ),
    **dict.fromkeys(
        (
            "tech_lead_ephemeral",
            "tech_lead_interviewee",
            "tech_lead",
            "tech_lead_ephemeral_platform",
            "tech_lead_interviewee_platform",
            "tech_lead_platform",
            "plan_sizer",
            "tech_lead_critic",
            "tech_lead_critic_platform",
            "pm_consultant",
            "tech_lead_ephemeral_stack",
            "tech_lead_interviewee_stack",
            "tech_lead_stack",
        ),
        "plan",
    ),
    **dict.fromkeys(("developer", "developer_integrator"), "development"),
    **dict.fromkeys(
        (
            "tech_lead_review",
            "tech_lead_review_light",
            "tech_writer_review",
            "ux_lead_final",
            "ui_lead_final",
            "pm_final",
            "qa_performance",
        ),
        "review",
    ),
    "landing_diagnoser": "landing",
    "reflector": "other",
}

# From the strongest claim to the weakest. Between equal ranks the stage nearer the merge wins.
STAGE_PRECEDENCE: tuple[CycleStage, ...] = (
    "human_wait",
    "account_limit",
    "checks",
    "landing",
    "review",
    "development",
    "plan",
    "definition",
    "queue",
    "other",
)
OTHER_ORDER: tuple[OtherCause, ...] = get_args(OtherCause)

# The causes of a mechanical escalation that wait on CI (`CiCause`, prefixed `ci_`).
_CI_CAUSES = frozenset({"ci_gh_unavailable", "ci_gh_unreadable", "ci_run_unfinished"})

RANK_HUMAN, RANK_ACCOUNT, RANK_PAUSE, RANK_LANDING, RANK_CI, RANK_RUN, RANK_QUEUE = range(1, 8)


def key(claim: Claim) -> tuple[int, int, int]:
    """The order of claims over the same instant: rank, then `STAGE_PRECEDENCE`, then
    `OTHER_ORDER` (a claim with no cause reads as `gap`)."""
    return (
        claim.rank,
        STAGE_PRECEDENCE.index(claim.stage),
        OTHER_ORDER.index(claim.cause or "gap"),
    )


def _pause_claim(pause: cycle.Pause) -> tuple[int, CycleStage, OtherCause | None]:
    """The rank, stage and cause of a closed pause. Whether it is human or external is
    `Pause.kind`'s (`rules.pause_kind`); this only subdivides an external one by its failures'
    reasons and its escalation's cause."""
    if pause.kind == "human":
        return RANK_HUMAN, "human_wait", None
    reasons = pause.failure_reasons
    if reasons and all(reason == "account_limit" for reason in reasons):
        return RANK_ACCOUNT, "account_limit", None
    if pause.cause in _CI_CAUSES:
        return RANK_PAUSE, "checks", None
    return RANK_PAUSE, "other", "outage_pause"


def _run_claim(slug: object) -> tuple[CycleStage, OtherCause | None]:
    """The stage of a run or failure by its slug. No slug, or one `ROLE_STAGES` does not know,
    is `legacy_role`; the reflector is the one role in `other`."""
    if not isinstance(slug, str) or slug not in ROLE_STAGES:
        return "other", "legacy_role"
    stage = ROLE_STAGES[slug]
    return stage, ("reflector" if stage == "other" else None)


def _utc(value: datetime) -> datetime:
    """A naive datetime read as UTC; `parallel_factor.clip` raises on mixed awareness."""
    return value if value.utcoffset() is not None else value.replace(tzinfo=UTC)


def _bounded(record: object) -> tuple[datetime, datetime] | None:
    """`[started_at, ended_at]` of a record, `None` when it is not a mapping, a bound is missing
    or does not parse, or the end is before the start."""
    if not isinstance(record, Mapping):
        return None
    started, ended = cycle.moment(record.get("started_at")), cycle.moment(record.get("ended_at"))
    if started is None or ended is None or ended < started:
        return None
    return started, ended


def _records(document: Mapping[str, Any], name: str) -> list[object] | None:
    """The list under `name`, empty when absent; `None` when it holds something else."""
    held = document.get(name)
    if not held:
        return []
    return list(held) if isinstance(held, list | tuple) else None


@dataclass(frozen=True)
class _Timed:
    start: datetime
    end: datetime
    slug: object
    queue_wait_s: object = None


def _read_runs(document: Mapping[str, Any]) -> list[_Timed] | None:
    """Every run with both bounds. Unlike `cycle._active`, which drops a run it cannot read and
    so counts it as no time, a run with a missing or unparsable bound, or that ends before it
    starts, makes the whole read `None` (`run_unbounded`): its seconds are unknown, not zero."""
    records = _records(document, "runs")
    if records is None:
        return None
    runs: list[_Timed] = []
    for record in records:
        span = _bounded(record)
        if span is None or not isinstance(record, Mapping):
            return None
        runs.append(_Timed(*span, record.get("slug"), record.get("queue_wait_s")))
    return runs


def _read_failures(document: Mapping[str, Any]) -> list[_Timed] | None:
    """Every `Failure` with both bounds. Unlike `cycle._failed`, which reads only whether every
    bound is there and has no use for the slug, this one returns the intervals with the failure's
    `slug` (never its `role`), and also refuses an end before the start. `None` is
    `failure_unbounded`: a failure recorded before the bounds existed has unknown seconds."""
    records = _records(document, "failures")
    if records is None:
        return None
    failures: list[_Timed] = []
    for record in records:
        span = _bounded(record)
        if span is None or not isinstance(record, Mapping):
            return None
        failures.append(_Timed(*span, record.get("slug")))
    return failures


def _queue_wait(value: object) -> float:
    """`queue_wait_s` as a non-negative finite number of seconds; a missing, non-numeric (bool
    included), negative or non-finite value reads as 0."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value) if math.isfinite(value) and value > 0 else 0.0


def _queue_claims(runs: list[_Timed], created: datetime) -> list[tuple[datetime, datetime]]:
    """`[issue_created_at, first run started_at]` plus each run's `[started_at - queue_wait_s,
    started_at]`. The wait is cut to what lies after creation, so no timedelta overflows."""
    if not runs:
        return []
    first = min(run.start for run in runs)
    spans = [(created, first)]
    for run in runs:
        room = max(0.0, (run.start - created).total_seconds())
        wait = min(_queue_wait(run.queue_wait_s), room)
        spans.append((run.start - timedelta(seconds=wait), run.start))
    return spans


def claims(
    document: Mapping[str, Any],
    *,
    issue_created_at: datetime | None,
    pr_created_at: datetime | None,
    pr_merged_at: datetime | None,
) -> list[Claim] | StageUnknown:
    """The claims of the task's cycle, clipped to `[issue_created_at, pr_merged_at]`, or a
    `StageUnknown` saying why they cannot be made. Never raises; a naive datetime argument is
    read as UTC.

    In order, the unknowns are: a missing argument (`no_issue_created`, `no_pr_created`,
    `no_merge`); a merge before the issue's creation, or a pull request created after the merge
    (`bounds_reversed`); a pause still open or unreadable (`pause_open`); a failure or a run
    with a missing, unparsable or reversed bound (`failure_unbounded`, `run_unbounded`); a CI
    wait that cannot be derived (`ci_wait_unknown`).

    The ranks: 1 a human pause, 2 an account-limit pause, 3 the other closed pauses (CI ones to
    `checks`, the rest to `other`/`outage_pause`), 4 `[pr_created_at, pr_merged_at]` to
    `landing`, 5 CI waits, 6 runs and failures by `ROLE_STAGES`, 7 the queue.

    `parallel_factor.clip` drops the empty claims silently. The inputs that would invert a range
    are caught first by the validation above, so no claim is ever inverted. The later sweep must
    carry `run_unbounded` and `failure_unbounded` as unknown, never as 0 seconds."""
    if issue_created_at is None:
        return StageUnknown("no_issue_created")
    if pr_created_at is None:
        return StageUnknown("no_pr_created")
    if pr_merged_at is None:
        return StageUnknown("no_merge")
    created, opened, merged = _utc(issue_created_at), _utc(pr_created_at), _utc(pr_merged_at)
    if merged < created or opened > merged:
        return StageUnknown("bounds_reversed")
    try:
        pauses = cycle.pause_intervals(document)
    except (KeyError, TypeError, AttributeError, ValueError):
        return StageUnknown("pause_open")
    if not pauses.complete:
        return StageUnknown("pause_open")
    failures = _read_failures(document)
    if failures is None:
        return StageUnknown("failure_unbounded")
    try:
        waits = cycle.waiting_intervals(document)
    except (KeyError, TypeError, AttributeError, ValueError):
        waits = None
    if waits is None:
        return StageUnknown("ci_wait_unknown")
    runs = _read_runs(document)
    if runs is None:
        return StageUnknown("run_unbounded")

    found: list[Claim] = []

    def add(
        span: tuple[datetime, datetime],
        rank: int,
        stage: CycleStage,
        cause: OtherCause | None = None,
    ) -> None:
        found.extend(
            Claim(start, end, rank, stage, cause)
            for start, end in parallel_factor.clip([span], created, merged)
        )

    for pause in pauses.closed:
        rank, stage, cause = _pause_claim(pause)
        add((pause.start, pause.end), rank, stage, cause)
    add((opened, merged), RANK_LANDING, "landing")
    for wait in waits:
        add(wait, RANK_CI, "checks")
    for timed in [*runs, *failures]:
        stage, cause = _run_claim(timed.slug)
        add((timed.start, timed.end), RANK_RUN, stage, cause)
    for span in _queue_claims(runs, created):
        add(span, RANK_QUEUE, "queue")
    return found
