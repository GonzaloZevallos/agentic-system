"""Tech Lead reviews one delivered subtask, in its checkout, and either approves it or
returns it to the same Developer within the pair's limit (README.md, 3 and 4).

One run, one subtask: the review is the second half of its Developer's branch in the
fan-out (ADR-0004, fifth addendum), so it starts as soon as that Developer delivers
instead of waiting for the slowest one. Which subtask is `state["subtask_id"]`, the
same the Developer before it read. Who notices that every subtask is approved, and
adds the integration, is the fan-in.

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

from ..domain import presubmit, rules
from ..domain.roles import pair_for, reviewer_for
from ..domain.task import WAITING_ON_HUMAN, TaskState, apply
from .checkouts import subtask_checkout
from .ports import Ports
from .presubmit import declared_checks
from .run_role import finish, run_role


async def review(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    subtask_id = state["subtask_id"]
    subtask = state["subtasks"][subtask_id]
    checks = declared_checks(state, ports)
    reviewer = reviewer_for(checks)
    checkout = subtask_checkout(state, subtask_id, ports)
    pair = pair_for(subtask_id)
    remaining = rules.remaining_returns(state, pair, cfg.max_returns_per_pair)
    section = rules.review_section(subtask_id)
    head = ports.workspace.head(checkout.path)
    output, patch = await run_role(
        state,
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
        checks=presubmit.describe_checks(checks),
    )
    if output is None:
        return finish(state, patch, ports)

    subtask = {**subtask, "reviewed_head": head}
    reason = output.data["reason"]
    findings, lowered = rules.lower_unbacked_blocks(output.data["findings"])
    category = rules.return_category(findings)
    markdown = rules.review_markdown(category, reason, output.text, lowered)
    patch = apply(patch, {"sections": {section: markdown}})
    if category is None:
        patch = apply(patch, {"subtasks": {subtask_id: {**subtask, "status": "approved"}}})
        return finish(state, patch, ports)

    returned = {
        **subtask,
        "status": "returned",
        "reason": reason,
        "return_category": category,
    }
    patch = apply(patch, {"subtasks": {subtask_id: returned}})
    if remaining > 0:
        patch = apply(
            patch,
            {
                "returns": rules.record_return(state, pair),
                "return_history": [rules.return_entry(pair, category, reason)],
            },
        )
    else:
        # Returned as well, without counting: the limit is spent. The human's answer
        # resets the pair and the resume sends the subtask back to its Developer.
        patch["escalated"] = rules.limit_reached(pair, cfg.max_returns_per_pair, reason)
        patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)
