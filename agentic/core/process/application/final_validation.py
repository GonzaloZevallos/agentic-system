"""PM's early, UI-only final validation (ADR-0040): between the approving review of
the whole branch and `deliver`, only for a feature PM flagged `touches_ui`
(`rules.final_validation_due`). UX Lead walks the sample app the product's `design`
block may declare, UI Lead judges the comparisons at equal-to-the-eye fidelity,
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

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import plan_settlement, rules
from ..domain.design import NO_SAMPLE_APP_NOTE, Design, compare_facts, design_facts, design_rules
from ..domain.roles import PAIR_PM_TECH_LEAD, PM_FINAL, UI_LEAD_FINAL, UX_LEAD_FINAL
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, TaskState, apply
from .checkouts import SetupFailed, task_base, task_checkout_in_thread
from .ports import Ports, SampleAppHandle
from .run_role import Output, finish, run_role, with_context

Started = SampleAppHandle | str | None


async def final_validation(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    try:
        task = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(PM_FINAL), ports)
    design_block = ports.stack.design(task_base(state, ports))
    started = _start(design_block, ports, task.path)
    try:
        ux_output, patch = await _ux_lead_final(state, cfg, ports, task.path, design_block, started)
        patch = apply(patch, _design_items_patch(UX_LEAD_FINAL.slug, ux_output))
        state = apply(state, patch)

        design_output, design_patch = await _ui_lead_final(
            state, cfg, ports, task.path, design_block
        )
        items = _design_items_patch(UI_LEAD_FINAL.slug, design_output)
        design_patch = apply(design_patch, items)
        state = apply(state, design_patch)
        patch = apply(patch, design_patch)

        # Every run starts without the previous run's record: the capability files it
        # committed are replaced, never accumulated, and a failed run leaves none claimed.
        patch = apply(patch, {"close_docs": None})
        reviewed = _reviewed_head(state)
        prompt = with_context(
            state, PM_FINAL, ports, task.path, _diff_facts(state, ports, reviewed)
        )
        output, pm_patch = await run_role(
            state,
            PM_FINAL,
            cfg,
            ports,
            check=_without_definitions_on_return(ports.knowledge.clarification_check()),
            cwd=task.path,
            prompt=prompt,
        )
        patch = apply(patch, pm_patch)
        if output is None:
            return finish(state, patch, ports)
        patch = apply(patch, _verdict_patch(state, cfg, output.data))
        patch = apply(patch, _close_docs_patch(state, ports, task.path, output.data, reviewed))
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
) -> tuple[Output | None, TaskState]:
    facts = _app_facts(design_block, started)
    prompt = with_context(state, UX_LEAD_FINAL, ports, cwd, facts)
    output, patch = await run_role(state, UX_LEAD_FINAL, cfg, ports, cwd=cwd, prompt=prompt)
    if design_block is None or design_block.sample_app is None or isinstance(started, str):
        note = started if isinstance(started, str) else NO_SAMPLE_APP_NOTE
        written = patch["sections"][UX_LEAD_FINAL.name]
        patch = apply(
            patch,
            {"sections": {UX_LEAD_FINAL.name: f"{rules.system_note(note)}\n\n{written}"}},
        )
    return output, patch


def _design_items_patch(role_slug: str, output: Output | None) -> TaskState:
    """The leads' final items (ADR-0040), recorded the way `design.py`'s own
    `_lead_walk` records a design-branch lead's: from structured output alone, never
    section prose. `step` is the literal `'final'` here, not the item's own flow step
    or screen -- this is a walk of the whole app, not of one step of it."""
    if output is None:
        return {}
    return {
        "design_items": [
            {"role_slug": role_slug, "kind": item["kind"], "step": "final", "text": item["text"]}
            for item in output.data["items"]
        ]
    }


def _app_facts(design_block: Design | None, started: Started) -> str:
    if design_block is None or design_block.sample_app is None:
        return NO_SAMPLE_APP_NOTE
    if isinstance(started, str):
        return started
    assert started is not None
    return "The sample app's first lines on start:\n\n" + "\n".join(started.lines)


async def _ui_lead_final(
    state: TaskState, cfg: Configuration, ports: Ports, cwd: Path, design_block: Design | None
) -> tuple[Output | None, TaskState]:
    extra = design_rules(design_block)
    widened = replace(UI_LEAD_FINAL, allowed=UI_LEAD_FINAL.allowed + extra)
    facts = "\n\n".join(filter(None, [design_facts(design_block), compare_facts(design_block)]))
    prompt = with_context(state, widened, ports, cwd, facts) if facts else None
    return await run_role(state, widened, cfg, ports, cwd=cwd, prompt=prompt)


def _reviewed_head(state: TaskState) -> str | None:
    """The `reviewed_head` of the reviewed unit: the commit PM's approval sits on, and the
    `parent` a close-time commit is made on."""
    name = rules.reviewed_unit_name(state)
    return state.get("subtasks", {}).get(name, {}).get("reviewed_head") if name else None


def _diff_facts(state: TaskState, ports: Ports, reviewed: str | None) -> str:
    """What PM needs to read the delivered diff itself: the base the task started from and
    the head its approval sits on. Nothing to read when no review recorded a head."""
    if not reviewed:
        return "No reviewed head is recorded: there is no delivered diff to reconcile."
    return (
        f"The delivered diff is `git diff {task_base(state, ports)}..{reviewed}`: read it to "
        "reconcile the capability files you return with what shipped."
    )


def _without_definitions_on_return(
    base: Callable[[dict[str, Any]], None],
) -> Callable[[dict[str, Any]], None]:
    """The knowledge rules for `definitions` (`clarification_check`), plus this node's own:
    a `returned` verdict writes nothing, so a non-empty list there is a mistake PM fixes
    inside its turn rather than a file the system silently drops."""

    def check(data: dict[str, Any]) -> None:
        if data["verdict"] == "returned" and data["definitions"]:
            raise ValueError(
                "returned with `definitions`: capability files are written only on `approved`; "
                "return an empty list"
            )
        base(data)

    return check


def _relative_paths(written: list[Path], root: Path) -> list[str]:
    """The files `consolidate` wrote, as paths relative to the checkout they were written in
    (what `commit_paths` takes and `close_docs.paths` records): the one place they are
    converted."""
    return [path.relative_to(root).as_posix() for path in written]


def _close_docs_patch(
    state: TaskState, ports: Ports, root: Path, data: dict[str, Any], reviewed: str | None
) -> TaskState:
    """The close-time pass: on `approved`, the capability files PM returned are written into
    the task checkout and committed there, and `close_docs` records that commit. Nothing is
    written when PM returned none or no head was reviewed, and nothing is recorded when the
    files were already what the checkout held (no commit). `paths` is what was committed."""
    if data["verdict"] != "approved" or not data["definitions"] or not reviewed:
        return {}
    written = ports.knowledge.consolidate(
        {"definitions": data["definitions"], "glossary": []}, root
    )
    relative = _relative_paths(written, root)
    message = f"{state['id']}: capability files at close"
    head = ports.workspace.commit_paths(root, relative, message)
    if head is None:
        return {}
    return {"close_docs": {"parent": reviewed, "head": head, "paths": relative}}


def _verdict_patch(state: TaskState, cfg: Configuration, data: dict[str, Any]) -> TaskState:
    version = rules.next_version(state, PM_FINAL.name)
    if data["verdict"] == "approved":
        settled = plan_settlement.on_approval(state, version)
        return {"plan_settled": settled} if settled else {}
    subtasks = state.get("subtasks", {})
    target = INTEGRATION if INTEGRATION in subtasks else next(iter(subtasks))
    subtask = {
        **subtasks[target],
        "status": "returned",
        "reason": data["reason"],
        "returned_by": "final_validation",
    }
    patch: TaskState = {"subtasks": {target: subtask}}
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
    if superseded := plan_settlement.on_return(state, version, data["blocking"]):
        patch["plan_settled"] = superseded
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
