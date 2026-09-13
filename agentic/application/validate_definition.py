"""PM validates the definition: approves and distils, or returns it to the Product
Analyst within the pair's limit (README.md, 3 and 4; ADR-0006)."""

from __future__ import annotations

from ..domain import knowledge, rules
from ..domain.configuration import Configuration
from ..domain.roles import PAIR_PM_PA, PM
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .ports import Ports
from .run_role import finish, run_role


async def validate_definition(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    limit = cfg.max_returns_per_pair
    remaining = rules.remaining_returns(state, PAIR_PM_PA, limit)
    output, patch = await run_role(
        state,
        PM,
        cfg,
        ports,
        check=knowledge.approval_check(state.get("type", "")),
        remaining_returns=remaining,
    )
    if output is None:
        return finish(state, patch, ports)

    patch["flags"] = output.data["flags"]
    if output.data["verdict"] == "approved":
        # The distillation (ADR-0006): PM returned the knowledge, the system writes it.
        definitions, terms = knowledge.distillation(output.data)
        ports.knowledge.write_definitions(definitions)
        ports.knowledge.upsert_terms(terms)
        patch["status"] = "definition_ready"
    elif remaining > 0:
        patch["returns"] = {PAIR_PM_PA: limit - remaining + 1}
        patch["status"] = "returned"
    else:
        # The pair hit its limit: escalate, always (README.md, 3). PM knew this was
        # its last return, so the reason is written for the human.
        patch["escalated"] = f"{PAIR_PM_PA} hit {limit} returns: {output.data['reason']}"
        patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)
