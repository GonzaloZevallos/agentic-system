"""PM's early, UI-only final validation (ADR-0040): between the approving review of
the whole branch and `deliver`, only for a feature PM flagged `touches_ui`
(`rules.final_validation_due`). UX Lead walks the sample app the product's `design`
block may declare, Design Lead judges the comparisons at equal-to-the-eye fidelity,
and PM decides -- consulting both, never running them as a role of their own.

The sample app is the system's, never a role's (§8): it is started here, in the
checkout holding the integrated piece (`task_checkout`, the same one `deliver` uses),
and stopped in a `finally` so it never outlives this node, whatever PM's verdict was. A
server that fails to start, or that the product never declared, is not a role failure:
it is written into UX Lead's own section instead, the same system-written-line
treatment `NO_DESIGN_NOTE` already gets.

On `returned`, PM reopens `integration`, or the single subtask when there is none, so
the fan-in's own routing (`rules.dispatched_subtasks`) sends it back to its Developer;
the pair is PM's own, to Tech Lead (`PAIR_PM_TECH_LEAD`), never a lead's own pair and
never `pair_for`'s per-subtask one -- leads never return to a Developer (README.md, 4,
§13). Every one of PM's blocking claims enters the findings table as `spec-behaviour`,
`scope: plan`, `open`: the integration review that follows settles them explicitly,
the same channel any other plan-scoped finding already travels."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.design import NO_SAMPLE_APP_NOTE, Design, compare_facts, design_facts, design_rules
from ..domain.roles import DESIGN_LEAD_FINAL, PAIR_PM_TECH_LEAD, PM_FINAL, UX_LEAD_FINAL
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, TaskState, apply
from .checkouts import SetupFailed, task_base, task_checkout
from .ports import Ports, SampleAppHandle
from .run_role import finish, run_role, with_context

Started = SampleAppHandle | str | None


async def final_validation(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(PM_FINAL), ports)
    design_block = ports.stack.design(task_base(state, ports))
    started = _start(design_block, ports, task.path)
    try:
        patch = await _ux_lead_final(state, cfg, ports, task.path, design_block, started)
        state = apply(state, patch)

        design_patch = await _design_lead_final(state, cfg, ports, task.path, design_block)
        state = apply(state, design_patch)
        patch = apply(patch, design_patch)

        output, pm_patch = await run_role(state, PM_FINAL, cfg, ports, cwd=task.path)
        patch = apply(patch, pm_patch)
        if output is None:
            return finish(state, patch, ports)
        patch = apply(patch, _verdict_patch(state, cfg, output.data))
        return finish(state, patch, ports)
    finally:
        if not isinstance(started, str) and started is not None:
            ports.sample_app.stop(started)


def _start(design_block: Design | None, ports: Ports, cwd: Path) -> Started:
    """`None` when the product declares no `sample_app` at all -- nothing to start and
    nothing to stop; a handle or a refusal otherwise."""
    if design_block is None or design_block.sample_app is None:
        return None
    return ports.sample_app.start(design_block.sample_app, cwd)


async def _ux_lead_final(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    design_block: Design | None,
    started: Started,
) -> TaskState:
    facts = _app_facts(design_block, started)
    prompt = with_context(state, UX_LEAD_FINAL, ports, cwd, facts)
    _, patch = await run_role(state, UX_LEAD_FINAL, cfg, ports, cwd=cwd, prompt=prompt)
    if design_block is None or design_block.sample_app is None or isinstance(started, str):
        note = started if isinstance(started, str) else NO_SAMPLE_APP_NOTE
        written = patch["sections"][UX_LEAD_FINAL.name]
        patch = apply(
            patch,
            {"sections": {UX_LEAD_FINAL.name: f"{rules.system_note(note)}\n\n{written}"}},
        )
    return patch


def _app_facts(design_block: Design | None, started: Started) -> str:
    if design_block is None or design_block.sample_app is None:
        return NO_SAMPLE_APP_NOTE
    if isinstance(started, str):
        return started
    assert started is not None
    return "The sample app's first lines on start:\n\n" + "\n".join(started.lines)


async def _design_lead_final(
    state: TaskState, cfg: Configuration, ports: Ports, cwd: Path, design_block: Design | None
) -> TaskState:
    extra = design_rules(design_block)
    widened = replace(DESIGN_LEAD_FINAL, allowed=DESIGN_LEAD_FINAL.allowed + extra)
    facts = "\n\n".join(filter(None, [design_facts(design_block), compare_facts(design_block)]))
    prompt = with_context(state, widened, ports, cwd, facts) if facts else None
    _, patch = await run_role(state, widened, cfg, ports, cwd=cwd, prompt=prompt)
    return patch


def _verdict_patch(state: TaskState, cfg: Configuration, data: dict[str, Any]) -> TaskState:
    if data["verdict"] == "approved":
        return {}
    subtasks = state.get("subtasks", {})
    target = INTEGRATION if INTEGRATION in subtasks else next(iter(subtasks))
    subtask = {
        **subtasks[target],
        "status": "returned",
        "reason": data["reason"],
        "returned_by": "final_validation",
    }
    patch: TaskState = {"subtasks": {target: subtask}}
    version = rules.next_version(state, PM_FINAL.name)
    patch["findings"] = [
        {
            "subtask": target,
            "version": version,
            "location": PM_FINAL.name,
            "claim": claim,
            "severity": "blocking",
            "scope": "plan",
            "category": "spec-behaviour",
            "status": "open",
        }
        for claim in data["blocking"]
    ]
    limit = cfg.max_changes_requested_per_pair
    remaining = rules.remaining_changes(state, PAIR_PM_TECH_LEAD, limit)
    if remaining > 0:
        patch["changes_requested"] = rules.record_return(state, PAIR_PM_TECH_LEAD)
        patch["change_history"] = [
            rules.change_entry(PAIR_PM_TECH_LEAD, "spec-behaviour", data["reason"])
        ]
    else:
        patch |= rules.escalation(
            "returns", rules.limit_reached(PAIR_PM_TECH_LEAD, limit, data["reason"])
        )
        patch = rules.logged(patch)
        patch["status"] = WAITING_ON_HUMAN
    return patch
