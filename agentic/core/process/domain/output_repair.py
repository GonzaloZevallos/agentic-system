"""Repairs of a role's raw structured output, one per mechanical refusal (GH-1016).

A refusal that only says "this text is too long" or "drop this field" has one fix the system
can apply without asking the model again. `REPAIRS` is the closed registry (Registry: it
resolves the dispatch by rule) from a `RefusalRule` to the one repair of it; `run_role`
applies the repair to the RAW dict, judges it again from `schemas.validate`, and records it.

A repair is pure: it returns a NEW dict and never edits the one it is given, or a list
nested in it. It returns `None` when it does not apply, and the refusal then stands. The
rule the check raised is the only evidence it uses: no repair recomputes a fit, a size or a
budget, reads an estimate, or changes subtasks.

D-4ec9e036: no repair touches visibility, drawing paths or anything tied to an approved
drawing. The registry holds two rules, both about the plan's own text and parts, and a new
entry that reached any of those would break that limit.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .plan_view import part_line_cause
from .refusal import RefusalRule
from .task_path import TEXT_MAX

ELLIPSIS = "…"


@dataclass(frozen=True)
class RepairContext:
    """What a repair may know besides the raw output: the text bound of a plan line. It
    carries no size, budget or estimate: a repair never recomputes them."""

    text_max: int = TEXT_MAX


Repair = Callable[[Mapping[str, object], RepairContext], dict[str, Any] | None]


def _cut(text: str, text_max: int) -> str:
    """`text` at the last word boundary that leaves room for the final ellipsis within
    `text_max`; a text with no boundary in that window is cut hard."""
    window = text[: text_max - 1]
    if not text[text_max - 1].isspace():
        boundary = max((i for i, char in enumerate(window) if char.isspace()), default=-1)
        words = window[:boundary].rstrip() if boundary >= 0 else ""
        if words:
            return words + ELLIPSIS
    return window.rstrip() + ELLIPSIS


def reason_truncated(raw: Mapping[str, object], context: RepairContext) -> dict[str, Any] | None:
    """`raw` with every `parts[*].depends_on_reasons[*]` the plan check refuses as too long
    cut to fit `context.text_max` (`depends_on_reason_too_long`). `None` when none is."""
    parts = raw.get("parts")
    if not isinstance(parts, list):
        return None
    fixed: dict[str, Any] = copy.deepcopy(dict(raw))
    changed = False
    for number, part in enumerate(fixed["parts"], 1):
        reasons = part.get("depends_on_reasons") if isinstance(part, dict) else None
        if not isinstance(reasons, list):
            continue
        for index, reason in enumerate(reasons):
            if not isinstance(reason, str):
                continue
            found = part_line_cause(number, "depends_on_reasons", reason, may_be_empty=False)
            if found is not None and found[0] == "too_long":
                reasons[index] = _cut(reason, context.text_max)
                changed = True
    return fixed if changed else None


def parts_dropped_when_fits(
    raw: Mapping[str, object], context: RepairContext
) -> dict[str, Any] | None:
    """`raw` without `parts` (`parts_when_fits`), only when `subtasks` is a non-empty list:
    a plan with neither is not a plan that fits, and the refusal stands.

    Known gap (GH-1016 review): `plan_check_for` refuses `parts` and `subtasks` together with a
    plain `ValueError` before it raises `parts_when_fits`, so through the real plan check this
    repair is not reached today; it applies to any check that raises the rule with subtasks."""
    subtasks = raw.get("subtasks")
    if "parts" not in raw or not isinstance(subtasks, list) or not subtasks:
        return None
    fixed: dict[str, Any] = copy.deepcopy(dict(raw))
    del fixed["parts"]
    return fixed


REPAIRS: Mapping[RefusalRule, Repair] = MappingProxyType(
    {
        "depends_on_reason_too_long": reason_truncated,
        "parts_when_fits": parts_dropped_when_fits,
    }
)


def repair_note(rule: RefusalRule, field: str) -> str:
    """The line a delivered section carries for a repair applied on its own attempt."""
    return f"System repair: {rule} on {field}"
