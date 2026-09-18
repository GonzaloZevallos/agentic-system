"""Running one role inside a task: context, prompt, attempt, retry, delivery.

A role failure is one of four things (README.md, 3 and 14): the harness broke, the
model ran out of turns, its structured output is missing or invalid, or its
`markdown` is empty. `run_with_retry` retries once with the error as input: inside the
session the attempt left when there is one, appended to the prompt of a fresh session
when there is none (ADR-0004, addendum). A second failure raises `RoleFailure`, which
the use case turns into an escalation. The graph engine's own retry was rejected
because it reruns the node without telling the model what went wrong.

The task cap is checked here too, before every attempt, because every role passes
through here: the routing between nodes would miss the rounds of an interview.
Reached, `TaskCapReached` escalates the same way, but it is not a role failure: the
role did not break (ADR-0004, third addendum). The cap is read off the state each
time, so it grows as soon as the plan is in (fourth addendum), and against what the
whole process spent, which is what the state cannot hold once branches run in
parallel (fifth addendum, `limits.py`).

Context rule (README.md, 6): each role reads, in full, the sections it declares in
`Role.reads`, after the engineering standards and the product knowledge if it reads
them. Not the previous role's handoff: a verifier that only sees a summary returns
over gaps that were never there.

The one exception (ADR-0004, sixth addendum): a role resuming its own earlier session
over a return reads a delta instead, because the session already holds what `Role.reads`
would repeat; the rule still holds through the session's own memory. When there is no
session to resume, the cold path restores the full reading, `cold_prompt` included.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain import document, rules, schemas
from ..domain.document import HUMAN
from ..domain.roles import ORIGINAL, Prompt, Role, describe_flags, render
from ..domain.task import WAITING_ON_HUMAN, EscalationClass, Failure, RoleRun, TaskState, apply
from .limits import RunLimits
from .ports import AttemptFailed, Ports, Presubmit, RoleRunner

Check = Callable[[dict], None]


def spent(failures: list[Failure]) -> float:
    """What the failed attempts cost: paid, so they count."""
    return sum(f["cost_usd"] for f in failures)


class Escalating(Exception):
    """A role stopped without a delivery; its message is the escalation's reason. The
    attempts paid before it stopped, if any, travel with it."""

    def __init__(self, role: Role, failures: list[Failure], reason: str):
        self.role = role
        self.failures = failures
        super().__init__(reason)

    @property
    def cost_usd(self) -> float:
        return spent(self.failures)


class RoleFailure(Escalating):
    def __init__(self, role: Role, failures: list[Failure]):
        last = failures[-1]["error"] if failures else "no attempts were made"
        super().__init__(role, failures, f"{role.name} failed {len(failures)} times: {last}")


class TaskCapReached(Escalating):
    """The run reached its money cap before this role's next attempt, be it the
    first or the retry after a paid failure."""

    def __init__(self, role: Role, spent_usd: float, cap_usd: float, failures: list[Failure]):
        self.spent_usd = spent_usd
        self.cap_usd = cap_usd
        super().__init__(role, failures, rules.cap_reached(role.name, spent_usd, cap_usd))


@dataclass
class Output:
    text: str
    data: dict
    cost_usd: float = 0.0
    turns: int = 0
    session_id: str = ""
    model: str = ""
    # The successful attempt's cost by model; a failed attempt only carries its total.
    cost_by_model: dict[str, float] = field(default_factory=dict)
    # Token usage of the delivering call, by kind (`RawResult.usage`'s own docstring
    # has the per-call-not-cumulative evidence). Empty on a failed attempt: only the
    # delivery carries it.
    usage: dict[str, int] = field(default_factory=dict)
    # Calls to the model in this run (`RawResult.calls`'s own docstring: one per
    # `AssistantMessage` seen before the delivery).
    calls: int = 0
    # `tool_use` blocks of this run, by tool name (`RawResult.tool_calls`).
    tool_calls: dict[str, int] = field(default_factory=dict)
    # What of `cost_by_model` a sub-agent spent: every model but this run's own
    # (ADR-0013). Not additive with `cost_usd`.
    subagent_cost_usd: float = 0.0
    # Failed attempts before this output. Empty on a clean run. Their cost is
    # already included in `cost_usd`: a paid attempt counts even when it failed.
    failures: list[Failure] = field(default_factory=list)
    # When the winning attempt started and ended, and how long it waited for a
    # concurrency slot first: `run_with_retry` fills these in after the slot was
    # acquired, so the interval they bound is only work, never the queue.
    started_at: str = ""
    ended_at: str = ""
    queue_wait_s: float = 0.0
    # The session the winning attempt actually resumed, decided by `run_with_retry`
    # (never re-derived from the caller's own `resume` argument, which lies on two
    # paths: a retry that continues a *failed* attempt's session unasked, and a cold
    # fallback that resumed nothing while the argument held a valid id). Empty when the
    # winning attempt resumed no session.
    resumed_from: str = ""
    # Whether the winning attempt is this run's cold fallback (ADR-0004, sixth
    # addendum): the caller asked to resume a session that never opened, so
    # `run_with_retry` dropped it and ran this attempt cold instead of retrying the
    # stale id.
    cold_fallback: bool = False


def context_for(
    state: TaskState,
    role: Role,
    product_knowledge: str = "",
    standards: str = "",
    extra_sections: tuple[str, ...] = (),
) -> str:
    """The role's input: the standards and the product knowledge if the role reads
    them, then the sections it declares plus `extra_sections` (a subtask's own), in
    document order, that exist, and last the human's decisions, which every role reads:
    they hold for the whole task and override what they contradict."""
    sections = state.get("sections", {})
    parts = [text for text in (standards, product_knowledge) if text]
    for name in (*role.reads, *extra_sections, HUMAN):
        if name == ORIGINAL:
            request = document.demote(state["description"])
            parts.append(f"## {ORIGINAL}\n\n{state['title']}\n\n{request}")
        elif name in sections:
            parts.append(f"## {name}\n\n{sections[name]}")
    return "\n\n".join(parts)


def preamble(role: Role, ports: Ports, root: Path | None = None) -> tuple[str, str]:
    """What the role reads before the task document: (standards, product knowledge),
    as found in the checkout the role works in."""
    standards = ports.knowledge.standards(root) if role.reads_standards else ""
    knowledge = ports.knowledge.read(root) if role.reads_knowledge else ""
    return standards, knowledge


def with_context(
    state: TaskState,
    role: Role,
    ports: Ports,
    cwd: Path | None,
    text: str,
    extra_sections: tuple[str, ...] = (),
) -> str:
    """`text` after the role's full reading, for a role with no session to resume (an
    interview's first round, a consultant with none) and for the cold prompt a caller
    hands `run_with_retry` when a resumed session turns out stale: the full context the
    session would otherwise skip, plus whatever `text` adds on top of it."""
    standards, knowledge = preamble(role, ports, cwd)
    return context_for(state, role, knowledge, standards, extra_sections) + "\n\n" + text


def load_prompt(
    role: Role, cfg: Configuration, ports: Ports, vocabulary: dict[str, str], **placeholders: object
) -> Prompt:
    """The role's system prompt, overlay applied, with the limits and the vocabulary in."""
    raw, source = ports.prompts.raw(role)
    return render(
        raw,
        source,
        max_changes=cfg.max_changes_requested_per_pair,
        max_turns=role.max_turns,
        flags=describe_flags(vocabulary),
        **placeholders,
    )


async def run_with_retry(
    role: Role,
    prompt: str,
    *,
    system_prompt: str,
    model: str,
    retries: int,
    vocabulary: dict[str, str],
    cwd: Path,
    runner: RoleRunner,
    check: Check | None = None,
    resume: str | None = None,
    max_budget_usd: float | None = None,
    spent_usd: float = 0.0,
    task_cap_usd: float | None = None,
    presubmit: Presubmit | None = None,
    limits: RunLimits | None = None,
    section: str | None = None,
    cold_prompt: str | None = None,
) -> Output:
    """`check` is the use case's own rule over the validated output (raise `ValueError`);
    breaking it is a role failure like any other, so the model gets the error back.

    `spent_usd` is what the task spent before this call; with `task_cap_usd`
    set, an attempt that would start at or over the cap raises `TaskCapReached`
    instead. A failed attempt counts: the retry can cost another attempt cap.

    `limits` is the run as a process (`limits.py`): the money every branch of a fan-out
    spent, which the state cannot hold, and how many roles may run at once. The cap is
    read against whichever of the two views is larger, so a branch stops on its
    sisters' spending too. It only refuses to start; a role already running is never
    cut off.

    `presubmit` is handed to every attempt: the harness asks it when the model delivers.

    `section` is what a successful attempt would write (`run_record`'s own default,
    the role's name, when the caller has none yet): a failed attempt stamps it on its
    `Failure` too, so a reader can tell which delivery an attempt was working on.

    `cold_prompt` is the caller's own full context, used at most once, when `resume`
    turns out stale: the first attempt held a caller-supplied session and the harness
    never opened one of its own (`AttemptFailed.session_id` empty). Retrying the same
    id again would just repeat the failure, so that one retry drops it and runs cold
    with `cold_prompt` instead (ADR-0004, sixth addendum). `None` (an interview's turn,
    which owns its own resume/prompt pairing already) keeps today's retry, which tries
    the caller's session again like any other. The decision is made here, once, and
    every reader (`resumed_from`, the `cold_fallback` flag on the `RoleRun`/`Failure`)
    reads it from this call, never re-derives it from `resume` on its own."""
    limits = limits or RunLimits()
    written_as = section or role.name
    failures: list[Failure] = []
    cold_fallback = False  # this run already spent its one fallback
    for attempt in range(1, max(retries, 0) + 2):
        total = max(spent_usd + spent(failures), limits.spent_usd)
        if task_cap_usd is not None and total >= task_cap_usd:
            raise TaskCapReached(role, total, task_cap_usd, failures)
        fallback_now = not cold_fallback and _stale_session(resume, failures, cold_prompt)
        if fallback_now:
            # The one drop: no error appended, since the caller's session never even
            # opened, so there is nothing this fresh cold start is fixing. From here on
            # the run *is* a cold run: `prompt`/`resume` are replaced for good, so a
            # further attempt (a second failure past the fallback) retries the cold
            # session it just opened, never the id this run already gave up on.
            text, session = cold_prompt, None
            prompt, resume = cold_prompt, None
            cold_fallback = True
        else:
            text, session = _retry_input(prompt, failures, resume)
        waited_from = time.monotonic()
        try:
            async with limits.slot():
                queue_wait_s = time.monotonic() - waited_from
                started_at = _now_iso()
                output = await _attempt(
                    role,
                    text,
                    system_prompt=system_prompt,
                    model=model,
                    vocabulary=vocabulary,
                    cwd=cwd,
                    runner=runner,
                    check=check,
                    resume=session,
                    max_budget_usd=max_budget_usd,
                    presubmit=presubmit,
                )
                ended_at = _now_iso()
        except AttemptFailed as exc:
            limits.record(exc.cost_usd)
            failures.append(
                {
                    "role": role.name,
                    "attempt": attempt,
                    "error": str(exc),
                    "cost_usd": exc.cost_usd,
                    "session_id": exc.session_id,
                    "reason": exc.reason,
                    "slug": role.slug,
                    "section": written_as,
                    "cold_fallback": fallback_now,
                }
            )
            continue
        limits.record(output.cost_usd)
        output.failures = failures
        output.cost_usd += spent(failures)
        output.started_at = started_at
        output.ended_at = ended_at
        output.queue_wait_s = queue_wait_s
        output.resumed_from = session or ""
        output.cold_fallback = fallback_now
        return output
    raise RoleFailure(role, failures)


def _stale_session(resume: str | None, failures: list[Failure], cold_prompt: str | None) -> bool:
    """Whether the run's very first attempt held a caller-supplied session the harness
    never opened: the one condition that spends the cold fallback (`run_with_retry`'s
    own docstring). Gated on `cold_prompt`: a caller with none keeps today's retry,
    which tries the same stale id again instead of dropping it."""
    return (
        cold_prompt is not None
        and resume is not None
        and len(failures) == 1
        and not failures[0]["session_id"]
    )


def _now_iso() -> str:
    """ISO-8601 UTC, millisecond precision: the format `RoleRun.started_at` and
    `.ended_at` share."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _retry_input(
    prompt: str, failures: list[Failure], resume: str | None
) -> tuple[str, str | None]:
    """What the next attempt gets: (prompt, session to resume).

    A failed attempt that left a session is continued with the error as the next
    message and nothing else, since the prompt is already in there: the model keeps
    what it read and did, and a Developer cut while delivering just delivers
    (ADR-0004, addendum). One that left none, because the harness broke before
    opening it, starts over: the caller's session if any, the error appended to the
    prompt."""
    if not failures:
        return prompt, resume
    error = (
        f"<PreviousAttemptFailed>\n{failures[-1]['error']}\n</PreviousAttemptFailed>\n\n"
        "Retry. Fix exactly what failed."
    )
    if left := failures[-1]["session_id"]:
        return error, left
    return f"{prompt}\n\n{error}", resume


async def _attempt(
    role: Role,
    prompt: str,
    *,
    system_prompt: str,
    model: str,
    vocabulary: dict[str, str],
    cwd: Path,
    runner: RoleRunner,
    check: Check | None,
    resume: str | None,
    max_budget_usd: float | None,
    presubmit: Presubmit | None = None,
) -> Output:
    result = await runner.run_once(
        role,
        prompt,
        system_prompt=system_prompt,
        model=model,
        json_schema=schemas.json_schema(role.schema, vocabulary),
        cwd=cwd,
        resume=resume,
        max_budget_usd=max_budget_usd,
        presubmit=presubmit,
    )
    cost, session = result.cost_usd, result.session_id
    if result.structured_output is None:
        raise AttemptFailed(
            "structured output missing; it is required", cost, session, reason="schema"
        )
    try:
        data = schemas.validate(role.schema, result.structured_output, vocabulary)
    except ValueError as exc:
        raise AttemptFailed(
            f"structured output invalid: {rules.exception_motive(exc)}",
            cost,
            session,
            reason="schema",
        ) from exc
    if check is not None:
        try:
            check(data)
        except ValueError as exc:
            raise AttemptFailed(
                f"structured output invalid: {rules.exception_motive(exc)}",
                cost,
                session,
                reason="check",
            ) from exc
    text = data.pop("markdown").strip()
    if not text:
        raise AttemptFailed(
            "`markdown` is empty; the Markdown section is required", cost, session, reason="schema"
        )
    return Output(
        text=text,
        data=data,
        cost_usd=cost,
        turns=result.turns,
        session_id=result.session_id,
        model=model,
        cost_by_model=result.cost_by_model,
        usage=result.usage,
        calls=result.calls,
        tool_calls=result.tool_calls,
        subagent_cost_usd=_subagent_cost_usd(result.cost_by_model, model),
    )


def _subagent_cost_usd(cost_by_model: dict[str, float], model: str) -> float:
    """What of `cost_by_model` a sub-agent spent: every model but the role's own
    (ADR-0013, addendum: every sub-agent runs in the subagent model, not the role's)."""
    return sum(cost for spent_on, cost in cost_by_model.items() if spent_on != model)


def task_cap(state: TaskState, cfg: Configuration) -> float:
    """The cap in force for this state: the formula over the plan (ADR-0004, fourth
    addendum), or the human's when a resume set a higher one. The human raises the cap,
    never lowers it: a budget given before the plan must not stop the task again when the
    plan makes the formula larger."""
    formula = rules.task_cap(
        state, cfg.task_budget_base_usd, cfg.task_budget_per_subtask_usd, cfg.task_max_budget_usd
    )
    return max(formula, cfg.task_budget_usd or 0.0)


def escalation(exc: Escalating) -> TaskState:
    """The patch of a role that failed twice, or that the task cap stopped: the human
    sees it (README.md, 3), with its escalation class (`rules.escalation`) — `cap` for
    the task cap, `failure` for a role `run_with_retry` gave up on."""
    cls: EscalationClass = "cap" if isinstance(exc, TaskCapReached) else "failure"
    return {
        "failures": exc.failures,
        "cost_usd": exc.cost_usd,
        "status": WAITING_ON_HUMAN,
        **rules.escalation(cls, str(exc)),
    }


def run_record(state: TaskState, role: Role, section: str, output: Output) -> RoleRun:
    return {
        "role": role.name,
        "slug": role.slug,
        "section": section,
        "version": rules.next_version(state, section),
        "session_id": output.session_id,
        "model": output.model,
        "cost_usd": output.cost_usd,
        "cost_by_model": output.cost_by_model,
        "turns": output.turns,
        "max_turns": role.max_turns,
        "started_at": output.started_at,
        "ended_at": output.ended_at,
        "queue_wait_s": output.queue_wait_s,
        "tokens": output.usage,
        "calls": output.calls,
        "tool_calls": output.tool_calls,
        "subagent_cost_usd": output.subagent_cost_usd,
        "resumed_from": output.resumed_from,
        "cold_fallback": output.cold_fallback,
    }


async def run_role(
    state: TaskState,
    role: Role,
    cfg: Configuration,
    ports: Ports,
    check: Check | None = None,
    *,
    section: str | None = None,
    extra_sections: tuple[str, ...] = (),
    cwd: Path | None = None,
    resume: str | None = None,
    prompt: str | None = None,
    cold_prompt: str | None = None,
    presubmit: Presubmit | None = None,
    **placeholders: object,
) -> tuple[Output | None, TaskState]:
    """Run a role and build the state patch. On `RoleFailure`, or when the task cap
    stops the role, the patch is an escalation and the output is `None`. Saving the
    document is the caller's (`finish`).

    `section` is where the delivery goes (the role's name by default); `cwd` where the
    role runs (the product root by default); `resume` continues the role's earlier
    session; `prompt` replaces the context with a shorter message when the session
    already holds it (an interview's later rounds); `presubmit` is asked when it
    delivers. `cold_prompt` is the full context for the one retry that drops `resume`
    when it turns out stale (`run_with_retry`'s own doc): `run_role` never builds one on
    its own, since only the caller knows what a message it did not build should contain
    (ADR-0004, sixth addendum) — a caller that passes `resume` with no `cold_prompt`
    keeps today's retry, which tries the same session again."""
    section = section or role.name
    vocabulary = ports.flags.load()
    loaded = load_prompt(role, cfg, ports, vocabulary, **placeholders)
    patch: TaskState = {"current_role": role.name, "prompts": {role.slug: loaded.hash}}
    if prompt is None:
        standards, knowledge = preamble(role, ports, cwd)
        prompt = context_for(state, role, knowledge, standards, extra_sections)
    try:
        output = await run_with_retry(
            role,
            prompt,
            system_prompt=loaded.text,
            model=cfg.model_for(role.slug, role.kind),
            retries=cfg.role_retries,
            vocabulary=vocabulary,
            cwd=cwd or cfg.product_dir,
            runner=ports.runner,
            check=check,
            resume=resume,
            max_budget_usd=cfg.max_budget_usd,
            spent_usd=state.get("cost_usd", 0.0),
            task_cap_usd=task_cap(state, cfg),
            presubmit=presubmit,
            limits=ports.limits,
            section=section,
            cold_prompt=cold_prompt,
        )
    except Escalating as exc:
        return None, patch | escalation(exc)

    patch |= {
        "sections": rules.sections_for(state, section, output.text),
        "failures": output.failures,
        "cost_usd": output.cost_usd,
        "runs": [run_record(state, role, section, output)],
    }
    return output, patch


def finish(state: TaskState, patch: TaskState, ports: Ports) -> TaskState:
    """Write the document once per role, with the reducers applied, and return the patch."""
    ports.documents.save(apply(state, patch))
    return patch
