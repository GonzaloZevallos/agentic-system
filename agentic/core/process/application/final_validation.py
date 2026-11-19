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

from ..domain import drawing, drawing_answer, plan_settlement, rules
from ..domain.design import (
    NO_COMPARE_NOTE,
    NO_SAMPLE_APP_NOTE,
    Capture,
    Design,
    NotReady,
    compare_facts,
    design_facts,
    design_rules,
)
from ..domain.roles import PAIR_PM_TECH_LEAD, PM_FINAL, UI_LEAD_FINAL, UX_LEAD_FINAL, Role
from ..domain.schemas import DrawingCheck, DrawingDeparture
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, DrawingRow, TaskState, apply
from .checkouts import task_base
from .ports import Checkout, Ports, SampleAppHandle
from .run_role import Check, Output, finish, run_role, with_context

Started = SampleAppHandle | NotReady | str | None

# The fixed lines the system writes (never a role) when the due screens cannot be compared.
NO_APP_NOTE = "the sample app did not start: no comparisons, every due screen is unverified"
HEAD_UNRESOLVED_NOTE = "the reviewed head is not a full commit id: no drawing checks recorded"
DRAWING_RETURN_REASON = "Drawing departures need the owner's answer or a fix before this ships."

_FILL_FACTS = (
    "Run the product's compare once per due screen, and Read the approved image and the capture "
    "of the app. Write in `drawing_checks` exactly one check per due screen: subject `screen`, "
    "ref the image path, verdict `matches`, `departs` or `unverified` (a `matches` or `departs` "
    "check names the drawing and the element). Add one entry to `departures` for each `departs` "
    "check, with the same drawing and element. A departure already listed in `Drawing "
    "departures` is cited with the same drawing and element, never reworded."
)


async def final_validation(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    *,
    checkout: Checkout,
) -> TaskState:
    """The `checkout` is the verifier set's (GH-469): resolved once by `verifiers.verify`.
    An approval with capability files does not write or commit them: it leaves them owed in
    `pending_close_docs` on the reviewed head, and `commit_close_docs` commits them once the
    whole set has judged the same head."""
    task = checkout
    design_block = ports.stack.design(task_base(state, ports))
    reviewed = _reviewed_head(state)
    # Read before the app starts and before any lead runs: a `GitError` stops the run here.
    due = _due_screens(state, ports, design_block, reviewed)
    started = _start(design_block, ports, task.path)
    comparable = _comparable(design_block, started)
    try:
        ux_output, patch = await _ux_lead_final(
            state, cfg, ports, task.path, (design_block, due, comparable), started
        )
        patch = apply(patch, _design_items_patch(UX_LEAD_FINAL.slug, ux_output))
        state = apply(state, patch)

        design_output, design_patch = await _ui_lead_final(
            state, cfg, ports, task.path, (design_block, due, comparable)
        )
        items = _design_items_patch(UI_LEAD_FINAL.slug, design_output)
        design_patch = apply(design_patch, items)
        state = apply(state, design_patch)
        patch = apply(patch, design_patch)

        # Every run starts without the previous run's record: the capability files it
        # committed are replaced, never accumulated, and a failed run leaves none claimed.
        patch = apply(patch, {"close_docs": None})
        if design_block is not None:
            drawing_patch, returned = _drawing_patch(
                state, cfg, ports, task.path, reviewed, due, comparable, (ux_output, design_output)
            )
            patch = apply(patch, drawing_patch)
            if returned:
                return finish(state, patch, ports)
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
        patch = apply(patch, _verdict_patch(state, cfg, output.data, at=ports.now()))
        patch = apply(patch, _pending_close_docs_patch(output.data, reviewed))
        return finish(state, patch, ports)
    finally:
        if isinstance(started, SampleAppHandle):
            ports.sample_app.stop(started)


def _start(design_block: Design | None, ports: Ports, cwd: Path) -> Started:
    """`None` when the product declares no `sample_app` at all -- nothing to start and
    nothing to stop; a handle or a refusal otherwise."""
    if design_block is None or design_block.sample_app is None:
        return None
    return ports.sample_app.start(design_block.sample_app, cwd)


Screens = tuple[Design | None, list[Capture], bool]


def _final_lead(role: Role, design_block: Design | None) -> Role:
    """The only builder of the two final leads: the role with the permission rule of the
    product's own `compare` when it declares one."""
    return replace(role, allowed=role.allowed + design_rules(design_block))


def _due_screens(
    state: TaskState, ports: Ports, design_block: Design | None, reviewed: str | None
) -> list[Capture]:
    """The screens this change owes a check for: `screens_due` over what `task_base..reviewed`
    touched (deleted paths and both ends of a rename included). Nothing without a design."""
    if design_block is None:
        return []
    touched = ports.workspace.touched_paths(task_base(state, ports), reviewed) if reviewed else ()
    return drawing.screens_due(design_block, touched)


def _comparable(design_block: Design | None, started: Started) -> bool:
    """Whether the due screens can be compared: a declared `compare` and a sample app up."""
    return (
        design_block is not None
        and design_block.compare is not None
        and isinstance(started, SampleAppHandle)
    )


def _due_facts(screens: Screens) -> str:
    """What the two leads read of the due screens; `""` without a design."""
    design_block, due, comparable = screens
    if design_block is None:
        return ""
    if not due:
        return "No screen is due for a drawing check."
    lines = [f"- screen `{c.screen}`: approved image `{c.image}`" for c in due]
    tail = _FILL_FACTS if comparable else _unverified_note(design_block)
    return "Screens due for a drawing check:\n\n" + "\n".join(lines) + "\n\n" + tail


def _unverified_note(design_block: Design) -> str:
    return NO_COMPARE_NOTE if design_block.compare is None else NO_APP_NOTE


def _drawing_check(state: TaskState, due_images: list[str] | None) -> Check:
    """The in-turn check of a lead's drawing output: a check per due screen when `due_images`
    is given, then `drawing_answer.classify`; its `ValueError` is the refusal."""

    def check(data: dict[str, Any]) -> None:
        checks = [DrawingCheck(**c) for c in data["drawing_checks"]]
        if due_images is not None:
            held = {c.ref for c in checks if c.subject == "screen"}
            if missing := [image for image in due_images if image not in held]:
                raise ValueError(f"no drawing check for the due screen(s): {', '.join(missing)}")
        drawing_answer.classify(state, checks, [DrawingDeparture(**d) for d in data["departures"]])

    return check


async def _ux_lead_final(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    cwd: Path,
    screens: Screens,
    started: Started,
) -> tuple[Output | None, TaskState]:
    design_block = screens[0]
    role = _final_lead(UX_LEAD_FINAL, design_block)
    facts = "\n\n".join(
        filter(
            None,
            [_app_facts(design_block, started), compare_facts(design_block), _due_facts(screens)],
        )
    )
    prompt = with_context(state, role, ports, cwd, facts)
    check = None if design_block is None else _drawing_check(state, None)
    output, patch = await run_role(state, role, cfg, ports, check=check, cwd=cwd, prompt=prompt)
    refusal = _refusal(started)
    if design_block is None or design_block.sample_app is None or refusal is not None:
        note = refusal if refusal is not None else NO_SAMPLE_APP_NOTE
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


def _refusal(started: Started) -> str | None:
    """What the lead reads when the app did not start: the command's own refusal, or the
    lapse of the wait said as such, which is not the command failing (GH-451)."""
    if isinstance(started, NotReady):
        return started.said()
    return started if isinstance(started, str) else None


def _app_facts(design_block: Design | None, started: Started) -> str:
    if design_block is None or design_block.sample_app is None:
        return NO_SAMPLE_APP_NOTE
    if (refusal := _refusal(started)) is not None:
        return refusal
    assert isinstance(started, SampleAppHandle)
    return "The sample app's first lines on start:\n\n" + "\n".join(started.lines)


async def _ui_lead_final(
    state: TaskState, cfg: Configuration, ports: Ports, cwd: Path, screens: Screens
) -> tuple[Output | None, TaskState]:
    design_block, due, comparable = screens
    role = _final_lead(UI_LEAD_FINAL, design_block)
    facts = "\n\n".join(
        filter(None, [design_facts(design_block), compare_facts(design_block), _due_facts(screens)])
    )
    prompt = with_context(state, role, ports, cwd, facts) if facts else None
    check = None
    if design_block is not None:
        check = _drawing_check(state, [c.image for c in due] if comparable else None)
    output, patch = await run_role(state, role, cfg, ports, check=check, cwd=cwd, prompt=prompt)
    if output is not None and design_block is not None and due and not comparable:
        written = patch["sections"][UI_LEAD_FINAL.name]
        note = rules.system_note(_unverified_note(design_block))
        patch = apply(patch, {"sections": {UI_LEAD_FINAL.name: f"{note}\n\n{written}"}})
    return output, patch


def _full_head(ports: Ports, path: Path, reviewed: str | None) -> str | None:
    """The full commit id the drawing checks stand on: `reviewed` when it is one, else the
    checkout's own full head when it names the same commit; `None` when neither does."""
    if reviewed is None:
        return None
    if rules.full_commit_id(reviewed):
        return reviewed
    head = ports.workspace.full_head(path)
    return head if rules.full_commit_id(head) and rules.same_commit(reviewed, head) else None


def _drawing_patch(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    path: Path,
    reviewed: str | None,
    due: list[Capture],
    comparable: bool,
    outputs: tuple[Output | None, Output | None],
) -> tuple[TaskState, bool]:
    """The `drawing_checks` record of this pass and, when some departure is not settled, what
    stops PM: the transient `drawing_raised` when one still has to be asked of the owner, else
    the return. Nothing when a lead failed (the run already escalated)."""
    ux, ui = outputs
    if ux is None or ui is None:
        return {}, False
    head = _full_head(ports, path, reviewed)
    if head is None:
        note = rules.system_note(HEAD_UNRESOLVED_NOTE)
        written = state["sections"][UI_LEAD_FINAL.name]
        return {"sections": {UI_LEAD_FINAL.name: f"{note}\n\n{written}"}}, False
    images = {c.image for c in due}
    if comparable:
        checks = [
            c
            for c in drawing_answer.merge_checks(
                [DrawingCheck(**c) for c in ux.data["drawing_checks"]],
                [DrawingCheck(**c) for c in ui.data["drawing_checks"]],
            )
            if c.ref in images
        ]
        entries = [DrawingDeparture(**d) for d in (*ux.data["departures"], *ui.data["departures"])]
        named = {
            drawing.departure_key(c.drawing, c.element)
            for c in checks
            if c.verdict == "departs" and c.drawing is not None and c.element is not None
        }
        entries = [e for e in entries if drawing.departure_key(e.drawing, e.element) in named]
    else:
        # Never `matches` for what nobody compared: the system's own line per due screen.
        checks = [DrawingCheck(subject="screen", ref=c.image, verdict="unverified") for c in due]
        entries = []
    classified = drawing_answer.classify(state, checks, entries)
    rows = [
        DrawingRow(subject=c.subject, ref=c.ref, verdict=c.verdict)
        if not (c.verdict == "departs" and _settled(c, classified.outcomes))
        else DrawingRow(subject=c.subject, ref=c.ref, verdict=c.verdict, settled=True)
        for c in checks
    ]
    patch: TaskState = {
        "drawing_checks": {"head": head, "rows": rows, "open": len(classified.open)}
    }
    if "raise" in classified.outcomes.values():
        # The owner is asked first, by `verify` (`drawing_answer.fold_raised`): nothing returns
        # and no fix pass is counted here, both are decided again on the resumed head. The key
        # is set even when no departure is new: an open row still has to be asked.
        return apply(patch, {"drawing_raised": list(classified.raised)}), True
    pending = {k: o for k, o in classified.outcomes.items() if o != "settled_accept"}
    if not pending:
        return patch, False
    data = {
        "verdict": "returned",
        "reason": DRAWING_RETURN_REASON,
        "blocking": _claims(state, checks, pending),
    }
    patch = apply(patch, _verdict_patch(state, cfg, data, at=ports.now()))
    patch = apply(
        patch, {"drawing_departures": drawing_answer.fix_pass_rows(state, classified.outcomes)}
    )
    return patch, True


def _settled(check: DrawingCheck, outcomes: dict[str, drawing_answer.Outcome]) -> bool:
    if check.drawing is None or check.element is None:
        return False
    return outcomes.get(drawing.departure_key(check.drawing, check.element)) == "settled_accept"


def _claims(
    state: TaskState, checks: list[DrawingCheck], pending: dict[str, drawing_answer.Outcome]
) -> list[str]:
    """One blocking claim per departure not settled, in the order of the checks. An unanswered
    one cites the drawing and the element and says the owner must answer; a rejected one cites
    its number and the owner's decision."""
    numbers = {r["key"]: r["number"] for r in state.get("drawing_departures", [])}
    claims: list[str] = []
    for c in checks:
        if c.drawing is None or c.element is None:
            continue
        key = drawing.departure_key(c.drawing, c.element)
        outcome = pending.get(key)
        if outcome == "raise":
            claims.append(
                f"{c.drawing}, element {c.element}: the screen departs from the approved drawing "
                "and the owner must answer it."
            )
        elif outcome is not None:
            claims.append(
                f"Drawing {numbers[key]}: the owner's decision is reject; {c.drawing}, element "
                f"{c.element} must match the approved drawing."
            )
    return claims


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


def _owes_close_docs(data: dict[str, Any], reviewed: str | None) -> bool:
    return data["verdict"] == "approved" and bool(data["definitions"]) and bool(reviewed)


def _pending_close_docs_patch(data: dict[str, Any], reviewed: str | None) -> TaskState:
    """The files an approval owes, kept in the document instead of written (GH-469)."""
    if not _owes_close_docs(data, reviewed) or reviewed is None:
        return {}
    return {"pending_close_docs": {"parent": reviewed, "definitions": data["definitions"]}}


def _commit_definitions(
    state: TaskState,
    ports: Ports,
    root: Path,
    definitions: list[dict[str, Any]],
    reviewed: str,
) -> TaskState:
    """Write `definitions` into the checkout `root` and commit them there: the `close_docs`
    record of that commit, or nothing when the files were already what the checkout held."""
    written = ports.knowledge.consolidate({"definitions": definitions, "glossary": []}, root)
    relative = _relative_paths(written, root)
    message = f"{state['id']}: capability files at close"
    head = ports.workspace.commit_paths(root, relative, message)
    if head is None:
        return {}
    return {"close_docs": rules.extend_close_docs(None, reviewed, head, relative)}


def commit_close_docs(state: TaskState, ports: Ports, root: Path) -> TaskState:
    """Commit the capability files `state['pending_close_docs']` owes, in the checkout `root`,
    exactly as the approving run does when it does not defer (GH-469): the patch records
    `close_docs` and clears the field. Only the verifier set calls it, once, when no verifier
    returned and the files' `parent` is still the reviewed head (`rules.close_docs_committable`)."""
    pending = state.get("pending_close_docs")
    if not pending:
        return {}
    committed = _commit_definitions(
        state, ports, root, list(pending["definitions"]), pending["parent"]
    )
    return {**committed, "pending_close_docs": None}


def _verdict_patch(
    state: TaskState, cfg: Configuration, data: dict[str, Any], *, at: str
) -> TaskState:
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
    if rules.on_return_limit(state, PAIR_PM_TECH_LEAD, limit) == "count":
        patch["changes_requested"] = rules.record_return(state, PAIR_PM_TECH_LEAD)
        patch["change_history"] = [
            rules.change_entry(PAIR_PM_TECH_LEAD, "spec-behaviour", data["reason"])
        ]
    else:
        patch |= rules.escalation(
            "returns", rules.limit_reached(PAIR_PM_TECH_LEAD, limit, data["reason"])
        )
        patch = rules.logged(patch, at=at)
        patch["status"] = WAITING_ON_HUMAN
    return patch
