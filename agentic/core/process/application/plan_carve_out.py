"""The depth-2 carve-out of the plan node (GH-722): one public entry, `carved`, that the plan
node calls once in each branch, between `_after_delivery` and `_finished`, and once at node
entry for a carve-out an earlier run left unfinished.

It never imports `application.plan`: the plan passes what it alone knows how to do as a
`PlanSteps` bundle (judge the remainder, land it, check it), and `Judged` carries the
remainder's own raised estimate back untouched in `floor`, a value this module never reads.
A carve-out never regates the route, never re-enters the interview or the critic, and is
tried at most once per task: `carve_out_due` is false the moment the document has a record.

A failure (the role, its schema, a refusal, the plan check, a remainder that does not fit once
floored) writes `outcome: failed` with no cause and pauses with the remainder's figures. The
tracker is written only by `application.carve_out`, whose record makes every step resumable."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from agentic.shared.configuration import Configuration

from ..domain import carve_out, plan_revision, plan_view, rules
from ..domain.carve_out import CarveOut
from ..domain.roles import TECH_LEAD, TECH_LEAD_CARVE_OUT
from ..domain.schemas import FitDecision
from ..domain.size_budget import PlanSize
from ..domain.task import CarveRecord, TaskState, apply
from . import carve_out as applied
from .carve_out import CarveData, CarveRefused
from .ports import Ports
from .run_role import Check, Output, run_role

_NO_FIT = FitDecision(False, 0.0, 0, None)


@dataclass(frozen=True)
class Judged:
    """A remainder judged by the plan: its output (estimate raised), its fit, and the plan's
    own record of the raise, opaque here and handed back to `PlanSteps.land` untouched."""

    output: Output
    fit: FitDecision
    floor: object


@dataclass(frozen=True)
class PlanSteps:
    """What the plan node lends the carve-out. `judge_remainder(rest, state)` floors the
    remainder and judges its fit; `land(state, base, judged, written)` routes it through the
    plan's own delivery; `check` is the branch's plan check (`None` at node entry); `part_of`
    is the part's marker; `cfg` runs the role; `fit` is the original plan's decision."""

    judge_remainder: Callable[[Mapping[str, object], TaskState], Judged]
    land: Callable[[TaskState, TaskState, Judged, TaskState], TaskState]
    check: Check | None
    part_of: tuple[str, int, int]
    cfg: Configuration
    fit: FitDecision | None = None


async def carved(
    state: TaskState,
    delivered: TaskState,
    judged: TaskState,
    output: Output | None,
    steps: PlanSteps,
    ports: Ports,
) -> TaskState:
    """`judged` itself when no carve-out is due or pending; otherwise the patch of the carve-out
    from `delivered` minus its subtasks, never from `judged`."""
    if carve_out.carve_out_pending(state):
        return _resume(state, steps, ports)
    due = carve_out.carve_out_due(state, judged, writes_tracker=carve_out.writes_tracker(state))
    if not due or output is None:
        return judged
    return await _carve(state, delivered, output, steps, ports)


async def _carve(
    state: TaskState, delivered: TaskState, output: Output, steps: PlanSteps, ports: Ports
) -> TaskState:
    base = cast(TaskState, {k: v for k, v in delivered.items() if k != "subtasks"})
    fit = steps.fit or FitDecision(
        False, output.data["estimated_cost_usd"], output.data["estimated_files"], None
    )
    role_output, run = await run_role(
        apply(state, base),
        TECH_LEAD_CARVE_OUT,
        steps.cfg,
        ports,
        resume=None,
        carve_plan=json.dumps(output.data, indent=2),
        carve_fit=rules.fit_figures_text(fit),
        carve_rules=carve_out.rules_text(),
    )
    if role_output is None:
        paid = {k: v for k, v in run.items() if k in ("failures", "cost_usd", "prompts")}
        return _failed(state, apply(base, cast(TaskState, paid)), fit, None, steps, ports)
    # The role's own text never replaces the plan's section: `written` rewrites that one.
    base = apply(base, cast(TaskState, {k: v for k, v in run.items() if k != "sections"}))
    current = apply(state, base)
    carve = _ordered(role_output.data, output.data)

    def fits(plan: Mapping[str, object]) -> bool:
        return steps.judge_remainder(plan, current).fit.fits

    remainder = steps.judge_remainder(carve_out.remainder(output.data, carve), current)
    reasons = applied.refusals(carve, output.data, fits)
    if steps.check is not None:
        try:
            steps.check(remainder.output.data)
        except ValueError as exc:
            reasons.append(str(exc))
    if reasons:
        return _failed(state, base, fit, remainder.fit, steps, ports)
    try:
        patch = applied.apply_carve(
            current, {"carve": carve, "plan_output": output.data}, ports, fit=fit, fits=fits
        )
    except CarveRefused:
        return _failed(state, base, fit, remainder.fit, steps, ports)
    if patch.get("escalation_class") is not None:
        return apply(base, _stopped(state, patch, ports))
    if "outcome" in patch.get("carve_out", {}):
        return apply(apply(base, patch), _over_line(state, fit, remainder.fit, steps, ports))
    return _land(state, base, apply(current, patch), remainder, steps, ports, fit)


def _resume(state: TaskState, steps: PlanSteps, ports: Ports) -> TaskState:
    """Node entry: the record is resumed from alone, no role runs and no plan check is made."""
    record = state["carve_out"]
    fit = _fit_of(record.get("fit", {}))
    patch = applied.apply_carve(
        state, cast(CarveData, record["data"]), ports, fit=fit or _NO_FIT, fits=lambda _: True
    )
    if patch.get("escalation_class") is not None:
        return _stopped(state, patch, ports)
    if "outcome" in patch.get("carve_out", {}):
        return apply(patch, _over_line(state, fit, None, steps, ports))
    return _land(state, _empty(state), apply(state, patch), None, steps, ports, fit)


def _land(
    state: TaskState,
    base: TaskState,
    after: TaskState,
    remainder: Judged | None,
    steps: PlanSteps,
    ports: Ports,
    fit: FitDecision | None,
) -> TaskState:
    """Closes the record and lands the remainder, or, when it no longer fits, pauses with the
    record still `applied` and no subtask queued."""
    try:
        closed, rest = applied.finish_carve(after)
    except ValueError:
        return _failed(state, base, fit, None, steps, ports)
    if remainder is None:
        remainder = steps.judge_remainder(rest, after)
    section = _rewritten(after, rest, closed["carve_out"])
    written: TaskState = {**_empty(state), "sections": {TECH_LEAD.name: section}}
    landed = apply(base, closed)
    if remainder.fit.fits:
        return steps.land(state, landed, remainder, written)
    paused = _over_line(state, fit, remainder.fit, steps, ports)
    return apply(apply(landed, written), paused)


def _empty(state: TaskState) -> TaskState:
    return {"id": state["id"], "title": state["title"], "description": state["description"]}


def _failed(
    state: TaskState,
    base: TaskState,
    fit: FitDecision | None,
    remainder: FitDecision | None,
    steps: PlanSteps,
    ports: Ports,
) -> TaskState:
    """Every failure: the paid runs, the record closed as `failed` with no cause, no subtask."""
    failed = apply(base, {**_empty(state), "carve_out": {"outcome": "failed"}})
    return apply(failed, _over_line(state, fit, remainder, steps, ports))


def _over_line(
    state: TaskState,
    fit: FitDecision | None,
    remainder: FitDecision | None,
    steps: PlanSteps,
    ports: Ports,
) -> TaskState:
    paused = rules.part_over_line(
        state["id"], steps.part_of, fit, at=ports.now(), remainder=remainder
    )
    return cast(TaskState, paused)


def _stopped(state: TaskState, patch: TaskState, ports: Ports) -> TaskState:
    """The pause of a tracker failure: it carries no `fit_question`, so a plain resume goes on,
    and the record the pause left in the document, which the patch does not hold."""
    kept = patch.copy()
    kept.pop("fit_question", None)
    saved = (ports.documents.load(state["id"]) or {}).get("carve_out")
    if "carve_out" not in kept and isinstance(saved, dict):
        kept["carve_out"] = cast(CarveRecord, saved)
    return kept


def _ordered(data: Mapping[str, Any], plan: Mapping[str, object]) -> CarveOut:
    """The model's declaration with `required_paths` and `ci_required` in the original plan's
    order; an entry the plan does not hold stays, after the others, for `validate` to refuse."""
    carve = {key: data[key] for key in CarveOut.__annotations__}
    for field in ("required_paths", "ci_required"):
        original = plan.get(field)
        held = original if isinstance(original, list) else []
        carve[field] = [e for e in held if e in carve[field]] + [
            e for e in carve[field] if e not in held
        ]
    return cast(CarveOut, carve)


def _left(original: list[str], remainder: list[str]) -> list[str]:
    """The entries of `original` the remainder does not keep, in the original order."""
    return [entry for entry in original if entry not in remainder]


def _closing_block(record: CarveRecord, rest: Mapping[str, object]) -> str:
    """The block the system closes the Tech Lead's section with: where the steps went and which
    required paths and CI checks left with them."""
    saved = cast(Mapping[str, Any], record["data"])
    plan = cast(Mapping[str, Any], saved["plan_output"])
    number = record["issue_url"].rstrip("/").rsplit("/", 1)[-1]

    def listed(field: str) -> str:
        left = _left(list(plan.get(field) or []), list(cast(list[str], rest[field])))
        return ", ".join(f"`{entry}`" for entry in left) or "none"

    steps = ", ".join(f"`{step}`" for step in saved["carve"]["steps"])
    return (
        f"### Carved out -> #{number}\n\nSubtasks moved to the follow-up: {steps}.\n\n"
        f"required_paths that left: {listed('required_paths')}.\n\n"
        f"ci_required that left: {listed('ci_required')}."
    )


def _rewritten(state: TaskState, rest: Mapping[str, object], record: CarveRecord) -> str:
    """The Tech Lead's section after the carve-out: its `### Subtasks` block rebuilt from the
    remainder's subtasks, and the closing block last."""
    section = state.get("sections", {}).get(TECH_LEAD.name, "")
    span = plan_view.subtasks_block(section)
    cut = section if span is None else section[: span[0]] + section[span[1] :]
    listed = [plan_revision.pending(s) for s in cast(list[Mapping[str, Any]], rest["subtasks"])]
    rebuilt = plan_view.with_subtasks_block(cut.rstrip(), listed)
    return f"{rebuilt.rstrip()}\n\n{_closing_block(record, rest)}"


def _fit_of(raw: Mapping[str, Any]) -> FitDecision | None:
    """The `FitDecision` a record saved as plain figures, or `None` when it cannot be read."""

    def size(value: object) -> PlanSize | None:
        return PlanSize(**value) if isinstance(value, dict) else None

    try:
        return FitDecision(
            bool(raw["fits"]),
            float(raw["cost_usd"]),
            int(raw["files"]),
            None if raw["reference_usd"] is None else float(raw["reference_usd"]),
            size(raw.get("size")),
            size(raw.get("judged_size")),
            tuple(raw.get("crossed") or ()),
        )
    except (KeyError, TypeError, ValueError):
        return None
