"""The `fix_important` node (GH-354, ADR-0053 amendment): the task's one important pass,
run before the pull request opens.

`rules.after_integrate` sends the task here when the final review approved with `owned` or
`plan` `important` rows still open (`rules.pre_pr_important_due`). No role runs and nothing
is paid: the node reopens the owning units exactly as the landing's pass does
(`landing_sync.reopen`, `by: "landing"`, so it is no return and `changes_requested` is never
touched) and records the pass in `landing` with `before_pr: true`. The next wave builds and
reviews the units again; the fan-in closes the entry (`close_pass`) once they and the
integration are approved, and only then does the task go on to `full_checks` and `deliver`.
Nothing here opens an issue: what the review after the pass raises again lands under
"Accepted without a fix", as ADR-0053 says."""

from __future__ import annotations

from datetime import UTC, datetime

from agentic.shared.configuration import Configuration

from ..domain import landing as landing_rules
from ..domain import rules
from ..domain.task import LandingEntry, TaskState
from . import landing_fix, landing_plan, landing_sync
from .ports import Ports
from .run_role import finish


async def fix_important(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    units = landing_rules.important_owners(state)
    reason, category = landing_fix.important_reason(state)
    reopened = landing_sync.reopen(state, units, reason, category, "returned")
    before = state.get("subtasks", {})
    kept = state.get("change_history", [])
    entry: LandingEntry = {
        "step": rules.FIX_IMPORTANT,
        "refusal": "open_findings",
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cost_base": state.get("cost_usd", 0.0),
        "before_pr": True,
    }
    if isinstance(head := state.get("task_head"), str) and head:
        entry["head"] = head
    patch: TaskState = {
        "status": reopened["status"],
        # Only the units the pass reopened: the reducer merges, and `reopen` returns the
        # whole state with the history already folded in.
        "subtasks": {
            name: unit for name, unit in reopened["subtasks"].items() if unit != before.get(name)
        },
        "change_history": reopened["change_history"][len(kept) :],
        "landing": [*(landing_rules.entry_of(e) for e in landing_plan.entries(state)), entry],
    }
    return finish(state, patch, ports)


def close_pass(state: TaskState, at: str) -> list[LandingEntry] | None:
    """The `landing` list with the pre-PR pass closed, or `None` when there is nothing to
    close: the last entry is this pass, still open, and every unit is approved again (the
    integration reviewed after the units it was reopened under). The cost is the task's
    spend since the entry was written."""
    entries = landing_plan.entries(state)
    last = entries[-1] if entries else None
    if (
        last is None
        or last.get("step") != rules.FIX_IMPORTANT
        or not rules.before_pr(last)
        or rules.entry_closed(last)
        or not rules.all_approved(state)
    ):
        return None
    return landing_rules.resolve_open(entries, at, state.get("cost_usd", 0.0))
