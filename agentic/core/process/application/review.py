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

from typing import Any

from agentic.shared.configuration import Configuration, experiment_snapshot

from ..domain import ci, presubmit, rules
from ..domain.roles import QA_PERFORMANCE, TECH_LEAD, pair_for, reviewer_for
from ..domain.task import WAITING_ON_HUMAN, TaskState, apply
from .checkouts import SetupFailed, subtask_checkout_in_thread, task_base
from .ci_review import CI_SECTION, CiEvidence, read_ci
from .ports import GitError, Ports
from .presubmit import declared_checks
from .run_role import finish, run_role


async def review(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    subtask_id = state["subtask_id"]
    subtask = state["subtasks"][subtask_id]
    reviewed_before = bool(subtask.get("reviewed_head"))
    checks = declared_checks(state, ports)
    # The affected-only forms (ADR-0046) are read at the same base as `checks`, and the
    # `{base}` they carry is the task's start, never the increment under review.
    base = task_base(state, ports)
    forms = ports.stack.affected_checks(base)
    plan = (
        presubmit.AffectedPlan(base, forms, ports.stack.affected_fallback(base)) if forms else None
    )
    reviewer = reviewer_for(checks, subtask_id, mode=cfg.review_mode, affected=forms or None)
    try:
        checkout = await subtask_checkout_in_thread(state, subtask_id, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(TECH_LEAD), ports)
    pair = pair_for(subtask_id)
    remaining = rules.remaining_changes(state, pair, cfg.max_changes_requested_per_pair)
    section = rules.review_section(subtask_id)
    version = rules.next_version(state, section)
    head = ports.workspace.full_head(checkout.path)
    # A product that declares `ci`, or a plan that requires a check (GH-371), has the
    # system's own CI section in front of the reviewer and its red checks recorded as
    # findings; one that has neither is reviewed exactly as before, with no read of the
    # tracker at all (GH-216). The names come from the one rule that decides them.
    names = rules.ci_gate_names(ports.stack.ci(base), state.get("ci_required") or []).names
    evidence: CiEvidence | None = read_ci(state, names, head, ports) if names else None
    reading = state
    extra_ci: tuple[str, ...] = ()
    if evidence is not None:
        reading = {**state, "sections": {**state.get("sections", {}), CI_SECTION: evidence.section}}
        extra_ci = (CI_SECTION,)
    output, patch = await run_role(
        reading,
        reviewer,
        cfg,
        ports,
        section=section,
        extra_sections=(
            rules.developer_section(subtask_id),
            rules.return_section(state, subtask_id),
            *extra_ci,
            *_perf_table(state, subtask_id),
        ),
        cwd=checkout.path,
        subtask=subtask_id,
        base=subtask.get("reviewed_head") or checkout.base,
        review="an incremental review" if subtask.get("reviewed_head") else "a first review",
        remaining_changes=remaining,
        invariants=rules.invariants_markdown(state),
        checks=presubmit.describe_checks(checks, affected=plan),
    )
    if output is None:
        return finish(state, patch, ports)

    subtask = {**subtask, "reviewed_head": head}
    reason = output.data["reason"]
    findings, lowered = rules.lower_unbacked_blocks(output.data["findings"])
    if evidence is not None and evidence.head is not None:
        # After the lowering, so the system's own blocking finding, which always carries
        # its run link, is never lowered; the verdict below is derived from the whole list.
        findings = ci.ci_findings(evidence.head, evidence.master, names, findings)
    if subtask_id == rules.reviewed_unit_name(state):
        findings = _required_paths_findings(state, base, head, findings, ports)
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
            # `{}` on the default path, so a document written before issue #28 stays
            # byte-identical (`experiment_snapshot`'s own omit-at-default rule); the
            # resolved model travels, not only the two switches, so an operator who ran
            # the light arm on Opus by mistake can be caught reading this key after the
            # run.
            "experiment": experiment_snapshot(cfg, cfg.model_for(reviewer.slug, reviewer.kind)),
        },
    )
    if category is None:
        # An approval is a verdict that no blocking exists (ADR-0016), so it closes this
        # subtask's own earlier blocking rows that this review does not raise again. Written
        # into the patch, never derived from the status at read time (ADR-0015, addendum).
        patch = apply(
            patch,
            {"approval_settled": rules.settled_on_approval(state, subtask_id, version, findings)},
        )
        patch = apply(patch, {"subtasks": {subtask_id: {**subtask, "status": "approved"}}})
        return finish(state, patch, ports)

    returned = {
        **subtask,
        "status": "returned",
        "reason": reason,
        "change_category": category,
    }
    if rules.return_section(state, subtask_id) != rules.review_section(subtask_id):
        # PM's early final validation (ADR-0040) or QA Performance (ADR-0060) was the open
        # return; this review is now, so the next resumed Developer's delta, and the pass
        # after this one's own spec, must both come from this section
        # (`rules.return_section`), not from theirs. Written only to flip that case: an
        # ordinary return keeps the subtask with no `returned_by` key at all, which
        # `return_section` already reads as `'review'`.
        returned["returned_by"] = "review"
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
        patch = rules.logged(patch)
        patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)


def _required_paths_findings(
    state: TaskState, base: str, head: str, findings: list[dict[str, Any]], ports: Ports
) -> list[dict[str, Any]]:
    """`findings` plus the system's own blocking one when the plan names paths the issue's
    done criteria need changed and the integrated diff touches none of them (GH-371).
    Written after the lowering, like the CI findings, so an approval cannot sit over it.
    Nothing is required, or a diff git cannot read, adds nothing: reading never fails the
    review, and a finding that claims a diff it did not see would be false."""
    required: list[str] = state.get("required_paths") or []
    if not required:
        return findings
    try:
        touched = ports.workspace.changed_paths(base, head)
    except GitError:
        return findings
    missing = rules.required_paths_missing(required, touched)
    if not missing:
        return findings
    return [
        *findings,
        {
            "location": missing[0],
            "claim": "the integrated diff touches none of the plan's required paths",
            "evidence": (
                f"git diff --name-only {base}...{head} lists: "
                f"{', '.join(touched) if touched else '(no paths)'}; "
                f"the plan requires one of: {', '.join(missing)}"
            ),
            "severity": "blocking",
            "scope": "subtask",
            "category": "spec-scope",
        },
    ]


def _perf_table(state: TaskState, subtask_id: str) -> tuple[str, ...]:
    """The latest QA Performance section (GH-318, ADR-0060), only for the unit whose review
    is the last before QA and only when the plan convened performance. Nothing otherwise, so
    a task that is not convened reads exactly what it read before. It adds no tool: the
    evidence that a performance finding is fixed is the node's re-measurement."""
    if state.get("performance") and subtask_id == rules.reviewed_unit_name(state):
        return (QA_PERFORMANCE.name,)
    return ()
