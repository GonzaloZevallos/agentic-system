"""Running one role inside a task: context, prompt, attempt, retry, delivery.

A role failure is one of four things (README.md, 3 and 14): the harness broke, the
model ran out of turns, its structured output is missing or invalid, or its
`markdown` is empty. `run_with_retry` retries once with the error as input: inside the
session the attempt left when there is one, appended to the prompt of a fresh session
when there is none (ADR-0004, addendum). A second failure raises `RoleFailure`, which
the use case turns into an escalation. The graph engine's own retry was rejected
because it reruns the node without telling the model what went wrong.

A `max_turns` cut is not an ordinary failure: the harness stopped the attempt on the
turn cap alone, and the work up to that point may already be done. `_retry_input`
gives that one reason its own message (`rules.turn_cut_retry_message`, ADR-0004,
seventh addendum) instead of the "Retry. Fix exactly what failed." every other
`FailureReason` still gets — a role whose work was fine must not be told to fix it.

The task cap is checked here too, before every attempt, because every role passes
through here: the routing between nodes would miss the rounds of an interview.
Reached, `TaskCapReached` escalates the same way, but it is not a role failure: the
role did not break (ADR-0004, third addendum). The cap is a flat ceiling, the same for any
plan size (2026-10-05 addendum), checked against what the whole process spent, which is
what the state cannot hold once branches run in parallel (fifth addendum, `limits.py`).
Beside it, before the same attempt, the safety cap of the task's estimate (ADR-0053):
`EstimateCapReached`, its own escalation class and text, never the flat cap's.

Context rule (README.md, 6): each role reads, in full, the sections it declares in
`Role.reads`, after the engineering standards and the product knowledge if it reads
them. Not the previous role's handoff: a verifier that only sees a summary returns
over gaps that were never there.

The one exception (ADR-0004, sixth addendum): a role resuming its own earlier
session — a Developer's return, one seat of a retaken interview (ADR-0014 addendum) —
reads a delta instead, because the session already holds what `Role.reads` would
repeat; the rule still holds through the session's own memory. When there is no
session to resume, the cold path restores the full reading. `cold_prompt` does not
follow automatically from resuming: the caller passes one only when `with_context` can
actually rebuild that full reading from what is already committed to the state it is
given — a return's own diff, a retake's section, already written by the time it
resumes — never from a turn still only in the caller's own local accumulator. That is
why an interview's ordinary round passes none even once a side resumes its own session
mid-run: the transcript is not yet a section for `with_context` to find.

A second exception (issue #71, `plan-trim`): a reader with a plan subtask of its own —
the Developer, the reviewer — does not read the `Tech Lead` section's `### Subtasks` in
full either. `context_for` projects it to that reader's own brief, rendered from
`state["subtasks"]` (never from matching a `#### <id>` heading), plus a one-line index
of the sisters' ids and titles; every other part of the section, and every other role's
reading, is untouched. The integrator gets the section whole, like a role with no
subtask id at all: its job is the seams between subtasks, not one of them (ADR-0015).
`plan_view.for_reader` is the pure function that decides this, so `with_context` and the
cold prompt inherit it through the same seam instead of a second, silently divergent copy.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import cost_history, document, plan_view, playbook, rules, schemas, turn_notice
from ..domain.document import HUMAN
from ..domain.reaping import ReapedProcess
from ..domain.roles import (
    ORIGINAL,
    TECH_LEAD,
    Prompt,
    Role,
    describe_flags,
    prompt_hash,
    reads_by_index,
    render,
    standards_by_index,
)
from ..domain.suppressions import Forbidden
from ..domain.task import (
    INTEGRATION,
    WAITING_ON_HUMAN,
    EscalationClass,
    Failure,
    RoleRun,
    TaskState,
    apply,
)
from .closed import closed_documents
from .limits import RunLimits
from .ports import AttemptFailed, Ports, Presubmit, RoleRunner, SchemaGuard
from .schema_guard import RoleSchemaGuard

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
        reason = f"{role.name} failed {len(failures)} times: {last}"
        if count := sum(f.get("denials", 0) for f in failures):
            # The run's total, over every failed attempt: none of them delivered, so
            # there is no delivering attempt's own count to add (`_run_denials`'s own
            # rule for the run that does deliver).
            reason = f"{reason} {rules.denial_note(role, count)}"
        super().__init__(role, failures, reason)


class TaskCapReached(Escalating):
    """The run reached its money cap before this role's next attempt, be it the
    first or the retry after a paid failure."""

    def __init__(self, role: Role, spent_usd: float, cap_usd: float, failures: list[Failure]):
        self.spent_usd = spent_usd
        self.cap_usd = cap_usd
        super().__init__(role, failures, rules.cap_reached(role.name, spent_usd, cap_usd))


@dataclass(frozen=True)
class EstimateStop:
    """What a run that reached the safety cap of its estimate escalates with: the text for
    the human and the estimate the task proposes (`rules.reestimate`)."""

    reason: str
    proposed_usd: float


class EstimateCapReached(Escalating):
    """The run reached the safety cap of its estimate (ADR-0053) before this role's next
    attempt. Not a role failure, and not the flat task cap: its own class and its own text."""

    def __init__(self, role: Role, stop: EstimateStop, failures: list[Failure]):
        self.proposed_usd = stop.proposed_usd
        super().__init__(role, failures, stop.reason)


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
    # Write attempts the SDK denied during the delivering attempt alone
    # (`RawResult.denials`'s own docstring); the run's total also folds in every
    # `failures` entry's own count (`_run_denials`).
    denials: int = 0
    # Whether the live turn-budget notice fired during the delivering attempt alone
    # (`RawResult.turn_notice`'s own docstring); the run's total also folds in every
    # `failures` entry's own flag (`_run_notice`).
    turn_notice: bool = False
    # How many times the live notice fired during the delivering attempt alone
    # (`RawResult.turn_notice_firings`'s own docstring); the run's total also folds in
    # every `failures` entry's own count (`_run_notice_firings`).
    turn_notice_firings: int = 0
    # Calls to the delivery tool during the delivering attempt alone
    # (`RawResult.deliveries`'s own docstring); the run's total also folds in every
    # `failures` entry's own count (`_run_deliveries`).
    deliveries: int = 0
    # Presubmit denials during the delivering attempt alone
    # (`RawResult.presubmit_denials`'s own docstring): a different fact from
    # `deliveries` above, never folded into it.
    presubmit_denials: int = 0
    # `TurnCount.value` at the end of the delivering attempt (`RawResult.main_thread_
    # turns`'s own docstring, ADR-0031, issue #117): `0` for a role the notice does not
    # cover. No run-total fold like `turn_notice_firings` has: each attempt's own count
    # is what a `max_turns` cut needs to refute or hold up the counted-quantity
    # assumption, not a sum across retries.
    main_thread_turns: int = 0
    # What the reaper stopped when the delivering attempt ended (`RawResult.reaped`'s own
    # docstring); the run's total also folds in every `failures` entry's own list
    # (`_run_reaped`).
    reaped: list[ReapedProcess] = field(default_factory=list)


def context_for(
    state: TaskState,
    role: Role,
    product_knowledge: str = "",
    standards: str = "",
    extra_sections: tuple[str, ...] = (),
    subtask_id: str | None = None,
) -> str:
    """The role's input: the standards and the product knowledge if the role reads
    them, then the sections it declares plus `extra_sections` (a subtask's own), in
    document order, that exist, and last the human's decisions, which every role reads:
    they hold for the whole task and override what they contradict.

    `subtask_id` is the reader's own plan subtask, when it has one (the Developer, the
    reviewer): the second exception to "each role reads, in full" (this module's own
    docstring; issue #71). Only the `Tech Lead` section is projected
    (`plan_view.for_reader`), to the reader's own brief plus a sister index instead of
    every subtask's; every other declared section, and every other reader, is
    untouched. `None` (the default) keeps today's full reading, and so does an id the
    projection cannot use (`plan_view.for_reader`'s own degrade rule)."""
    sections = state.get("sections", {})
    parts = [text for text in (standards, product_knowledge) if text]
    for name in (*role.reads, *extra_sections, HUMAN):
        if name == ORIGINAL:
            request = document.demote(state["description"])
            parts.append(f"## {ORIGINAL}\n\n{state['title']}\n\n{request}")
        elif name in sections:
            text = sections[name]
            if name == TECH_LEAD.name:
                text = plan_view.for_reader(text, subtask_id, state.get("subtasks", {}))
            parts.append(f"## {name}\n\n{text}")
    return "\n\n".join(parts)


def preamble(role: Role, ports: Ports, root: Path | None = None) -> tuple[str, str]:
    """What the role reads before the task document: (standards, product knowledge),
    as found in the checkout the role works in. The knowledge is what always goes
    whole plus the area index; the standards are whole for a verifier and an index
    entry for the Developer and the integrator (`standards_by_index`)."""
    standards = (
        ports.knowledge.standards(root, whole=not standards_by_index(role))
        if role.reads_standards
        else ""
    )
    knowledge = ports.knowledge.read(root) if role.reads_knowledge else ""
    return standards, knowledge


def with_context(
    state: TaskState,
    role: Role,
    ports: Ports,
    cwd: Path | None,
    text: str,
    extra_sections: tuple[str, ...] = (),
    subtask_id: str | None = None,
) -> str:
    """`text` after the role's full reading, for a role with no session to resume (an
    interview's first round, a consultant with none) and for the cold prompt a caller
    hands `run_with_retry` when a resumed session turns out stale: the full context the
    session would otherwise skip, plus whatever `text` adds on top of it.

    `subtask_id` reaches `context_for` unchanged, so the cold prompt trims the plan the
    same way the ordinary prompt would (issue #71): the exception this function exists
    for is the session, never the reading."""
    standards, knowledge = preamble(role, ports, cwd)
    return (
        context_for(state, role, knowledge, standards, extra_sections, subtask_id) + "\n\n" + text
    )


def load_prompt(
    role: Role, cfg: Configuration, ports: Ports, vocabulary: dict[str, str], **placeholders: object
) -> Prompt:
    """The role's system prompt, overlay applied, with the limits and the vocabulary in.

    The role's playbook (ADR-0043) is appended after the placeholders are filled, so it
    never goes through them. With none, the prompt is exactly what `render` made; with
    one, the hash covers the raw text and the section, so a playbook change is a prompt
    change for comparison."""
    raw, source = ports.prompts.raw(role)
    rendered = render(
        raw,
        source,
        max_changes=cfg.max_changes_requested_per_pair,
        max_turns=role.max_turns,
        flags=describe_flags(vocabulary),
        **placeholders,
    )
    text = playbook.section(ports.prompts.playbook(role))
    if not text:
        return rendered
    return Prompt(
        text=rendered.text + "\n\n" + text,
        hash=prompt_hash(raw + "\n" + text),
        source=source,
        playbook=prompt_hash(text),
    )


def playbook_patch(state: TaskState, role: Role, loaded: Prompt) -> TaskState:
    """The playbook keys of a run's patch (ADR-0043): which playbook section the role
    ran with, for the window and `eval compare`, beside `prompts` (which stays last-wins).

    With an empty playbook nothing is written, so a task with none has neither key. The
    first non-empty hash goes in `playbooks` and is never overwritten (`task.first`). A
    different later hash goes in `playbooks_changed`, and so does the first hash of a
    role that already ran with no playbook (`prompts` has its slug, `playbooks` does
    not): the section hash of that first run is the segment `empty`, and a task that
    ran under two playbooks is excluded from every window the same way."""
    if not loaded.playbook:
        return {}
    recorded = state.get("playbooks", {}).get(role.slug)
    if recorded is None:
        patch: TaskState = {"playbooks": {role.slug: loaded.playbook}}
        if role.slug in state.get("prompts", {}):
            patch["playbooks_changed"] = {role.slug: loaded.playbook}
        return patch
    if recorded != loaded.playbook:
        return {"playbooks_changed": {role.slug: loaded.playbook}}
    return {}


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
    owner_task: str,
    check: Check | None = None,
    resume: str | None = None,
    max_budget_usd: float | None = None,
    spent_usd: float = 0.0,
    task_cap_usd: float | None = None,
    presubmit: Presubmit | None = None,
    delivery_check: SchemaGuard | None = None,
    forbidden: Sequence[Forbidden] | None = None,
    limits: RunLimits | None = None,
    section: str | None = None,
    cold_prompt: str | None = None,
    before_attempt: Callable[[], None] | None = None,
    estimate_stop: Callable[[float], EstimateStop | None] | None = None,
) -> Output:
    """`before_attempt` runs at the top of every attempt, retries included, before the
    slot is taken: `run_role` hands it the index write, so a retry sees the files its
    failed attempt left in the checkout (GH-292).

    `estimate_stop` is asked, after the flat cap, with what the run spent: it answers with
    the escalation when the estimate's safety cap is reached (`EstimateCapReached`), and
    `None` otherwise. The caller owns the decision (`rules.estimate_cap_reached`); this
    only refuses to start the attempt, like the flat cap.

    `check` is the use case's own rule over the validated output (raise `ValueError`);
    breaking it is a role failure like any other, so the model gets the error back.

    `spent_usd` is what the task spent before this call; with `task_cap_usd`
    set, an attempt that would start at or over the cap raises `TaskCapReached`
    instead. A failed attempt counts: the retry can cost another attempt cap.

    `limits` is the run as a process (`limits.py`): the money every branch of a fan-out
    spent, which the state cannot hold, and how many roles may run at once. The cap is
    read against whichever of the two views is larger, so a branch stops on its
    sisters' spending too. It only refuses to start; a role already running is never
    cut off.

    `owner_task` is the parent task's id (`state["id"]`, never a subtask's): every attempt
    is marked with it so what a role leaves running in the background can be found when
    the attempt ends (GH-282, ADR-0052).

    `presubmit` is handed to every attempt: the harness asks it when the model delivers.
    `forbidden` is handed to every attempt too: the product's own declared suppression
    cage (issue #38), `None` by default, byte for byte what every caller but a
    Developer's own already passes.

    `delivery_check` is a second in-turn refuser of the delivery (GH-174: the Developer's
    coverage map), asked after the role's own schema guard and before the presubmit.
    `None` leaves the guard the runner receives exactly as it was.

    `section` is what a successful attempt would write (`run_record`'s own default,
    the role's name, when the caller has none yet): a failed attempt stamps it on its
    `Failure` too, so a reader can tell which delivery an attempt was working on.

    `cold_prompt` is the caller's own full context, used at most once, when `resume`
    turns out stale: the first attempt held a caller-supplied session and the harness
    never opened one of its own (`AttemptFailed.session_id` empty). Retrying the same
    id again would just repeat the failure, so that one retry drops it and runs cold
    with `cold_prompt` instead (ADR-0004, sixth addendum). Whether an interview's turn
    (`interview.turn`/`.consult`) passes one turns on whether `with_context` could
    rebuild the whole reading if it had to: an ordinary turn passes `None`, because the
    transcript this round belongs to is still only a local accumulator, not yet a
    section `with_context` could read back; a retaken interview's turn (ADR-0014
    addendum) passes one, because `_record` wrote that section, on the state this call
    was given, before the escalation this retake resumes from. `None` otherwise keeps
    today's retry, which tries the caller's session again like any other. The decision
    is made here, once, and every reader (`resumed_from`, the `cold_fallback` flag on
    the `RoleRun`/`Failure`) reads it from this call, never re-derives it from `resume`
    on its own."""
    limits = limits or RunLimits()
    written_as = section or role.name
    failures: list[Failure] = []
    cold_fallback = False  # this run already spent its one fallback
    # Built once per role run, outside the attempt loop, so its one-denial cap
    # (`RoleSchemaGuard`, `domain.schema_guard.REFUSALS`) survives the retry the same
    # way `CheckoutPresubmit.refusals` does -- never rebuilt inside `_attempt`, which
    # would reset it every attempt. Wired only for a role that executes (Developer, the
    # integrator): every other role's `hooks_for`/`options_for` see `None`, byte for
    # byte what they saw before this guard existed.
    schema_guard: SchemaGuard | None = (
        RoleSchemaGuard(role.schema, vocabulary) if role.executes else None
    )
    guard = _chain(schema_guard, delivery_check)
    for attempt in range(1, max(retries, 0) + 2):
        if before_attempt is not None:
            before_attempt()
        total = max(spent_usd + spent(failures), limits.spent_usd)
        if task_cap_usd is not None and total >= task_cap_usd:
            raise TaskCapReached(role, total, task_cap_usd, failures)
        if estimate_stop is not None and (stop := estimate_stop(total)) is not None:
            raise EstimateCapReached(role, stop, failures)
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
            text, session = _retry_input(role, prompt, failures, resume)
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
                    owner_task=owner_task,
                    check=check,
                    resume=session,
                    max_budget_usd=max_budget_usd,
                    presubmit=presubmit,
                    guard=guard,
                    forbidden=forbidden,
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
                    "denials": exc.denials,
                    "turn_notice": exc.turn_notice,
                    "turn_notice_firings": exc.turn_notice_firings,
                    "deliveries": exc.deliveries,
                    "presubmit_denials": exc.presubmit_denials,
                    "main_thread_turns": exc.main_thread_turns,
                    **({"reaped": exc.reaped} if exc.reaped else {}),
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


class GuardChain:
    """Chain of Responsibility over two `SchemaGuard`s that share the delivery hook's one
    guard seat: asked in order, the first denial wins and the second is not asked.
    `refusals` is the sum of the two, each keeping its own cap."""

    def __init__(self, first: SchemaGuard, second: SchemaGuard):
        self.first = first
        self.second = second

    @property
    def refusals(self) -> int:
        return self.first.refusals + self.second.refusals

    def __call__(self, tool_input: dict[str, Any]) -> str | None:
        return self.first(tool_input) or self.second(tool_input)


def _chain(first: SchemaGuard | None, second: SchemaGuard | None) -> SchemaGuard | None:
    if first is None or second is None:
        return first or second
    return GuardChain(first, second)


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
    role: Role, prompt: str, failures: list[Failure], resume: str | None
) -> tuple[str, str | None]:
    """What the next attempt gets: (prompt, session to resume).

    A failed attempt that left a session is continued with the error as the next
    message and nothing else, since the prompt is already in there: the model keeps
    what it read and did, and a Developer cut while delivering just delivers
    (ADR-0004, addendum). One that left none, because the harness broke before
    opening it, starts over: the caller's session if any, the error appended to the
    prompt.

    A cut for `reason == "max_turns"` swaps that error for
    `rules.turn_cut_retry_message(role)` (ADR-0004, seventh addendum): the harness
    stopped the attempt on the turn cap alone, so the next attempt is told that,
    never ordered to "fix" work that did not fail. Every other reason keeps today's
    message, byte for byte."""
    if not failures:
        return prompt, resume
    last = failures[-1]
    if last.get("reason") == "max_turns":
        error = rules.turn_cut_retry_message(role)
    else:
        error = (
            f"<PreviousAttemptFailed>\n{last['error']}\n</PreviousAttemptFailed>\n\n"
            "Retry. Fix exactly what failed."
        )
    if left := last["session_id"]:
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
    owner_task: str,
    check: Check | None,
    resume: str | None,
    max_budget_usd: float | None,
    presubmit: Presubmit | None = None,
    guard: SchemaGuard | None = None,
    forbidden: Sequence[Forbidden] | None = None,
) -> Output:
    result = await runner.run_once(
        role,
        prompt,
        system_prompt=system_prompt,
        model=model,
        json_schema=schemas.json_schema(role.schema, vocabulary),
        cwd=cwd,
        owner_task=owner_task,
        resume=resume,
        max_budget_usd=max_budget_usd,
        presubmit=presubmit,
        guard=guard,
        forbidden=forbidden,
    )
    cost, session = result.cost_usd, result.session_id
    if result.structured_output is None:
        raise AttemptFailed(
            "structured output missing; it is required",
            cost,
            session,
            reason="schema",
            denials=result.denials,
            turn_notice=result.turn_notice,
            turn_notice_firings=result.turn_notice_firings,
            deliveries=result.deliveries,
            presubmit_denials=result.presubmit_denials,
            main_thread_turns=result.main_thread_turns,
            reaped=result.reaped,
        )
    try:
        data = schemas.validate(role.schema, result.structured_output, vocabulary)
    except ValueError as exc:
        raise AttemptFailed(
            f"structured output invalid: {rules.exception_motive(exc)}",
            cost,
            session,
            reason="schema",
            denials=result.denials,
            turn_notice=result.turn_notice,
            turn_notice_firings=result.turn_notice_firings,
            deliveries=result.deliveries,
            presubmit_denials=result.presubmit_denials,
            main_thread_turns=result.main_thread_turns,
            reaped=result.reaped,
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
                denials=result.denials,
                turn_notice=result.turn_notice,
                turn_notice_firings=result.turn_notice_firings,
                deliveries=result.deliveries,
                presubmit_denials=result.presubmit_denials,
                main_thread_turns=result.main_thread_turns,
                reaped=result.reaped,
            ) from exc
    text = data.pop("markdown").strip()
    if not text:
        raise AttemptFailed(
            "`markdown` is empty; the Markdown section is required",
            cost,
            session,
            reason="schema",
            denials=result.denials,
            turn_notice=result.turn_notice,
            turn_notice_firings=result.turn_notice_firings,
            deliveries=result.deliveries,
            presubmit_denials=result.presubmit_denials,
            main_thread_turns=result.main_thread_turns,
            reaped=result.reaped,
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
        denials=result.denials,
        turn_notice=result.turn_notice,
        turn_notice_firings=result.turn_notice_firings,
        deliveries=result.deliveries,
        presubmit_denials=result.presubmit_denials,
        main_thread_turns=result.main_thread_turns,
        reaped=result.reaped,
    )


def _subagent_cost_usd(cost_by_model: dict[str, float], model: str) -> float:
    """What of `cost_by_model` a sub-agent spent: every model but the role's own
    (ADR-0013, addendum: every sub-agent runs in the subagent model, not the role's)."""
    return sum(cost for spent_on, cost in cost_by_model.items() if spent_on != model)


def task_cap(cfg: Configuration) -> float:
    """The cap in force: one flat ceiling, the same for any plan size (ADR-0004,
    2026-10-05 addendum). The human raises it with a task budget, never lowers it."""
    return rules.task_cap(cfg.task_max_budget_usd, cfg.task_budget_usd)


def estimate_stop_for(
    state: TaskState, role: Role, spent_usd: float, ports: Ports
) -> EstimateStop | None:
    """Whether the run, having spent `spent_usd`, reached the safety cap of the estimate in
    force, and the escalation if so. The decision is `rules.estimate_cap_reached`'s; the
    history is read only here, once the cap is reached, to price the unfinished subtasks."""
    if not rules.estimate_cap_reached(spent_usd, state):
        return None
    estimate = rules.estimate_in_force(state)
    assert estimate is not None  # the cap is only reached over an estimate
    spent_state: TaskState = {**state, "cost_usd": spent_usd}
    count = len([sid for sid in state.get("subtasks", {}) if sid != INTEGRATION])
    history = cost_history.build(closed_documents(ports))
    proposed = rules.reestimate(spent_state, cost_history.bucket_median(history, count))
    reason = rules.estimate_cap_reached_reason(
        role.name,
        spent_usd,
        rules.estimate_cap(estimate),
        len(rules.unfinished_subtasks(state)),
        proposed,
    )
    return EstimateStop(reason, proposed)


def escalation(exc: Escalating) -> TaskState:
    """The patch of a role that failed twice, or that the task cap stopped: the human
    sees it (README.md, 3), with its escalation class (`rules.escalation`) — `cap` for
    the task cap, `failure` for a role `run_with_retry` gave up on.

    Composed through `rules.logged` (issue #137), `exc.failures` passed along: the one
    call site that ever has a role's own failures to log, so `pause_log`'s eligibility
    for an automatic retake reads the reason each attempt actually failed with."""
    cls: EscalationClass = "failure"
    if isinstance(exc, TaskCapReached):
        cls = "cap"
    elif isinstance(exc, EstimateCapReached):
        cls = "estimate"
    patch: TaskState = {
        "failures": exc.failures,
        "cost_usd": exc.cost_usd,
        "status": WAITING_ON_HUMAN,
        **rules.escalation(cls, str(exc)),
    }
    if isinstance(exc, EstimateCapReached):
        patch["proposed_estimate"] = exc.proposed_usd
    return rules.logged(patch, exc.failures)


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
        "failed_cost_usd": spent(output.failures),
        "denials": output.denials,
        "turn_notice": output.turn_notice,
        "turn_notice_firings": output.turn_notice_firings,
        "deliveries": output.deliveries,
        "presubmit_denials": output.presubmit_denials,
        "main_thread_turns": output.main_thread_turns,
        **({"reaped": output.reaped} if output.reaped else {}),
    }


def _run_denials(output: Output) -> int:
    """The run's total denials (glossary: Negación): the delivering attempt's plus
    every recorded failure's, never the delivering attempt's alone. Attempt 1 can burn
    every denial fighting the permission layer and attempt 2 still deliver clean; a
    per-attempt figure would read that run as if nothing had happened."""
    return output.denials + sum(f.get("denials", 0) for f in output.failures)


def _run_notice_firings(output: Output) -> int:
    """How many times the live notice fired anywhere in the run
    (`domain/turn_notice.py`): the delivering attempt's own count, plus every recorded
    failure's — the same fold `_run_denials` gives denials, so a run cut once despite
    the notice and delivering clean on the next attempt still counts every firing, not
    only the delivering attempt's own."""
    return output.turn_notice_firings + sum(
        f.get("turn_notice_firings", 0) for f in output.failures
    )


def _run_notice_cut(output: Output) -> bool:
    """Whether any recorded failure of this run was the harness cutting the attempt on
    the turn cap: what distinguishes `turn_notice.notice_note`'s two variants — a run
    that entered the close-out window and delivered in the same attempt from one that
    entered it, was cut, and only delivered on a later attempt. Independent of
    `_run_notice_firings`: a role the notice does not cover can still be cut, though
    `_section_text` never asks this question for one, since it gates on firings > 0
    first."""
    return any(f.get("reason") == "max_turns" for f in output.failures)


def _run_deliveries(output: Output) -> int:
    """The run's total calls to the delivery tool (glossary: Emisión de entrega): the
    delivering attempt's plus every recorded failure's, the same fold `_run_denials`
    gives denials — a run that spent an early attempt re-emitting an invalid shape
    (issue #32) still reads as such even once a later attempt delivers on its first
    call."""
    return output.deliveries + sum(f.get("deliveries", 0) for f in output.failures)


def _run_reaped(output: Output) -> list[ReapedProcess]:
    """Every process the run left behind and the reaper stopped: the delivering attempt's
    plus every recorded failure's, the same fold `_run_denials` gives denials."""
    return output.reaped + [p for f in output.failures for p in f.get("reaped", [])]


def _section_text(role: Role, output: Output) -> str:
    """What lands in the role's section: its own delivery, with the system's one-line
    notes in front when the run had any (glossary: Negación; Sección) — the denial note
    first, then the turn-notice note, then the deliveries note (only past one), the
    same seam `develop.cold_fallback_note` uses for a dropped session, so an expensive
    or nearly-cut run is legible without opening logs."""
    notes = []
    if count := _run_denials(output):
        notes.append(rules.denial_note(role, count))
    if firings := _run_notice_firings(output):
        notes.append(turn_notice.notice_note(cut=_run_notice_cut(output), firings=firings))
    if (count := _run_deliveries(output)) > 1:
        notes.append(rules.deliveries_note(count))
    if reaped := _run_reaped(output):
        notes.append(rules.reaped_note(reaped))
    if notes:
        return "\n\n".join([*notes, output.text])
    return output.text


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
    delivery_check: SchemaGuard | None = None,
    forbidden: Sequence[Forbidden] | None = None,
    **placeholders: object,
) -> tuple[Output | None, TaskState]:
    """Run a role and build the state patch. On `RoleFailure`, or when the task cap
    stops the role, the patch is an escalation and the output is `None`. Saving the
    document is the caller's (`finish`).

    `section` is where the delivery goes (the role's name by default); `cwd` where the
    role runs (the product root by default); `resume` continues the role's earlier
    session; `prompt` replaces the context with a shorter message when the session
    already holds it (an interview's later rounds); `presubmit` is asked when it
    delivers. `forbidden` is the product's own declared suppression cage (issue #38),
    threaded through exactly where `presubmit` already travels: `None` by default, and
    only `develop.py`'s Developer call passes one. `delivery_check` is the Developer's
    coverage guard (GH-174), threaded the same way. `cold_prompt` is the full context for
    the one retry that drops `resume` when it turns out stale (`run_with_retry`'s own
    doc): `run_role` never builds one on its own, since only the caller knows what a
    message it did not build should contain (ADR-0004, sixth addendum) — a caller that
    passes `resume` with no `cold_prompt` keeps today's retry, which tries the same
    session again."""
    section = section or role.name
    vocabulary = ports.flags.load()
    loaded = load_prompt(role, cfg, ports, vocabulary, **placeholders)
    patch: TaskState = {
        "current_role": role.name,
        "prompts": {role.slug: loaded.hash},
        **playbook_patch(state, role, loaded),
    }
    # One value for where the role runs, where its knowledge is read from and where its
    # index files are written: they are the same checkout by construction, never two.
    workdir = cwd or cfg.product_dir

    def write_index() -> None:
        # Before every attempt of the role, a resumed one that receives only a delta and
        # a retry included: the index points at files of this checkout as they are now.
        ports.workspace.write_index(workdir, ports.knowledge.index_files(workdir))

    if prompt is None:
        standards, knowledge = preamble(role, ports, workdir)
        prompt = context_for(
            state, role, knowledge, standards, extra_sections, state.get("subtask_id")
        )
    try:
        output = await run_with_retry(
            role,
            prompt,
            system_prompt=loaded.text,
            model=cfg.model_for(role.slug, role.runs_on),
            retries=cfg.role_retries,
            vocabulary=vocabulary,
            cwd=workdir,
            runner=ports.runner,
            owner_task=state["id"],
            check=check,
            resume=resume,
            max_budget_usd=cfg.max_budget_usd,
            # A landing restart carries the cost of the run it replaces (GH-239): the cap sees both.
            spent_usd=state.get("cost_usd", 0.0) + state.get("landing_cost_carried", 0.0),
            task_cap_usd=task_cap(cfg),
            presubmit=presubmit,
            delivery_check=delivery_check,
            forbidden=forbidden,
            limits=ports.limits,
            section=section,
            cold_prompt=cold_prompt,
            before_attempt=write_index if reads_by_index(role) else None,
            estimate_stop=lambda total: estimate_stop_for(state, role, total, ports),
        )
    except Escalating as exc:
        # Never `patch | escalation(exc)` (issue #137): `escalation(exc)` already
        # carries its own `pause_log` (`rules.logged`'s doing), and the dict `|`
        # operator picks one side's value for a shared key instead of the list
        # `pause_log`'s own reducer expects appended -- `patch` itself never has one
        # at this point, but the guard that checks this is structural and does not
        # know that, by design (`tests/core/process/application/test_pause_log_guard.py`).
        return None, {**patch, **escalation(exc)}

    patch |= {
        "sections": rules.sections_for(state, section, _section_text(role, output)),
        "failures": output.failures,
        "cost_usd": output.cost_usd,
        "runs": [run_record(state, role, section, output)],
    }
    return output, patch


def finish(state: TaskState, patch: TaskState, ports: Ports) -> TaskState:
    """Write the document once per role, with the reducers applied, and return the patch."""
    ports.documents.save(apply(state, patch))
    return patch
