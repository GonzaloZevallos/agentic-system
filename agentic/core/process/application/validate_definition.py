"""PM validates the definition: approves and consolidates, or returns it to the Product
Analyst within the pair's limit (README.md, 3 and 4; ADR-0006). The consolidation
lands in the task's checkout and is committed there (ADR-0011)."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import PAIR_PM_PA, PM
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .checkouts import task_checkout
from .ports import Ports
from .run_role import finish, run_role


async def validate_definition(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    task = task_checkout(state, ports)
    limit = cfg.max_changes_requested_per_pair
    remaining = rules.remaining_changes(state, PAIR_PM_PA, limit)
    output, patch = await run_role(
        state,
        PM,
        cfg,
        ports,
        check=ports.knowledge.approval_check(state.get("type", "")),
        cwd=task.path,
        remaining_changes=remaining,
    )
    if output is None:
        return finish(state, patch, ports)

    patch["flags"] = output.data["flags"]
    if output.data["verdict"] == "approved":
        # The consolidation (ADR-0006): PM returned the knowledge, the system writes it.
        ports.knowledge.consolidate(output.data, task.path)
        ports.workspace.commit_all(task.path, f"{state['id']}: product definition")
        patch["status"] = "definition_ready"
    elif remaining > 0:
        patch["changes_requested"] = rules.record_return(state, PAIR_PM_PA)
        patch["change_history"] = [
            rules.change_entry(PAIR_PM_PA, rules.DEFINITION_RETURN, output.data["reason"])
        ]
        patch["status"] = "returned"
    else:
        # The pair hit its limit: escalate, always (README.md, 3). PM knew this was
        # its last return, so the reason is written for the human.
        patch["escalated"] = rules.limit_reached(PAIR_PM_PA, limit, output.data["reason"])
        patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)
