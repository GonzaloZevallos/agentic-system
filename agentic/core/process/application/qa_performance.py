"""QA Performance's node (GH-318, ADR-0060), run by the graph when `rules.qa_performance_due`.

The system measures the integrated head and the role reads the result; nothing the role says
about a number decides anything. In order: resolve the full sha of the task checkout (the sha
only, its tree is never touched); read the stack's `performance` at that head and at the
task's base; for each scenario the *plan* convened (`TechPlan.performance`, the only source
of the limits and the volume) build either a `performance.Scenario` or a blocking fact
(missing at the head, limits mismatch, limits loosened since the base); measure every built
one through `perf.measure_at` -- a throwaway `perf-` worktree under the `PerfLock`, never the
task's or a subtask's checkout; write the table into the role's section; run the role.

**Verdict**, stored on the run with the sha it measured (`perf_verdict`, `perf_head`):
`returned` on any `over` budget, any fact or a blocking finding of the role; else `approved`
when every budget is `within`; else `unverified` (an `inconclusive` alone never blocks and
never approves). `measure_at` runs in a worker
thread (GH-385), so the event loop stays free for the run's stop. A `RunStopped` (the run's stop,
seen from a perf command or the lock wait) propagates: nothing was written before `measure_at`
returned, so no table and no run exist and `qa_performance_due` stays true; its `finally` has
already removed the throwaway worktree and released the lock.

On `returned`, `integration` (or the single subtask) is reopened with `returned_by:
'qa_performance'`, counted on `PAIR_QA_PERFORMANCE_TECH_LEAD`. The claims of the rows the
system raises are built here from facts with fixed templates, never worded by the model, so
the same row keeps its key across returns; an approving run settles them in the fold
(`rules.qa_settles`)."""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import anyio
import anyio.to_thread

from agentic.shared.configuration import Configuration

from ..domain import performance, rules
from ..domain.performance import Limit, Result, Run, Scenario
from ..domain.roles import PAIR_QA_PERFORMANCE_TECH_LEAD, QA_PERFORMANCE
from ..domain.schemas import PerfScenario
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, TaskState, apply
from .checkouts import SetupFailed, task_base, task_checkout_in_thread
from .perf import PerfRefused, measure_at
from .ports import Ports
from .run_role import finish, run_role, with_context

Verdict = Literal["approved", "returned", "unverified"]
Fact = Literal["missing", "mismatch", "loosened"]
FACT_TEMPLATES: dict[Fact, str] = {
    "missing": "{name} missing at head",
    "mismatch": "{name} limits mismatch",
    "loosened": "{name} limits loosened",
}
CLOSE_THE_PR = (
    "A budget is read-only after planning: a different limit means closing the pull request "
    "and re-filing the issue."
)
_HEADER = (
    "| Scenario | Metric | Value | Limit | Unit | Outcome | Reason | Spread | Load |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)


@dataclass(frozen=True)
class Row:
    """A blocking row the system builds: its fixed claim and its category."""

    claim: str
    category: Literal["spec-scope", "standard-other"]


async def qa_performance(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    runner, lock = ports.perf_runner, ports.perf_lock
    if runner is None or lock is None:
        raise RuntimeError("QA Performance needs a PerfRunner and a PerfLock in the ports")
    try:
        task = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(QA_PERFORMANCE), ports)
    head = ports.workspace.full_head(task.path)
    plan = [PerfScenario.model_validate(raw) for raw in state.get("performance", [])]
    scenarios, facts = _scenarios(
        plan, _declared(ports, head), _declared(ports, task_base(state, ports))
    )
    results: list[Result] = []
    if scenarios:
        try:
            results, _ = await anyio.to_thread.run_sync(
                functools.partial(
                    measure_at,
                    head,
                    scenarios,
                    ports.stack.setup(head),
                    (),
                    cfg.perf_lock_wait_s,
                    2,
                    ports,
                    runner,
                    lock,
                    cfg,
                )
            )
        except PerfRefused:
            results = [_broken(s, cfg) for s in scenarios]
    rows = [*(Row(_claim(fact, name), _category(fact)) for fact, name in facts), *_overs(results)]
    preface = _with_facts(_preface(head, results), rows)
    prompt = with_context(
        apply(state, {"sections": {QA_PERFORMANCE.name: preface}}),
        QA_PERFORMANCE,
        ports,
        task.path,
        f"The measured head is {head}.",
    )
    output, patch = await run_role(state, QA_PERFORMANCE, cfg, ports, cwd=task.path, prompt=prompt)
    if output is None:
        return finish(state, patch, ports)
    blocking = _role_blocking(output.data, rows)
    verdict = _verdict(results, rows, blocking)
    text = f"{preface}\n\n{patch['sections'][QA_PERFORMANCE.name]}"
    patch["sections"] = rules.sections_for(state, QA_PERFORMANCE.name, text)
    patch["runs"] = [{**patch["runs"][0], "perf_verdict": verdict, "perf_head": head}]
    if verdict == "returned":
        patch = apply(patch, _return(state, cfg, rows, blocking, preface))
    return finish(state, patch, ports)


def _declared(ports: Ports, revision: str) -> dict[str, Scenario]:
    try:
        return ports.stack.performance(revision)
    except ValueError:
        return {}


def _limits(plan: PerfScenario) -> dict[str, Limit]:
    return {b.metric: Limit(b.limit, b.unit) for b in plan.budgets}


def _scenarios(
    plan: Sequence[PerfScenario], at_head: dict[str, Scenario], at_base: dict[str, Scenario]
) -> tuple[list[Scenario], list[tuple[Fact, str]]]:
    """The scenarios to measure, in the plan's order, and the facts for those that cannot be.
    The head's commands with the plan's limits and volume; a scenario the plan does not name
    is ignored."""
    built: list[Scenario] = []
    facts: list[tuple[Fact, str]] = []
    for item in plan:
        limits = _limits(item)
        declared = at_head.get(item.name)
        if declared is None:
            facts.append(("missing", item.name))
        elif performance.limits_mismatch(limits, declared.limits):
            facts.append(("mismatch", item.name))
        elif (base := at_base.get(item.name)) is not None and _loosened(base, declared):
            facts.append(("loosened", item.name))
        else:
            built.append(
                Scenario(
                    item.name,
                    declared.seed,
                    declared.measure,
                    item.volume,
                    item.volume_unit,
                    limits,
                )
            )
    return built, facts


def _loosened(base: Scenario, head: Scenario) -> bool:
    return any(
        (old := base.limits.get(name)) is not None
        and old.unit == new.unit
        and new.limit > old.limit
        for name, new in head.limits.items()
    )


def _claim(fact: Fact, name: str) -> str:
    return FACT_TEMPLATES[fact].format(name=name)


def _category(fact: Fact) -> Literal["spec-scope", "standard-other"]:
    return "standard-other" if fact == "loosened" else "spec-scope"


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else repr(value)


def _overs(results: Sequence[Result]) -> list[Row]:
    return [
        Row(
            f"{r.scenario}/{m.metric} over {_number(m.limit.limit)}{m.limit.unit}",
            "standard-other",
        )
        for r in results
        for m in r.metrics
        if m.outcome == "over"
    ]


def _broken(scenario: Scenario, cfg: Configuration) -> Result:
    """The throwaway checkout could not be made: nothing was measured, which is not a
    verdict on the code."""
    return performance.verdict(scenario, Run(None, 1, False, None, None), [], cfg.perf_spread_max)


def _preface(head: str, results: Sequence[Result]) -> str:
    lines = [_HEADER] if results else []
    for r in results:
        for m in r.metrics:
            value = "-" if m.median is None else _number(round(m.median, 3))
            spread = "-" if m.spread is None else f"{m.spread:.3f}"
            load = f"{_load(r.load_before)}/{_load(r.load_after)}"
            reason = m.reason or r.reason or ""
            lines.append(
                f"| {r.scenario} | {m.metric} | {value} | {_number(m.limit.limit)} "
                f"| {m.limit.unit} | {m.outcome} | {reason} | {spread} | {load} |"
            )
    table = "\n".join(lines) if lines else "No scenario was measured."
    return f"Measured head: `{head}`\n\n{table}"


def _load(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _with_facts(preface: str, rows: Sequence[Row]) -> str:
    if not rows:
        return preface
    listed = "\n".join(f"- {row.claim}" for row in rows)
    return f"{preface}\n\n### Facts\n\n{listed}"


def _role_blocking(data: dict[str, Any], rows: Sequence[Row]) -> list[str]:
    """The role's own blocking claims as written, less the ones that repeat a fact the
    system already raised (the prompt tells it to report those verbatim)."""
    known = {" ".join(row.claim.split()).casefold() for row in rows}
    claims: list[str] = []
    for finding in data["findings"]:
        claim = finding["claim"]
        if finding["severity"] == "blocking" and " ".join(claim.split()).casefold() not in known:
            claims.append(claim)
    return claims


def _verdict(results: Sequence[Result], rows: Sequence[Row], blocking: Sequence[str]) -> Verdict:
    if rows or blocking:
        return "returned"
    if results and all(m.outcome == "within" for r in results for m in r.metrics):
        return "approved"
    return "unverified"


def _return(
    state: TaskState, cfg: Configuration, rows: Sequence[Row], blocking: Sequence[str], table: str
) -> TaskState:
    subtasks = state.get("subtasks", {})
    target = INTEGRATION if INTEGRATION in subtasks else next(iter(subtasks))
    claims = [row.claim for row in rows] + list(blocking)
    reason = "; ".join(claims)
    patch: TaskState = {
        "subtasks": {
            target: {
                **subtasks[target],
                "status": "returned",
                "reason": reason,
                "returned_by": "qa_performance",
            }
        }
    }
    version = rules.next_version(state, QA_PERFORMANCE.name)
    categories = [row.category for row in rows] + ["standard-other"] * len(blocking)
    patch["findings"] = [
        {
            "subtask": target,
            "version": version,
            "location": QA_PERFORMANCE.name,
            "claim": claim,
            "severity": "blocking",
            "scope": "plan",
            "category": category,
            "status": "open",
        }
        for claim, category in zip(claims, categories, strict=True)
    ]
    pair = PAIR_QA_PERFORMANCE_TECH_LEAD
    limit = cfg.max_changes_requested_per_pair
    if rules.remaining_changes(state, pair, limit) > 0:
        patch["changes_requested"] = rules.record_return(state, pair)
        patch["change_history"] = [rules.change_entry(pair, "standard-other", reason)]
    else:
        text = f"{rules.limit_reached(pair, limit, reason)}\n\n{table}\n\n{CLOSE_THE_PR}"
        patch |= rules.escalation("returns", text)
        patch = rules.logged(patch)
        patch["status"] = WAITING_ON_HUMAN
    return patch
