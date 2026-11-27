"""The early size check (GH-999, ADR-0109): right after PM approves a feature's PRD, a cheap
tool-less read of it decides whether the task is so clearly over the size budget that it splits
before the design ladder and the Tech Lead. The decision is `early_split.verdict`; this node
only runs the sizer, judges the parts it proposes and writes the one patch.

It fails open: a sizer that delivers nothing, or parts that are refused, give `go`, and only a
task cap stops the task. It never applies the estimate cap and never writes `estimate`,
`planned_size`, `subtasks` or a section. The only writer of the `early_split` rows."""

from __future__ import annotations

from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import cost_history, early_split, rules, schemas, size_budget
from ..domain.roles import DEFINITION_SIZER, PM
from ..domain.task import EarlySplitRow, TaskState
from .closed import closed_documents
from .ports import Ports
from .run_role import run_role, soft_failure


def _row(
    decided: early_split.Verdict, at: str, size: size_budget.PlanSize | None, data: dict[str, Any]
) -> EarlySplitRow:
    """The row the verdict leaves: the figures the sizer declared are in it only when it gave
    them, and no decision reads them."""
    row: EarlySplitRow = {"verdict": decided.verdict, "cause": decided.cause, "at": at}
    if size is not None:
        row["hand_files"] = size.hand_files
        row["hand_lines"] = size.hand_lines
        row["test_lines"] = size.test_lines
        row["estimated_cost_usd"] = float(data["estimated_cost_usd"])
    return row


def _parts_ok(
    state: TaskState, ports: Ports, parts: list[dict[str, Any]], ratio: size_budget.Ratio
) -> bool:
    """Whether the parts the sizer proposed pass `early_parts_check` against PM's section. A task
    that is no tracker issue has no `#<issue>` to quote, so its parts are refused."""
    issue = ports.inbox.issue_of(state["id"])
    if not parts or issue is None:
        return False
    prd = state.get("sections", {}).get(PM.name, "")
    try:
        schemas.early_parts_check(parts, issue, prd, ratio)
    except ValueError:
        return False
    return True


async def early_size(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    """Run the definition sizer and answer with one patch: the role's own, one `early_split`
    row, and `parts` only when the verdict is `split`. A task cap is the escalation as it is; a
    failure keeps what it cost and leaves a `sizer_failed` row, so the check comes up once more."""
    history = cost_history.build(closed_documents(ports))
    ratio = cost_history.size_ratio(history)
    output, patch = await run_role(
        state,
        DEFINITION_SIZER,
        cfg,
        ports,
        cost_history=history.text(),
        size_budget=size_budget.budget_text(ratio),
        slicing_rules=rules.SLICING_RULES,
    )
    at = ports.now()
    if output is None:
        kept, stop = soft_failure(patch)
        if stop:
            return kept
        failed: EarlySplitRow = {"verdict": "go", "cause": "sizer_failed", "at": at}
        return {**kept, "early_split": [failed]}
    patch.pop("sections", None)
    size = schemas.declared_size(output.data)
    parts = output.data.get("parts") or []
    decided = early_split.verdict(size, _parts_ok(state, ports, parts, ratio))
    patch["early_split"] = [_row(decided, at, size, output.data)]
    if decided.verdict == "split":
        patch["parts"] = parts
    return patch
