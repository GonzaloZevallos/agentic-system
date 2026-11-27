"""PM validates what the Product Analyst wrote: approves and consolidates, or returns
it to the Product Analyst within the pair's limit (README.md, 3 and 4; ADR-0006). The
consolidation lands in the task's checkout and is committed there (ADR-0011).

On a feature that is the PRD, and PM's approval always consolidates. On a bug
it is the diagnosis (issue #65): PM's approval carries a ruling, `bug` sends the task
to the plan and `expected_behaviour` discards it, and PM consolidates only when the
ruling fixes a definition the product did not have. One node for both, because the
pair, its limit and the return are the same; which PM runs is the type's call."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import PAIR_PM_PA, PM, PM_BUG, Role
from ..domain.schemas import VerdictWithSpecDraft
from ..domain.spec_brief import DRAFT_BLOCK
from ..domain.spec_delivery import (
    draft_body_refusal,
    draft_refusal,
    draft_retry,
    draft_text,
)
from ..domain.task import WAITING_ON_HUMAN, TaskState
from . import spec_draft
from .checkouts import SetupFailed, task_checkout_in_thread
from .ports import Checkout, Ports
from .run_role import Check, finish, run_role


async def validate_prd(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    role = validator_for(state)
    try:
        task = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(role), ports)
    limit = cfg.max_changes_requested_per_pair
    remaining = rules.remaining_changes(state, PAIR_PM_PA, limit)
    output, patch = await run_role(
        state,
        role,
        cfg,
        ports,
        check=_check(state, task, cfg, ports),
        cwd=task.path,
        remaining_changes=remaining,
        # `tiers` is run_role's slot for a system-written section appended after the playbook (its
        # docstring names the GH-358 check tier); the draft Spec section rides there, and is
        # empty unless `rules.spec_fallback_due`.
        tiers=draft_section(state, ports),
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
        if rules.spec_fallback_due(state):
            patch["spec_draft"] = spec_draft.deliver(state, data, task, cfg, ports)
        patch["status"] = "definition_ready"
    elif rules.on_return_limit(state, PAIR_PM_PA, limit) == "count":
        patch["changes_requested"] = rules.record_return(state, PAIR_PM_PA)
        patch["change_history"] = [rules.change_entry(PAIR_PM_PA, rules.PRD_RETURN, data["reason"])]
        patch["status"] = "returned"
    else:
        # The pair hit its limit: escalate, always (README.md, 3). PM knew this was
        # its last return, so the reason is written for the human.
        patch |= rules.escalation("returns", rules.limit_reached(PAIR_PM_PA, limit, data["reason"]))
        patch = rules.logged(patch, at=ports.now())
        patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)


def validator_for(state: TaskState) -> Role:
    """Which PM validates this task: the one that rules on a diagnosis for a bug, the one
    that validates a PRD otherwise. Same name, same section, same pair: only the
    schema and the prompt change (issue #65)."""
    if state.get("type") == "bug":
        return PM_BUG
    # The schema that may carry the draft, only when one is due: the false case is `PM` itself.
    return replace(PM, schema=VerdictWithSpecDraft) if rules.spec_fallback_due(state) else PM


def draft_section(state: TaskState, ports: Ports) -> str:
    """The constant section that asks PM for the Spec body: the block when a draft is due
    (`rules.spec_fallback_due`), empty otherwise, so every other prompt is what it was."""
    return ports.prompts.spec_block(DRAFT_BLOCK) if rules.spec_fallback_due(state) else ""


def _check(state: TaskState, task: Checkout, cfg: Configuration, ports: Ports) -> Check:
    """`run_role`'s check: the knowledge's approval check as it is, and, only when a draft Spec is
    due (`rules.spec_fallback_due`), that check followed by the draft pre-check."""
    approval = ports.knowledge.approval_check(state.get("type", ""))
    if not rules.spec_fallback_due(state):
        return approval
    return _with_draft_check(approval, task, cfg, ports)


def _with_draft_check(approval: Check, task: Checkout, cfg: Configuration, ports: Ports) -> Check:
    """`approval`, then a synchronous check of the draft body PM approved, so a body `deliver`
    would refuse goes back to PM once, with the reason, before the verdict counts. It raises
    `ValueError` (what `run_role` retries on, an `OutputRefusal` from `draft_refusal`) with the
    fixed sentence of the cause. The preview of
    the draft approved by the owner runs only with a well-formed `owner_login` and clock
    (`spec_draft.draft_cause`); without them it is skipped, never an error. Nothing is read from
    the checkout and nothing is written.

    A draft with more than one slice (`preview_slice_missing`) is refused on the closure's first
    call past the approval gate, with the fold-or-split sentence, and let through on every later
    call (`draft_retry`): `deliver` then records it as `split_proposed`. The count is of calls
    of this closure, whatever refused the earlier one."""
    validator = ports.spec_validator
    calls = 0

    def check(data: dict[str, Any]) -> None:
        nonlocal calls
        approval(data)
        if validator is None or data.get("verdict") != "approved":
            return
        calls_before, calls = calls, calls + 1
        body = data.get("spec_draft")
        cause = draft_body_refusal(body)
        if cause is None and isinstance(body, str):
            cause = spec_draft.draft_cause(
                draft_text(body), validator, task.base, cfg.owner_login, ports.now()
            ).cause
        if cause is not None and draft_retry(cause, calls_before) == "refuse":
            raise draft_refusal(cause)

    return check
