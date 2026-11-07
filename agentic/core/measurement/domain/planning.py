"""What planning cost and returned, by arm and by route, read over task documents (GH-472).

`eval compare` hands each task document's frontmatter here. A read over `runs[]`,
`return_history`, `estimate` and `plan_route`, nothing recorded: no span attribute, metric,
label or document key is added (ADR-0037, ADR-0086). Pure domain.

Planning cost is the sum of `cost_usd` over the runs of `PLANNING_SLUGS`; the sizer is not one
(its own column) and neither is the PM consultant, who answers the Tech Lead but is not a
planning seat. Planning time is the union of those runs' intervals, so two seats working at
once count once, and a wait between them counts for nothing (the same rule as
`parallel_factor`). A document without `plan_route` was not sized: it falls in `UNSIZED`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from agentic.core.process.domain import cost_history, returns
from agentic.core.process.domain.task import RoleRun

from . import metrics, parallel_factor

# The roles that plan a task, interview or critic route alike (the stack review's seats are
# not a task's planning). A test pins every slug to `ROLES`.
PLANNING_SLUGS: tuple[str, ...] = (
    "tech_lead_ephemeral",
    "tech_lead_ephemeral_platform",
    "tech_lead_interviewee",
    "tech_lead_interviewee_platform",
    "tech_lead",
    "tech_lead_platform",
    "tech_lead_critic",
    "tech_lead_critic_platform",
)
SIZER_SLUG = "plan_sizer"
UNSIZED = "unsized"
PLAN_CALIBRATION = "plan-calibration"
FAILED = "failed"


@dataclass(frozen=True)
class TaskPlanning:
    """One task's planning facts: the arm it ran under, the bucket (its recorded route, or
    `UNSIZED`) and the figures `PlanningFigures` sums."""

    arm: str
    bucket: str
    plan_returns: int
    planning_cost_usd: float
    planning_time_s: float
    sizing_cost_usd: float
    misrouted: bool
    critic_failed: bool
    pair: tuple[float, float] | None


@dataclass(frozen=True)
class PlanningFigures:
    """The figures of a set of tasks: sums, with the per-task means derived. A mean is
    `None` when there is no task, never a zero."""

    tasks: int = 0
    plan_returns: int = 0
    planning_cost_usd: float = 0.0
    planning_time_s: float = 0.0
    sizing_cost_usd: float = 0.0
    misrouted: int = 0
    critic_failed: int = 0
    # `(pre_estimate_usd, declared estimate)` of the tasks that have both, in document order.
    pairs: tuple[tuple[float, float], ...] = ()

    def _per_task(self, total: float) -> float | None:
        return total / self.tasks if self.tasks else None

    @property
    def returns_per_task(self) -> float | None:
        return self._per_task(self.plan_returns)

    @property
    def cost_per_task(self) -> float | None:
        return self._per_task(self.planning_cost_usd)

    @property
    def time_per_task_s(self) -> float | None:
        return self._per_task(self.planning_time_s)

    def plus(self, task: TaskPlanning) -> PlanningFigures:
        return replace(
            self,
            tasks=self.tasks + 1,
            plan_returns=self.plan_returns + task.plan_returns,
            planning_cost_usd=self.planning_cost_usd + task.planning_cost_usd,
            planning_time_s=self.planning_time_s + task.planning_time_s,
            sizing_cost_usd=self.sizing_cost_usd + task.sizing_cost_usd,
            misrouted=self.misrouted + task.misrouted,
            critic_failed=self.critic_failed + task.critic_failed,
            pairs=self.pairs if task.pair is None else (*self.pairs, task.pair),
        )


def _plan_returns(document: Mapping[str, Any]) -> int:
    return sum(
        1
        for entry in document.get("return_history") or []
        if returns.is_return(entry) and entry.get("category") == PLAN_CALIBRATION
    )


def _cost(runs: Iterable[RoleRun]) -> float:
    return sum(float(run.get("cost_usd") or 0.0) for run in runs)


def _pair(plan_route: Mapping[str, Any], document: Mapping[str, Any]) -> tuple[float, float] | None:
    pre, estimate = plan_route.get("pre_estimate_usd"), document.get("estimate")
    if pre is None or not estimate or estimate.get("cost_usd") is None:
        return None
    return float(pre), cost_history.declared_estimate(estimate)


def task_planning(document: Mapping[str, Any]) -> TaskPlanning:
    """The planning facts of one document. A field absent from a document written before it
    existed reads as nothing: no cost, no return, no pair."""
    runs: list[RoleRun] = list(document.get("runs") or [])
    planned = [run for run in runs if run.get("slug") in PLANNING_SLUGS]
    sized = [run for run in runs if run.get("slug") == SIZER_SLUG]
    plan_route = document.get("plan_route")
    sizer_ran = isinstance(plan_route, Mapping)
    route = plan_route if isinstance(plan_route, Mapping) else {}
    return TaskPlanning(
        arm=metrics.arm_of(document),
        bucket=str(route.get("route") or UNSIZED) if sizer_ran else UNSIZED,
        plan_returns=_plan_returns(document),
        planning_cost_usd=_cost(planned),
        planning_time_s=parallel_factor.union_seconds(
            i for run in planned if (i := parallel_factor.interval(run))
        ),
        sizing_cost_usd=_cost(sized),
        misrouted=bool(route.get("misrouted")),
        critic_failed=route.get("critic") == FAILED,
        pair=_pair(route, document) if sizer_ran else None,
    )


def aggregate(tasks: Iterable[TaskPlanning]) -> dict[str, PlanningFigures]:
    """The figures of `tasks` by bucket, in order of first appearance."""
    out: dict[str, PlanningFigures] = {}
    for task in tasks:
        out[task.bucket] = out.get(task.bucket, PlanningFigures()).plus(task)
    return out


def table(documents: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, PlanningFigures]]:
    """The per-arm table (`metrics.arm_of`, never an arm's name): arm, then bucket, then
    the figures. The unsized bucket holds every document without a `plan_route`."""
    by_arm: dict[str, list[TaskPlanning]] = {}
    for document in documents:
        task = task_planning(document)
        by_arm.setdefault(task.arm, []).append(task)
    return {arm: aggregate(tasks) for arm, tasks in by_arm.items()}


def _mean(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def lines(buckets: Mapping[str, PlanningFigures]) -> list[str]:
    """The printed block of one arm: a header, a row per bucket (`interview`, `critic`, then
    `unsized`) and, for each bucket with any, its sizer/plan pairs as `pre->plan` USD. Empty
    for an arm with no task. Means are per task, `n/a` when there is none."""
    if not buckets:
        return []
    out = [
        f"{'route':<10}{'tasks':>6}{'plan_ret':>9}{'ret/task':>9}{'plan_usd':>10}"
        f"{'usd/task':>10}{'plan_s':>9}{'s/task':>9}{'sizing_usd':>11}{'misrouted':>10}"
        f"{'critic_failed':>14}"
    ]
    for bucket in sorted(buckets, key=lambda b: (b == UNSIZED, b)):
        f = buckets[bucket]
        out.append(
            f"{bucket:<10}{f.tasks:>6}{f.plan_returns:>9}{_mean(f.returns_per_task, '.2f'):>9}"
            f"{f.planning_cost_usd:>10.4f}{_mean(f.cost_per_task, '.4f'):>10}"
            f"{f.planning_time_s:>9.0f}{_mean(f.time_per_task_s, '.0f'):>9}"
            f"{f.sizing_cost_usd:>11.4f}{f.misrouted:>10}{f.critic_failed:>14}"
        )
    for bucket in sorted(buckets, key=lambda b: (b == UNSIZED, b)):
        if buckets[bucket].pairs:
            pairs = ", ".join(f"{pre:.2f}->{plan:.2f}" for pre, plan in buckets[bucket].pairs)
            out.append(f"sizer->plan pairs [{bucket}]: {pairs}")
    return out
