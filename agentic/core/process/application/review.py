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

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from agentic.shared.configuration import Configuration, ReviewMode, experiment_snapshot

from ..domain import ci, docs, presubmit, rules, speculation
from ..domain.roles import QA_PERFORMANCE, TECH_LEAD, pair_for, reviewer_for
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, TaskState, apply
from . import ci_push, ci_trust, shadow_review
from .checkouts import SetupFailed, subtask_checkout_in_thread, task_base
from .ci_review import CI_SECTION, CiEvidence, read_ci
from .ports import Checkout, GitError, Ports
from .presubmit import declared_checks
from .run_role import Output, finish, run_role

SYSTEM_APPROVAL = "approved by the system: review_mode single"


async def run_reviewer(
    state: TaskState,
    subtask_id: str,
    cfg: Configuration,
    ports: Ports,
    checkout: Checkout,
    *,
    base: str,
    mode: ReviewMode,
    with_developer: bool,
    ci_names: tuple[str, ...],
) -> tuple[Output | None, TaskState]:
    """One reviewer run over `checkout` (GH-473): the role for `mode`, its sections, the
    product's checks and affected forms read at `base`, the plan's invariants, the docs
    review, the landing gate of `ci_names` and the performance table, then `run_role`. The
    one place that builds that call: `review` makes it for the branch and the integration, the
    shadow for each approved branch, so no second list of sections or arguments exists.

    `with_developer` puts the Developer's own section in front of the reviewer; the
    integration's `single` review leaves it out. A non-empty `ci_names` means `state` already
    holds the system's CI section (`review` read it), which is added. Returns what `run_role`
    returns: the output (or `None` when the role could not run) and its patch; the verdict,
    the lowering, the records and the status are the caller's."""
    subtask = state["subtasks"][subtask_id]
    checks = declared_checks(state, ports)
    # The affected-only forms (ADR-0046) are read at the same base as `checks`, and the
    # `{base}` they carry is the task's start, never the increment under review.
    forms = ports.stack.affected_checks(base)
    plan = (
        presubmit.AffectedPlan(base, forms, ports.stack.affected_fallback(base)) if forms else None
    )
    reviewer = reviewer_for(checks, subtask_id, mode=mode, affected=forms or None)
    return await run_role(
        state,
        reviewer,
        cfg,
        ports,
        section=rules.review_section(subtask_id),
        extra_sections=(
            *((rules.developer_section(subtask_id),) if with_developer else ()),
            *rules.return_sections(state, subtask_id),
            *((CI_SECTION,) if ci_names else ()),
            *_perf_table(state, subtask_id),
        ),
        cwd=checkout.path,
        subtask=subtask_id,
        base=subtask.get("reviewed_head") or checkout.base,
        review="an incremental review" if subtask.get("reviewed_head") else "a first review",
        remaining_changes=rules.remaining_changes(
            state, pair_for(subtask_id), cfg.max_changes_requested_per_pair
        ),
        invariants=rules.invariants_markdown(state),
        required_paths=(
            rules.required_paths_markdown(state)
            if subtask_id == rules.reviewed_unit_name(state)
            else ""
        ),
        checks=presubmit.describe_checks(checks, affected=plan),
        docs_review=docs.review_text(ports.stack.docs(base)),
        landing_gate=rules.landing_gate_text(ci_names),
    )


async def review(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    push_sleep: Callable[[float], None] = time.sleep,
) -> TaskState:
    subtask_id = state["subtask_id"]
    subtask = state["subtasks"][subtask_id]
    reviewed_before = bool(subtask.get("reviewed_head"))
    base = task_base(state, ports)
    # Only the role's slug and kind are read from this (the experiment record below); its
    # tools come from the checks `run_reviewer` reads.
    reviewer = reviewer_for({}, subtask_id, mode=cfg.review_mode)
    try:
        checkout = await subtask_checkout_in_thread(state, subtask_id, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(TECH_LEAD), ports)
    pair = pair_for(subtask_id)
    remaining = rules.remaining_changes(state, pair, cfg.max_changes_requested_per_pair)
    section = rules.review_section(subtask_id)
    version = rules.next_version(state, section)
    head = ports.workspace.full_head(checkout.path)
    experiment = experiment_snapshot(cfg, cfg.model_for(reviewer.slug, reviewer.kind))
    if rules.system_approves(state, subtask_id, cfg.review_mode):
        # The `single` arm (GH-473): the branch is approved on the head it delivered, with
        # no role, no push and no finding; the integration review is the one that reads it.
        patch = apply(
            {"sections": {section: SYSTEM_APPROVAL}, "experiment": experiment},
            rules.approval_patch(state, subtask_id, subtask, version, [], head),
        )
        return finish(state, patch, ports)
    pushed_at = await _push_speculatively(
        state, subtask_id, base, head, checkout.path, ports, push_sleep
    )
    # A product that declares `ci`, or a plan that requires a check (GH-371), has the
    # system's own CI section in front of the reviewer and its red checks recorded as
    # findings; one that has neither is reviewed exactly as before, with no read of the
    # tracker at all (GH-216). The names come from the one rule that decides them.
    names = rules.ci_gate_names(ports.stack.ci(base), state.get("ci_required") or []).names
    evidence: CiEvidence | None = read_ci(state, names, head, ports) if names else None
    reading = state
    if evidence is not None:
        reading = {**state, "sections": {**state.get("sections", {}), CI_SECTION: evidence.section}}

    async def reviewed() -> tuple[Output | None, TaskState]:
        return await run_reviewer(
            reading,
            subtask_id,
            cfg,
            ports,
            checkout,
            base=base,
            mode=cfg.review_mode,
            with_developer=not (cfg.review_mode == "single" and subtask_id == INTEGRATION),
            ci_names=names if evidence is not None else (),
        )

    shadows = shadow_review.ShadowOutcome()
    if shadow_review.due(state, subtask_id, cfg):
        # The `single` arm's first pass over the integration (GH-473): the shadow reviews run
        # beside the review and add only their own keys to whatever the node returns.
        (output, patch), shadows = await shadow_review.run_shadows(state, cfg, ports, reviewed)
    else:
        output, patch = await reviewed()

    def done(written: TaskState) -> TaskState:
        return finish(state, apply(written, shadows.patch()), ports)

    if output is None:
        return done(patch)

    subtask = {**subtask, "reviewed_head": head}
    # The landing lowering is first (GH-441): a finding that asks for what only the pull
    # request's CI can give is an observation before any other rule reads its severity.
    findings, landed = rules.lower_landing_blocks(output.data["findings"])
    findings, unbacked = rules.lower_unbacked_blocks(findings)
    lowered = [*landed, *unbacked]
    own = findings
    if evidence is not None and evidence.head is not None:
        # After the lowering, so the system's own blocking finding, which always carries
        # its run link, is never lowered; the verdict below is derived from the whole list.
        findings = ci.ci_findings(evidence.head, evidence.master, names, findings)
    revised, refusal = _revised(state, subtask_id, output.data, ports)
    if subtask_id == rules.reviewed_unit_name(state):
        applied = {
            **state,
            "required_paths_revisions": [
                *state.get("required_paths_revisions", []),
                *revised.get("required_paths_revisions", []),
            ],
        }
        findings = _required_paths_findings(applied, base, head, findings, ports)
    system = rules.appended(own, findings)
    category = rules.change_category(findings)
    # One rebind, after the verdict: the reason is the returning row's, the system's when
    # that is the one that blocks (GH-511), and nothing below reads the reviewer's own.
    reason = rules.verdict_reason(own, system, output.data["reason"])
    markdown = rules.review_markdown(
        category, reason, output.text, lowered, rules.system_blocking(system)
    )
    if refusal:
        markdown += f"\n\nPath revision refused by the system: `{refusal}`."
    patch = apply(patch, {"sections": {section: markdown}, **revised})
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
            "experiment": experiment,
        },
    )
    if category is None:
        # An approval is a verdict that no blocking exists (ADR-0016), so it closes this
        # subtask's own earlier blocking rows that this review does not raise again. Written
        # into the patch, never derived from the status at read time (ADR-0015, addendum).
        patch = apply(
            patch, rules.approval_patch(state, subtask_id, subtask, version, findings, head)
        )
        if pushed_at is not None and rules.full_commit_id(head):
            patch = apply(patch, {"ci_speculation": {"head": head, "pushed_at": pushed_at}})
        return done(patch)

    returned = {
        # A verifier set's `returned_with` goes with the returners this review replaces.
        **{key: value for key, value in subtask.items() if key != "returned_with"},
        "status": "returned",
        "reason": reason,
        "change_category": category,
    }
    if rules.return_sections(state, subtask_id) != (rules.review_section(subtask_id),):
        # PM's early final validation (ADR-0040) or QA Performance (ADR-0060) was the open
        # return; this review is now, so the next resumed Developer's delta, and the pass
        # after this one's own spec, must both come from this section
        # (`rules.return_sections`), not from theirs. Written only to flip that case: an
        # ordinary return keeps the subtask with no `returned_by` key at all, which
        # `return_section` already reads as `'review'`.
        returned["returned_by"] = "review"
    patch = apply(patch, {"subtasks": {subtask_id: returned}})
    if state.get("ci_speculation") and subtask_id == rules.reviewed_unit_name(state):
        # A return discards the pushed run (GH-465): the head will move, and the record that
        # named the old one must not stand for the next.
        patch = apply(patch, {"ci_speculation": None})
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
        patch = rules.logged(patch, at=ports.now())
        patch["status"] = WAITING_ON_HUMAN
    return done(patch)


async def _push_speculatively(
    state: TaskState,
    subtask_id: str,
    base: str,
    head: str,
    path: Path,
    ports: Ports,
    sleep: Callable[[float], None],
) -> str | None:
    """Publish `head` to the task's CI ref when `speculation.push_due` says so (GH-465), so
    the full tier runs while the Tech Lead reads it; the ISO instant of the push, or `None`
    when nothing was pushed. A product without `ci:` or `delivery_checks` makes no call at
    all. A push that fails never fails the review: nothing is recorded and `full_checks`
    publishes as it always did. The push is retried by what git said (`ci_push`); one that
    still fails leaves one line in the log and nothing in the document."""
    ci_names = ports.stack.ci(base)
    tiered = bool(ports.stack.delivery_checks(base))
    if not ci_names or not tiered or subtask_id != rules.reviewed_unit_name(state):
        return None
    trust = await anyio.to_thread.run_sync(
        lambda: ci_trust.read(
            ports, head, task_base=base, resolve_master=lambda: ports.workspace.upstream()
        )
    )
    if not speculation.push_due(
        state,
        subtask_id,
        ci_names=ci_names,
        tiered=tiered,
        untrusted=rules.ci_untrusted(trust.paths),
        head=head,
    ):
        return None
    ref = rules.ci_ref(state["id"])
    try:
        await anyio.to_thread.run_sync(
            lambda: ci_push.publish_with_retry(
                lambda: ports.workspace.publish_ci_ref(path, ref), sleep=sleep
            )
        )
    except ci_push.PushFailed as failed:
        # The text is already through `scrub`: one line, no credentials. Never a span or label.
        print(
            f"agentic: review: {state['id']}: speculative push failed after "
            f"{failed.attempts} attempt(s) ({failed.kind}): {failed.said}",
            file=sys.stderr,
        )
        return None
    return datetime.now(UTC).isoformat()


def _revised(
    state: TaskState, subtask_id: str, data: dict[str, Any], ports: Ports
) -> tuple[TaskState, str]:
    """The patch for a revision of the plan's `required_paths` the deciding review asked for
    (GH-538), and the closed reason `rules.path_revision` refused it with. Only the deciding
    review's `data` comes here, never a shadow's. Nothing asked is `({}, "")`; a refusal leaves
    the list in force where it was."""
    if not (decision := data.get("revision_decision", 0)) > 0:
        return {}, ""
    revision = rules.path_revision(
        state,
        subtask_id,
        data.get("revised_required_paths", []),
        decision,
        data.get("revision_reason", ""),
        ports.now(),
    )
    if isinstance(revision, str):
        return {}, revision
    return {"required_paths_revisions": [revision]}, ""


def _required_paths_findings(
    state: TaskState, base: str, head: str, findings: list[dict[str, Any]], ports: Ports
) -> list[dict[str, Any]]:
    """`findings` plus the system's own blocking one when the plan names paths the issue's
    done criteria need changed and the integrated diff touches none of them (GH-371).
    Written after the lowering, like the CI findings, so an approval cannot sit over it.
    Nothing is required, or a diff git cannot read, adds nothing: reading never fails the
    review, and a finding that claims a diff it did not see would be false."""
    required = rules.required_paths_in_force(state)
    if not required:
        return findings
    try:
        touched = ports.workspace.changed_paths(base, head)
    except GitError:
        return findings
    missing = rules.required_paths_missing(required, touched)
    if not missing:
        return findings
    hint = rules.revision_hint(state)
    return [
        *findings,
        {
            "location": missing[0],
            "claim": "the integrated diff touches none of the plan's required paths",
            "evidence": (
                f"git diff --name-only {base}...{head} lists: "
                f"{', '.join(touched) if touched else '(no paths)'}; "
                f"the plan requires one of: {', '.join(missing)}" + (f". {hint}" if hint else "")
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
