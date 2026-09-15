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
what measurement counts. A block without evidence, or that this subtask's Developer
cannot fix, is lowered by the system, never a role failure. The system writes the
verdict, and what it lowered, at the head of the review's section."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import gate, rules
from ..domain.roles import pair_for, reviewer_for
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, TaskState, apply
from .checkouts import subtask_checkout
from .gate import declared_checks
from .ports import Ports
from .run_role import finish, run_role


async def review(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    limit = cfg.max_returns_per_pair
    patch: TaskState = {}
    checks = declared_checks(state, ports)
    reviewer = reviewer_for(checks)
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
            reviewer,
            cfg,
            ports,
            section=section,
            extra_sections=(rules.developer_section(subtask_id), section),
            cwd=checkout.path,
            subtask=subtask_id,
            base=subtask.get("reviewed_head") or checkout.base,
            review="an incremental review" if subtask.get("reviewed_head") else "a first review",
            remaining_returns=remaining,
            checks=gate.describe_checks(checks),
        )
        patch = apply(patch, reviewed)
        if output is None:
            break
        subtask = {**subtask, "reviewed_head": head}
        reason = output.data["reason"]
        findings, lowered = rules.lower_unbacked_blocks(output.data["findings"])
        category = rules.return_category(findings)
        markdown = rules.review_markdown(category, reason, output.text, lowered)
        patch = apply(patch, {"sections": {section: markdown}})
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
            # Returned as well, without counting: the limit is spent. The human's answer
            # resets the pair and the resume sends the subtask back to its Developer.
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
                    }
                },
            )
            patch["escalated"] = rules.limit_reached(pair, limit, reason)
            patch["status"] = WAITING_ON_HUMAN
            break

    current = apply(state, patch)
    if not current.get("escalated") and rules.needs_integration(current):
        patch = apply(patch, {"subtasks": {INTEGRATION: rules.integration_subtask()}})
    return finish(state, patch, ports)
