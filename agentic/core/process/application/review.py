"""Tech Lead reviews every delivered subtask, in its checkout, and either approves
it or returns it to the same Developer within the pair's limit (README.md, 3 and 4).
When all the real subtasks are approved and there are several, the integration
enters as one more subtask for the integrator (ADR-0011).

The first review of a subtask diffs from its base with the brief as spec. Every
review after a return is incremental (ADR-0015): it diffs from the head the previous
review reached, with that review's list for the Developer as spec, so what a pass
approved is not paid for twice. Each pass records the head it reviewed.

The reviewer hands back findings, not a verdict (ADR-0016): the subtask is returned
only on a blocking finding, and the return takes that finding's category, which is
what measurement counts. A block without evidence is a role failure, retried with
the error. The system writes the verdict at the head of the review's section."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import rules, schemas
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
        section = rules.review_section(subtask_id)
        head = ports.workspace.head(checkout.path)
        output, reviewed = await run_role(
            current,
            TECH_LEAD_REVIEWER,
            cfg,
            ports,
            check=schemas.review_check,
            section=section,
            extra_sections=(rules.developer_section(subtask_id), section),
            cwd=checkout.path,
            subtask=subtask_id,
            base=subtask.get("reviewed_head") or checkout.base,
            review="an incremental review" if subtask.get("reviewed_head") else "a first review",
            remaining_returns=remaining,
        )
        patch = apply(patch, reviewed)
        if output is None:
            break
        subtask = {**subtask, "reviewed_head": head}
        reason = output.data["reason"]
        category = rules.return_category(output.data["findings"])
        patch = apply(
            patch, {"sections": {section: rules.review_markdown(category, reason, output.text)}}
        )
        if category is None:
            patch = apply(patch, {"subtasks": {subtask_id: {**subtask, "status": "approved"}}})
        elif remaining > 0:
            patch = apply(
                patch,
                {
                    "subtasks": {
                        subtask_id: {
                            **subtask,
                            "status": "returned",
                            "reason": reason,
                            "return_category": category,
                        }
                    },
                    "returns": rules.record_return(current, pair),
                    "return_history": [rules.return_entry(pair, category, reason)],
                },
            )
        else:
            patch["escalated"] = rules.limit_reached(pair, limit, reason)
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
