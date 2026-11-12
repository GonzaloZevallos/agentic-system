"""The plan revision after a promotion (GH-591 part 2): a re-plan REVISES the subtasks the
task already has, it does not redo them. Pure: `revise` returns the `subtasks` patch the plan
node writes; nothing is deleted, reset or archived, and a changed subtask keeps its branch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .task import INTEGRATION, Subtask

# What makes a subtask "the same" across a re-plan: the fields a plan writes about it.
_PLANNED = ("brief", "seams", "depends_on", "screens")


def pending(item: Mapping[str, Any]) -> Subtask:
    """A subtask as a plan first writes it: `pending`, with `screens` only when non-empty
    (ADR-0040). The one builder, for a first plan and for a new id in a revision."""
    built: Subtask = {
        "id": item["id"],
        "title": item["title"],
        "brief": item["brief"],
        "seams": item["seams"],
        "depends_on": item["depends_on"],
        "status": "pending",
    }
    # Persisted only when non-empty (ADR-0040): a plan from before `screens` existed,
    # and a subtask with no screen of its own, both read back `.get("screens", [])`.
    if item.get("screens"):
        built["screens"] = item["screens"]
    return built


def revise(
    old: Mapping[str, Subtask], new: Sequence[Mapping[str, Any]], *, approved_work: bool
) -> dict[str, Subtask]:
    """The `subtasks` patch of a revised plan. The `merge` reducer replaces a subtask whole,
    so a changed one is written whole, built from its old self.

    - same id, same planned fields: not in the patch, state and `reviewed_head` stay;
    - same id, changed: new fields, `pending`, every other key kept, no `prd_recheck`;
    - new id: pending, as a first plan builds it;
    - the final unit (`integration`, else the only subtask) that is unchanged and was
      approved, with `approved_work`: `delivered` with `prd_recheck`, so its next review is
      a full one against the new PRD. For `integration` only while every dependency stays
      approved in the result; an `integration` whose dependency went back to pending waits."""
    patch: dict[str, Subtask] = {}
    for item in new:
        sid = item["id"]
        before = old.get(sid)
        if before is None:
            patch[sid] = pending(item)
        elif _changed(before, item):
            patch[sid] = _reopened(before, item)
    result = {**old, **patch}
    final = _final_unit(result)
    if final is not None:
        updated = _final_state(result, final, approved_work, changed=final in patch)
        if updated is not None:
            patch[final] = updated
    return patch


def _changed(before: Subtask, item: Mapping[str, Any]) -> bool:
    return before.get("brief") != item["brief"] or any(
        before.get(key, []) != item.get(key, []) for key in ("seams", "depends_on", "screens")
    )


def _reopened(before: Subtask, item: Mapping[str, Any]) -> Subtask:
    """The subtask whole, back to `pending` with the new plan's fields. `screens`,
    `prd_recheck` and `attempt` are dropped first: the plan's own fields replace the first, a
    reopened subtask is not rechecked, and it starts over on the new brief, so it is not read
    as a clean restart's fresh attempt (GH-593, `restart.restarted_fresh`) with a stale
    `Restart <subtask>` section."""
    kept = Subtask(**before)
    kept.pop("screens", None)
    kept.pop("prd_recheck", None)
    kept.pop("attempt", None)
    return Subtask(**{**kept, **pending(item)})


def _final_unit(result: Mapping[str, Subtask]) -> str | None:
    if INTEGRATION in result:
        return INTEGRATION
    if len(result) == 1:
        return next(iter(result))
    return None


def _final_state(
    result: Mapping[str, Subtask], final: str, approved_work: bool, *, changed: bool
) -> Subtask | None:
    unit = result[final]
    if changed:
        return None
    if final == INTEGRATION and not _dependencies_approved(result, unit):
        # Its `depends_on` orders it after the reopened dependency; an integration that had
        # run goes back to the start of the line instead of waiting as approved.
        if unit.get("status") in ("approved", "delivered"):
            return Subtask(**{**unit, "status": "pending"})
        return None
    if approved_work and unit.get("status") == "approved":
        return Subtask(**{**unit, "status": "delivered", "prd_recheck": True})
    return None


def _dependencies_approved(result: Mapping[str, Subtask], unit: Subtask) -> bool:
    return all(
        result[dep].get("status") == "approved"
        for dep in unit.get("depends_on", [])
        if dep in result
    )
