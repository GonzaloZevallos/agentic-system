"""PM validates what the Product Analyst wrote: approves and consolidates, or returns
it to the Product Analyst within the pair's limit (README.md, 3 and 4; ADR-0006). The
consolidation lands in the task's checkout and is committed there (ADR-0011).

On a feature that is the definition, and PM's approval always consolidates. On a bug
it is the diagnosis (issue #65): PM's approval carries a ruling, `bug` sends the task
to the plan and `expected_behaviour` discards it, and PM consolidates only when the
ruling fixes a definition the product did not have. One node for both, because the
pair, its limit and the return are the same; which PM runs is the type's call."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import PAIR_PM_PA, PM, PM_BUG, Role
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .checkouts import SetupFailed, task_checkout
from .ports import Ports
from .run_role import finish, run_role


async def validate_definition(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    role = validator_for(state)
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(role), ports)
    limit = cfg.max_changes_requested_per_pair
    remaining = rules.remaining_changes(state, PAIR_PM_PA, limit)
    output, patch = await run_role(
        state,
        role,
        cfg,
        ports,
        check=ports.knowledge.approval_check(state.get("type", "")),
        cwd=task.path,
        remaining_changes=remaining,
    )
    if output is None:
        return finish(state, patch, ports)

    data = output.data
    if "flags" in data:
        # A bug proposes no flags, so there is no set to replace.
        patch["flags"] = data["flags"]
    if data["verdict"] == "approved" and data.get("ruling") == "expected_behaviour":
        # The product already does what it should: nothing to build, and nothing to
        # consolidate either, since a discarded task delivers nothing and a definition
        # written on its branch would go nowhere (the prompt asks PM for none).
        # `discard` tells the human, who closes the issue.
        patch["status"] = "discarded"
        patch["discard_reason"] = data["reason"]
    elif data["verdict"] == "approved":
        # The consolidation (ADR-0006): PM returned the knowledge, the system writes it.
        # A feature always brings definitions (`approval_check`); a bug against a
        # written definition brings none, and then there is nothing to commit.
        if data["definitions"] or data["glossary"]:
            ports.knowledge.consolidate(data, task.path)
            ports.workspace.commit_all(task.path, f"{state['id']}: product definition")
        patch["status"] = "definition_ready"
    elif remaining > 0:
        patch["changes_requested"] = rules.record_return(state, PAIR_PM_PA)
        patch["change_history"] = [
            rules.change_entry(PAIR_PM_PA, rules.DEFINITION_RETURN, data["reason"])
        ]
        patch["status"] = "returned"
    else:
        # The pair hit its limit: escalate, always (README.md, 3). PM knew this was
        # its last return, so the reason is written for the human.
        patch["escalated"] = rules.limit_reached(PAIR_PM_PA, limit, data["reason"])
        patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)


def validator_for(state: TaskState) -> Role:
    """Which PM validates this task: the one that rules on a diagnosis for a bug, the one
    that validates a definition otherwise. Same name, same section, same pair: only the
    schema and the prompt change (issue #65)."""
    return PM_BUG if state.get("type") == "bug" else PM
