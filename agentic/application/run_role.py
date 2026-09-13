"""Running one role inside a task: context, prompt, attempt, retry, delivery.

A role failure is one of four things (README.md, 3 and 14): the harness broke, the
model ran out of turns, its structured output is missing or invalid, or its
`markdown` is empty. `run_with_retry` retries once with the error appended to the
prompt; a second failure raises `RoleFailure`, which the use case turns into an
escalation. The graph engine's own retry was rejected because it reruns the node
without telling the model what went wrong.

Context rule (README.md, 6): each role reads, in full, the sections it declares in
`Role.reads`. Not the previous role's handoff: a validator that only sees a summary
returns over gaps that were never there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..domain import document, flags, rules, schemas
from ..domain.configuration import Configuration
from ..domain.roles import ORIGINAL, Prompt, Role, render
from ..domain.task import WAITING_ON_HUMAN, Failure, TaskState, apply
from .ports import AttemptFailed, Ports, RoleRunner

Check = Callable[[dict], None]


class RoleFailure(Exception):
    def __init__(self, role: Role, failures: list[Failure]):
        self.role = role
        self.failures = failures
        last = failures[-1]["error"] if failures else "no attempts were made"
        super().__init__(f"{role.name} failed {len(failures)} times: {last}")

    @property
    def cost_usd(self) -> float:
        return sum(f["cost_usd"] for f in self.failures)


@dataclass
class Output:
    text: str
    data: dict
    cost_usd: float = 0.0
    turns: int = 0
    session_id: str = ""
    model: str = ""
    # Failed attempts before this output. Empty on a clean run. Their cost is
    # already included in `cost_usd`: a paid attempt counts even when it failed.
    failures: list[Failure] = field(default_factory=list)


def context_for(state: TaskState, role: Role, product_knowledge: str = "") -> str:
    """The role's input: the product knowledge if the role reads it, then the sections
    it declares, in document order, that exist."""
    sections = state.get("sections", {})
    parts = [product_knowledge] if product_knowledge else []
    for name in role.reads:
        if name == ORIGINAL:
            request = document.demote(state["description"])
            parts.append(f"## {ORIGINAL}\n\n{state['title']}\n\n{request}")
        elif name in sections:
            parts.append(f"## {name}\n\n{sections[name]}")
    return "\n\n".join(parts)


def load_prompt(
    role: Role, cfg: Configuration, ports: Ports, vocabulary: dict[str, str], **placeholders: object
) -> Prompt:
    """The role's system prompt, overlay applied, with the limits and the vocabulary in."""
    raw, source = ports.prompts.raw(role)
    return render(
        raw,
        source,
        max_returns=cfg.max_returns_per_pair,
        flags=flags.describe(vocabulary),
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
) -> Output:
    """`check` is the use case's own rule over the validated output (raise `ValueError`);
    breaking it is a role failure like any other, so the model gets the error back."""
    failures: list[Failure] = []
    for attempt in range(1, max(retries, 0) + 2):
        try:
            output = await _attempt(
                role,
                _with_error(prompt, failures),
                system_prompt,
                model,
                vocabulary,
                cwd,
                runner,
                check,
            )
        except AttemptFailed as exc:
            failures.append(
                {"role": role.name, "attempt": attempt, "error": str(exc), "cost_usd": exc.cost_usd}
            )
            continue
        output.failures = failures
        output.cost_usd += sum(f["cost_usd"] for f in failures)
        return output
    raise RoleFailure(role, failures)


def _with_error(prompt: str, failures: list[Failure]) -> str:
    if not failures:
        return prompt
    return (
        f"{prompt}\n\n<PreviousAttemptFailed>\n{failures[-1]['error']}\n"
        "</PreviousAttemptFailed>\n\nRetry. Fix exactly what failed."
    )


async def _attempt(
    role: Role,
    prompt: str,
    system_prompt: str,
    model: str,
    vocabulary: dict[str, str],
    cwd: Path,
    runner: RoleRunner,
    check: Check | None,
) -> Output:
    result = await runner.run_once(
        role,
        prompt,
        system_prompt=system_prompt,
        model=model,
        json_schema=schemas.json_schema(role.schema, vocabulary),
        cwd=cwd,
    )
    cost = result.cost_usd
    if result.structured_output is None:
        raise AttemptFailed("structured output missing; it is required", cost)
    try:
        data = schemas.validate(role.schema, result.structured_output, vocabulary)
        if check is not None:
            check(data)
    except ValueError as exc:
        raise AttemptFailed(f"structured output invalid: {exc}", cost) from exc
    text = data.pop("markdown").strip()
    if not text:
        raise AttemptFailed("`markdown` is empty; the Markdown section is required", cost)
    return Output(
        text=text,
        data=data,
        cost_usd=cost,
        turns=result.turns,
        session_id=result.session_id,
        model=model,
    )


async def run_role(
    state: TaskState,
    role: Role,
    cfg: Configuration,
    ports: Ports,
    check: Check | None = None,
    **placeholders: object,
) -> tuple[Output | None, TaskState]:
    """Run a role and build the state patch. On `RoleFailure` the patch is an escalation
    and the output is `None`. Saving the document is the caller's (`finish`)."""
    product_knowledge = ports.knowledge.read() if role.reads_knowledge else ""
    vocabulary = ports.flags.load()
    loaded = load_prompt(role, cfg, ports, vocabulary, **placeholders)
    patch: TaskState = {"current_role": role.name, "prompts": {role.slug: loaded.hash}}
    try:
        output = await run_with_retry(
            role,
            context_for(state, role, product_knowledge),
            system_prompt=loaded.text,
            model=cfg.model_for(role.slug, role.kind),
            retries=cfg.role_retries,
            vocabulary=vocabulary,
            cwd=cfg.product_dir,
            runner=ports.runner,
            check=check,
        )
    except RoleFailure as exc:
        patch |= {
            "failures": exc.failures,
            "cost_usd": exc.cost_usd,
            "escalated": str(exc),
            "status": WAITING_ON_HUMAN,
        }
        return None, patch

    patch |= {
        "sections": rules.sections_for(state, role.name, output.text),
        "failures": output.failures,
        "cost_usd": output.cost_usd,
        "runs": [
            {
                "role": role.name,
                "version": rules.next_version(state, role.name),
                "session_id": output.session_id,
                "model": output.model,
                "cost_usd": output.cost_usd,
                "turns": output.turns,
            }
        ],
    }
    return output, patch


def finish(state: TaskState, patch: TaskState, ports: Ports) -> TaskState:
    """Write the document once per role, with the reducers applied, and return the patch."""
    ports.documents.save(apply(state, patch))
    return patch
