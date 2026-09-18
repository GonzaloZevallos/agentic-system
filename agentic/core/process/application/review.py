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

Every finding joins the findings table (issue #42), whatever the verdict: what one
review saw as "worth a remark, not a charge" is what the next one, and the integration
review, need in front of them. The table is appended to here and folded where it is
read, because the sister branches of this wave cannot see this one. It is empty in the
first wave, which is exactly issue #17's case: what carries there are the plan's
invariants, which this review verifies alongside the brief and the seams.

The reviewer hands back findings, not a verdict (ADR-0016): the subtask is returned
only on a blocking finding, and the return takes that finding's category, which is
what measurement counts. A block without evidence, or that this subtask's Developer
cannot fix, is lowered by the system, never a role failure. The system writes the
verdict, and what it lowered, at the head of the review's section."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import presubmit, rules
from ..domain.roles import TECH_LEAD, pair_for, reviewer_for
from ..domain.task import WAITING_ON_HUMAN, TaskState, apply
from .checkouts import SetupFailed, subtask_checkout
from .ports import Ports
from .presubmit import declared_checks
from .run_role import finish, run_role


async def review(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    subtask_id = state["subtask_id"]
    subtask = state["subtasks"][subtask_id]
    reviewed_before = bool(subtask.get("reviewed_head"))
    checks = declared_checks(state, ports)
    reviewer = reviewer_for(checks)
    try:
        checkout = subtask_checkout(state, subtask_id, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(TECH_LEAD), ports)
    pair = pair_for(subtask_id)
    remaining = rules.remaining_changes(state, pair, cfg.max_changes_requested_per_pair)
    section = rules.review_section(subtask_id)
    version = rules.next_version(state, section)
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
        remaining_changes=remaining,
        invariants=rules.invariants_markdown(state),
        checks=presubmit.describe_checks(checks),
    )
    if output is None:
        return finish(state, patch, ports)

    subtask = {**subtask, "reviewed_head": head}
    reason = output.data["reason"]
    findings, lowered = rules.lower_unbacked_blocks(output.data["findings"])
    category = rules.change_category(findings)
    markdown = rules.review_markdown(category, reason, output.text, lowered)
    patch = apply(patch, {"sections": {section: markdown}})
    # Appended, never folded: a sister branch is reviewing at the same time and cannot
    # see this. Whoever builds the next prompt resolves the table (issue #42).
    patch = apply(
        patch,
        {
            "findings": rules.finding_records(subtask_id, version, findings),
            # A row closes only here, because a reviewer looked and said so. Silence is
            # not a verdict: an incremental pass is told not to re-read what it approved.
            "settled_findings": output.data["settled"],
            # Measurement only (like `subtask_id`): read once by the adapter for
            # `agentic.review.findings`'s `review.kind` label, never persisted.
            "review_kind": rules.review_kind(subtask_id, reviewed_before),
        },
    )
    if category is None:
        patch = apply(patch, {"subtasks": {subtask_id: {**subtask, "status": "approved"}}})
        return finish(state, patch, ports)

    returned = {
        **subtask,
        "status": "returned",
        "reason": reason,
        "change_category": category,
    }
    patch = apply(patch, {"subtasks": {subtask_id: returned}})
    if remaining > 0:
        patch = apply(
            patch,
            {
                "changes_requested": rules.record_return(state, pair),
                "change_history": [rules.change_entry(pair, category, reason)],
            },
        )
    else:
        # Returned as well, without counting: the limit is spent. The human's answer
        # resets the pair and the resume sends the subtask back to its Developer.
        patch |= rules.escalation(
            "returns", rules.limit_reached(pair, cfg.max_changes_requested_per_pair, reason)
        )
        patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)
