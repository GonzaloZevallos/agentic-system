"""The landing steps that fix a failure outside the code (GH-610, ADR-0048): `fix_pr_body`
writes the product's required line into the pull request body and opens a fresh `pull_request`
run by closing and reopening it, and `escalate_api_break` asks the owner for the reason of an API
break the task never recorded.

Neither runs a model. The line is written only through `pull_request.with_required_lines`, and
the reopening is the fresh run: CI reads the body of the event that triggered it, so re-running
the old event would fail again."""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain import escalation_prompts, pull_request
from ..domain import landing as landing_rules
from . import api_break, landing_fix, landing_plan, landing_sync
from .checkouts import task_base
from .landing_plan import LandingContext
from .resume import state_from

_REFUSED = (
    "Your last answer was not taken ({reason}): the reason must be one line of at most 240 "
    "characters, with no backtick, `{{{{` or link."
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _resolve(context: LandingContext, closed_at: str | None) -> None:
    """Finishes the step's entry once the pull request is open again: `resolved_at` is the
    reopening, the instant a run must have begun after for the line to have been read."""
    document = context.ports.documents.load(context.task_id)
    if document is None:
        return
    cost = document.get("cost_usd", 0.0)
    resolved = landing_rules.resolve_open(
        landing_plan.all_entries(document),
        (context.clock or _now)(),
        cost if isinstance(cost, int | float) else 0.0,
    )
    if resolved and closed_at is not None:
        resolved[-1]["closed_at"] = closed_at
    context.ports.documents.save({**state_from(document), "landing": resolved})
    landing_sync.record_landing(context, resolved)


def fix_pr_body(context: LandingContext) -> None:
    """Edits the body (when it lacks the line), closes the pull request and reopens it, in that
    order, starting from what the tracker shows: an open pull request is edited, closed and
    reopened; one a crash left closed is edited if needed and reopened. A failure at any call, a
    reopening that is refused included, escalates as `system_delivery`: the pull request is never
    left closed in silence."""
    ports = context.ports
    tracker = ports.tracker
    document = ports.documents.load(context.task_id)
    if document is None:
        return
    raw = state_from(document)
    reference = str(raw.get("pull_request") or "")
    reason = landing_rules.recorded_api_break(
        str(raw.get("api_break") or ""), landing_plan.live_entries(raw)
    )
    try:
        line = ports.stack.api_break_line(task_base(raw, ports))
    except (ValueError, OSError):
        line = None
    try:
        number = api_break.pull_request_number(reference)
    except ValueError:
        number = None
    if line is None or reason is None or number is None:
        landing_fix.escalate_landing(
            context, "the pull request body cannot be fixed: no line, reason or pull request"
        )
        return
    closed_at: str | None = None
    try:
        current = tracker.pull_request(reference)
        if current is None:
            raise ValueError("the pull request was not found")
        body = tracker.pull_request_body(reference)
        fixed = pull_request.with_required_lines(body, line.prefix, reason)
        if fixed != body:
            tracker.update_pull_request_body(number, fixed)
        if current.state == "OPEN":
            tracker.close_pull_request(reference)
            closed_at = (context.clock or _now)()
    except (ValueError, OSError) as exc:
        landing_fix.escalate_landing(context, f"the pull request body could not be fixed: {exc}")
        return
    try:
        tracker.reopen_pull_request(number)
    except (ValueError, OSError) as exc:
        landing_fix.escalate_landing(
            context,
            f"the pull request was closed to get a fresh run and could not be reopened: {exc}",
        )
        return
    _resolve(context, closed_at)
    context.log(
        f"agentic: land: {context.task_id}: {context.head} fix_pr_body: "
        f"{line.prefix} written, pull request reopened for a fresh run"
    )


def question(line_prefix: str, step: str, head: str, rejected: str | None = None) -> str:
    """The neutral question of the `api_break_missing` row, with the reason an earlier answer was
    refused when there is one."""
    text = escalation_prompts.prompt_for("questions", "api_break_missing")[0].format(
        step=step, sha=head[:12], prefix=line_prefix
    )
    return f"{text} {_REFUSED.format(reason=rejected)}" if rejected else text


def escalate_api_break(context: LandingContext, rejected: str | None = None) -> None:
    """Asks the owner, once per head, for the reason of the API break the task never recorded
    (class `questions`, the cause `api_break_missing`). The entry is resolved after the comment
    is posted, so a comment that fails leaves it open and the next round asks again. With
    `rejected`, the same question is published again with the reason the last answer was refused."""
    ports = context.ports
    document = ports.documents.load(context.task_id)
    if document is None:
        return
    try:
        line = ports.stack.api_break_line(task_base(state_from(document), ports))
    except (ValueError, OSError):
        line = None
    if line is None:
        context.log(f"agentic: land: {context.task_id}: escalate_api_break: no api_break_line")
        return
    landing_fix.escalate_landing(
        context,
        "the pull request body lacks the required line and the task recorded no API break",
        "questions",
        [question(line.prefix, line.step, context.head, rejected)],
    )
    if rejected is None:
        _resolve(context, None)
