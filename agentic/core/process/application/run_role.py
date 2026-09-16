"""Running one role inside a task: context, prompt, attempt, retry, delivery.

A role failure is one of four things (README.md, 3 and 14): the harness broke, the
model ran out of turns, its structured output is missing or invalid, or its
`markdown` is empty. `run_with_retry` retries once with the error as input: inside the
session the attempt left when there is one, appended to the prompt of a fresh session
when there is none (ADR-0004, addendum). A second failure raises `RoleFailure`, which
the use case turns into an escalation. The graph engine's own retry was rejected
because it reruns the node without telling the model what went wrong.

The task cap is checked here too, before every attempt, because every role passes
through here: the routing between nodes would miss the reviews inside one node and
the rounds of an interview. Reached, `TaskCapReached` escalates the same way, but it
is not a role failure: the role did not break (ADR-0004, third addendum). The cap is
read off the state each time, so it grows as soon as the plan is in (fourth addendum).

Context rule (README.md, 6): each role reads, in full, the sections it declares in
`Role.reads`, after the engineering standards and the product knowledge if it reads
them. Not the previous role's handoff: a validator that only sees a summary returns
over gaps that were never there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain import document, rules, schemas
from ..domain.document import HUMAN
from ..domain.roles import ORIGINAL, Prompt, Role, describe_flags, render
from ..domain.task import WAITING_ON_HUMAN, Failure, RoleRun, TaskState, apply
from .ports import AttemptFailed, DeliveryGate, Ports, RoleRunner

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
    # Failed attempts before this output. Empty on a clean run. Their cost is
    # already included in `cost_usd`: a paid attempt counts even when it failed.
    failures: list[Failure] = field(default_factory=list)


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


def load_prompt(
    role: Role, cfg: Configuration, ports: Ports, vocabulary: dict[str, str], **placeholders: object
) -> Prompt:
    """The role's system prompt, overlay applied, with the limits and the vocabulary in."""
    raw, source = ports.prompts.raw(role)
    return render(
        raw,
        source,
        max_returns=cfg.max_returns_per_pair,
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
    gate: DeliveryGate | None = None,
) -> Output:
    """`check` is the use case's own rule over the validated output (raise `ValueError`);
    breaking it is a role failure like any other, so the model gets the error back.

    `spent_usd` is what the task spent before this call; with `task_cap_usd`
    set, an attempt that would start at or over the cap raises `TaskCapReached`
    instead. A failed attempt counts: the retry can cost another attempt cap.

    `gate` is handed to every attempt: the harness asks it when the model delivers."""
    failures: list[Failure] = []
    for attempt in range(1, max(retries, 0) + 2):
        total = spent_usd + spent(failures)
        if task_cap_usd is not None and total >= task_cap_usd:
            raise TaskCapReached(role, total, task_cap_usd, failures)
        text, session = _retry_input(prompt, failures, resume)
        try:
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
                gate=gate,
            )
        except AttemptFailed as exc:
            failures.append(
                {
                    "role": role.name,
                    "attempt": attempt,
                    "error": str(exc),
                    "cost_usd": exc.cost_usd,
                    "session_id": exc.session_id,
                }
            )
            continue
        output.failures = failures
        output.cost_usd += spent(failures)
        return output
    raise RoleFailure(role, failures)


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
    gate: DeliveryGate | None = None,
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
        gate=gate,
    )
    cost, session = result.cost_usd, result.session_id
    if result.structured_output is None:
        raise AttemptFailed("structured output missing; it is required", cost, session)
    try:
        data = schemas.validate(role.schema, result.structured_output, vocabulary)
        if check is not None:
            check(data)
    except ValueError as exc:
        raise AttemptFailed(f"structured output invalid: {exc}", cost, session) from exc
    text = data.pop("markdown").strip()
    if not text:
        raise AttemptFailed("`markdown` is empty; the Markdown section is required", cost, session)
    return Output(
        text=text,
        data=data,
        cost_usd=cost,
        turns=result.turns,
        session_id=result.session_id,
        model=model,
        cost_by_model=result.cost_by_model,
    )


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
    sees it (README.md, 3)."""
    return {
        "failures": exc.failures,
        "cost_usd": exc.cost_usd,
        "escalated": str(exc),
        "status": WAITING_ON_HUMAN,
    }


def run_record(state: TaskState, role: Role, section: str, output: Output) -> RoleRun:
    return {
        "role": role.name,
        "section": section,
        "version": rules.next_version(state, section),
        "session_id": output.session_id,
        "model": output.model,
        "cost_usd": output.cost_usd,
        "cost_by_model": output.cost_by_model,
        "turns": output.turns,
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
    gate: DeliveryGate | None = None,
    **placeholders: object,
) -> tuple[Output | None, TaskState]:
    """Run a role and build the state patch. On `RoleFailure`, or when the task cap
    stops the role, the patch is an escalation and the output is `None`. Saving the
    document is the caller's (`finish`).

    `section` is where the delivery goes (the role's name by default); `cwd` where the
    role runs (the product root by default); `resume` continues the role's earlier
    session; `prompt` replaces the context with a shorter message when the session
    already holds it (an interview's later rounds); `gate` is asked when it delivers."""
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
            gate=gate,
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
