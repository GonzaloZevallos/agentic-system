"""Harness over the Claude Agent SDK: run one role once, retrying once on failure.

Authenticates through the Claude Code subscription, not an API key.

A role failure is one of four things (DISENO.md, 3 and 14): an SDK exception,
`max_turns` exhausted without a result, a missing or invalid structured output,
or an empty `markdown` field. `run` retries once with the error appended to the prompt; a
second failure raises `RoleFailure`, which the node turns into an escalation.
LangGraph's `RetryPolicy` was rejected because it reruns the node without
telling the model what went wrong.

Roles have no tools yet: they reason and write Markdown. Once Developer joins,
it will need tools and a deliberate permission mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from pydantic import ValidationError

from . import schemas
from .roles import Role
from .state import Failure


class RoleFailure(Exception):
    def __init__(self, role: Role, failures: list[Failure]):
        self.role = role
        self.failures = failures
        super().__init__(f"{role.name} failed {len(failures)} times: {failures[-1]['error']}")


class _AttemptFailed(Exception):
    pass


@dataclass
class Output:
    text: str
    data: dict
    cost_usd: float = 0.0
    turns: int = 0
    # Failed attempts before this output. Empty on a clean run.
    failures: list[Failure] = field(default_factory=list)


async def run(
    role: Role,
    prompt: str,
    *,
    system_prompt: str,
    model: str,
    retries: int,
    flags: dict[str, str],
) -> Output:
    failures: list[Failure] = []
    for attempt in range(1, retries + 2):
        try:
            output = await _once(role, _with_error(prompt, failures), system_prompt, model, flags)
        except _AttemptFailed as exc:
            failures.append({"role": role.name, "attempt": attempt, "error": str(exc)})
            continue
        output.failures = failures
        return output
    raise RoleFailure(role, failures)


def _with_error(prompt: str, failures: list[Failure]) -> str:
    if not failures:
        return prompt
    return (
        f"{prompt}\n\n<PreviousAttemptFailed>\n{failures[-1]['error']}\n"
        "</PreviousAttemptFailed>\n\nRetry. Fix exactly what failed."
    )


async def _once(
    role: Role, prompt: str, system_prompt: str, model: str, flags: dict[str, str]
) -> Output:
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=[],
        model=model,
        effort=role.effort,
        max_turns=role.max_turns,
        output_format={"type": "json_schema", "schema": schemas.json_schema(role.schema, flags)},
        setting_sources=[],  # ignore project CLAUDE.md and settings
    )

    result: ResultMessage | None = None
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                result = message
    except Exception as exc:  # the SDK raises its own hierarchy; all of it is a role failure
        raise _AttemptFailed(f"SDK error: {exc}") from exc

    if result is None:
        raise _AttemptFailed("run ended without a result message")
    if result.is_error:
        raise _AttemptFailed(f"run ended in error: {'; '.join(result.errors or [result.subtype])}")
    if result.subtype == "error_max_turns" or result.terminal_reason == "max_turns":
        raise _AttemptFailed(f"max_turns ({role.max_turns}) exhausted without a result")

    if result.structured_output is None:
        raise _AttemptFailed("structured output missing; it is required")
    try:
        data = schemas.validate(role.schema, result.structured_output, flags)
    except (ValidationError, ValueError) as exc:
        raise _AttemptFailed(f"structured output invalid: {exc}") from exc
    text = data.pop("markdown").strip()
    if not text:
        raise _AttemptFailed("`markdown` is empty; the Markdown section is required")

    return Output(
        text=text,
        data=data,
        cost_usd=result.total_cost_usd or 0.0,
        turns=result.num_turns,
    )
