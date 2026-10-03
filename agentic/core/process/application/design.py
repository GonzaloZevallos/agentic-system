"""The definition-side design branch (ADR-0040): UX Designer writes the flow, UX Lead
validates it with a walk of the prototype, UI Designer writes the screens design over
the validated flow, Design Lead validates it, and PM consolidates both into the PRD.
All five run in the task's own checkout, between PM's own validation and the plan,
with `status` left at `definition_ready` throughout (ADR-0026 freezes `Status`);
`rules.design_step` is the only place that says which of them runs next, and these
use cases never decide routing themselves.

A design role never edits the product's code or its own knowledge (read-only, like
`TECH_LEAD_INTERVIEWEE`): it reads and writes its own structured output. The product's
optional `design` block reaches a role only as data, in its own turn message, built
here from `domain.design.design_facts`/`compare_facts` -- never named in a prompt. A
lead additionally gets `design_rules(design)` folded into its `allowed`, the one `Bash`
prefix its own `compare` command needs, nothing wider; with no block at all,
`NO_DESIGN_NOTE` is prepended to its own section, the same system-written-line
treatment `denial_note`/`cold_fallback_note` already get."""

from __future__ import annotations

from dataclasses import replace

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.design import NO_DESIGN_NOTE, compare_facts, design_facts, design_rules
from ..domain.roles import (
    DESIGN_LEAD,
    PAIR_DESIGN_LEAD,
    PAIR_PM_PA,
    PAIR_UX_LEAD,
    PM_CONSOLIDATOR,
    UI_DESIGNER,
    UX_DESIGNER,
    UX_LEAD,
    Pair,
    Role,
)
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .checkouts import SetupFailed, task_base, task_checkout
from .ports import Ports
from .run_role import finish, run_role, with_context


async def ux_flow(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    """UX Designer writes the flow of each job the PRD describes, or rewrites it on a
    return from UX Lead's own walk -- which it reads as `extra_sections`, since it is
    not among its own declared `reads` (the same seam `develop.py` already uses for a
    Developer's review)."""
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(UX_DESIGNER), ports)
    extra = ("UX Lead",)
    facts = design_facts(ports.stack.design(task_base(state, ports)))
    prompt = with_context(state, UX_DESIGNER, ports, task.path, facts, extra) if facts else None
    _, patch = await run_role(
        state, UX_DESIGNER, cfg, ports, cwd=task.path, extra_sections=extra, prompt=prompt
    )
    return finish(state, patch, ports)


async def ui_screens(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    """UI Designer writes the screens design over the flow UX Lead already validated,
    or rewrites it on a return from Design Lead's own comparisons."""
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(UI_DESIGNER), ports)
    extra = ("Design Lead",)
    facts = design_facts(ports.stack.design(task_base(state, ports)))
    prompt = with_context(state, UI_DESIGNER, ports, task.path, facts, extra) if facts else None
    _, patch = await run_role(
        state, UI_DESIGNER, cfg, ports, cwd=task.path, extra_sections=extra, prompt=prompt
    )
    return finish(state, patch, ports)


async def _lead_walk(
    state: TaskState, cfg: Configuration, ports: Ports, role: Role, pair: Pair
) -> TaskState:
    """The shape UX Lead's walk and Design Lead's comparisons share: validate the
    thinker's latest write, with the product's `design` block as data and, when it
    declares `compare`, the one `Bash` rule to run it; return to the thinker within the
    pair's limit, or escalate past it; record every item -- blocking or observation --
    into `design_items`, from structured output alone, approved or returned alike
    (ADR-0040; a lead never returns to a Developer, README.md, 4, §13)."""
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(role), ports)
    design_block = ports.stack.design(task_base(state, ports))
    widened = replace(role, allowed=role.allowed + design_rules(design_block))
    limit = cfg.max_changes_requested_per_pair
    remaining = rules.remaining_changes(state, pair, limit)
    facts = "\n\n".join(filter(None, [design_facts(design_block), compare_facts(design_block)]))
    prompt = with_context(state, widened, ports, task.path, facts) if facts else None
    output, patch = await run_role(state, widened, cfg, ports, cwd=task.path, prompt=prompt)
    if output is None:
        return finish(state, patch, ports)
    data = output.data
    patch["design_items"] = [
        {"role_slug": role.slug, "kind": item["kind"], "step": item["step"], "text": item["text"]}
        for item in data["items"]
    ]
    if design_block is None:
        patch["sections"] = {
            **patch["sections"],
            role.name: f"{rules.system_note(NO_DESIGN_NOTE)}\n\n{patch['sections'][role.name]}",
        }
    if data["verdict"] == "returned":
        if remaining > 0:
            patch["changes_requested"] = rules.record_return(state, pair)
            patch["change_history"] = [rules.change_entry(pair, rules.PRD_RETURN, data["reason"])]
        else:
            patch |= rules.escalation("returns", rules.limit_reached(pair, limit, data["reason"]))
            patch = rules.logged(patch)
            patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)


async def ux_walk(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    return await _lead_walk(state, cfg, ports, UX_LEAD, PAIR_UX_LEAD)


async def design_review(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    return await _lead_walk(state, cfg, ports, DESIGN_LEAD, PAIR_DESIGN_LEAD)


async def consolidate(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    """PM folds the validated flow and screens design into the PRD: what UX marked as
    impeding the job becomes a question for the human, escalated under the existing
    `questions` class; the rest becomes an `observation`, travelling to the pull
    request, never pausing the task. May return to Product Analyst under
    `PAIR_PM_PA`, within its limit, like any other PRD return (ADR-0040)."""
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(PM_CONSOLIDATOR), ports)
    limit = cfg.max_changes_requested_per_pair
    remaining = rules.remaining_changes(state, PAIR_PM_PA, limit)
    output, patch = await run_role(
        state, PM_CONSOLIDATOR, cfg, ports, cwd=task.path, remaining_changes=remaining
    )
    if output is None:
        return finish(state, patch, ports)
    data = output.data
    patch["design_items"] = [
        {
            "role_slug": PM_CONSOLIDATOR.slug,
            "kind": item["kind"],
            "step": item["step"],
            "text": item["text"],
        }
        for item in data["observations"]
    ]
    if data["verdict"] == "approved":
        if data["questions_for_human"]:
            patch["questions_for_human"] = data["questions_for_human"]
            patch |= rules.escalation(
                "questions",
                f"{PM_CONSOLIDATOR.name} could not complete the job without "
                f"{len(data['questions_for_human'])} point(s) UX marked as impeding it; "
                "see the questions",
            )
            patch = rules.logged(patch)
            patch["status"] = WAITING_ON_HUMAN
    elif remaining > 0:
        patch["changes_requested"] = rules.record_return(state, PAIR_PM_PA)
        patch["change_history"] = [rules.change_entry(PAIR_PM_PA, rules.PRD_RETURN, data["reason"])]
        patch["status"] = "returned"
    else:
        patch |= rules.escalation("returns", rules.limit_reached(PAIR_PM_PA, limit, data["reason"]))
        patch = rules.logged(patch)
        patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)
