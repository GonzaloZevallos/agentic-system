"""Tech Lead reviews every delivered subtask, in its checkout, and either approves
it or returns it to the same Developer within the pair's limit (README.md, 3 and 4).
When all the real subtasks are approved and there are several, the integration
enters as one more subtask for the integrator (ADR-0011)."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import TECH_LEAD_REVIEWER, pair_for
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, TaskState, apply
from .checkouts import subtask_checkout
from .ports import Ports
from .run_role import finish, run_role


async def review(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    limit = cfg.max_returns_per_pair
    patch: TaskState = {}
    for subtask_id in rules.delivered_subtasks(state):
        current = apply(state, patch)
        subtask = current["subtasks"][subtask_id]
        checkout = subtask_checkout(current, subtask_id, ports)
        pair = pair_for(subtask_id)
        remaining = rules.remaining_returns(current, pair, limit)
        output, reviewed = await run_role(
            current,
            TECH_LEAD_REVIEWER,
            cfg,
            ports,
            section=rules.review_section(subtask_id),
            extra_sections=(rules.developer_section(subtask_id), rules.review_section(subtask_id)),
            cwd=checkout.path,
            subtask=subtask_id,
            base=checkout.base,
            remaining_returns=remaining,
        )
        patch = apply(patch, reviewed)
        if output is None:
            break
        if output.data["verdict"] == "approved":
            patch = apply(patch, {"subtasks": {subtask_id: {**subtask, "status": "approved"}}})
        elif remaining > 0:
            patch = apply(
                patch,
                {
                    "subtasks": {
                        subtask_id: {
                            **subtask,
                            "status": "returned",
                            "reason": output.data["reason"],
                        }
                    },
                    "returns": rules.record_return(current, pair),
                },
            )
        else:
            patch["escalated"] = rules.limit_reached(pair, limit, output.data["reason"])
            patch["status"] = WAITING_ON_HUMAN
            break

    current = apply(state, patch)
    if not current.get("escalated") and rules.needs_integration(current):
        patch = apply(
            patch,
            {
                "subtasks": {
                    INTEGRATION: {
                        "id": INTEGRATION,
                        "title": "Integration",
                        "brief": "Join every subtask branch on the task branch.",
                        "seams": [],
                        "depends_on": [],
                        "status": "pending",
                    }
                }
            },
        )
    return finish(state, patch, ports)
