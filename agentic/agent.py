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

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, Message, ResultError, ResultMessage, query
from pydantic import ValidationError

from . import observe, schemas
from .roles import Role
from .state import Failure


class RoleFailure(Exception):
    def __init__(self, role: Role, failures: list[Failure]):
        self.role = role
        self.failures = failures
        last = failures[-1]["error"] if failures else "no attempts were made"
        super().__init__(f"{role.name} failed {len(failures)} times: {last}")

    @property
    def cost_usd(self) -> float:
        return sum(f["cost_usd"] for f in self.failures)


class _AttemptFailed(Exception):
    def __init__(self, error: str, cost_usd: float = 0.0):
        super().__init__(error)
        self.cost_usd = cost_usd


@dataclass
class Output:
    text: str
    data: dict
    cost_usd: float = 0.0
    turns: int = 0
    # Claude Code session of the successful attempt: `claude -r <id>` reopens it.
    session_id: str = ""
    model: str = ""
    # Failed attempts before this output. Empty on a clean run. Their cost is
    # already included in `cost_usd`: a paid attempt counts even when it failed.
    failures: list[Failure] = field(default_factory=list)


async def run(
    role: Role,
    prompt: str,
    *,
    system_prompt: str,
    model: str,
    retries: int,
    flags: dict[str, str],
    cwd: Path | None = None,
    check: Callable[[dict], None] | None = None,
    observer: Callable[[Message], None] | None = None,
) -> Output:
    """`check` is the node's own rule over the validated output (raise `ValueError`);
    breaking it is a role failure like any other, so the model gets the error back.
    `observer` sees every message of the stream, partial ones included."""
    failures: list[Failure] = []
    for attempt in range(1, max(retries, 0) + 2):
        try:
            output = await _once(
                role,
                _with_error(prompt, failures),
                system_prompt,
                model,
                flags,
                cwd,
                check,
                observer,
            )
        except _AttemptFailed as exc:
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


async def _once(
    role: Role,
    prompt: str,
    system_prompt: str,
    model: str,
    flags: dict[str, str],
    cwd: Path | None,
    check: Callable[[dict], None] | None = None,
    observer: Callable[[Message], None] | None = None,
) -> Output:
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        tools=[],  # no built-in tools at all; `allowed_tools` would only skip the permission prompt
        cwd=cwd,
        model=model,
        effort=role.effort,
        max_turns=role.max_turns,
        output_format={"type": "json_schema", "schema": schemas.json_schema(role.schema, flags)},
        setting_sources=[],  # ignore project CLAUDE.md and settings
        # Deltas as they are generated; without an observer nobody would read them.
        include_partial_messages=observer is not None,
        # Claude Code parents its own spans under the role's (SDK mode honours it).
        env={"TRACEPARENT": parent} if (parent := observe.traceparent()) else {},
    )

    result: ResultMessage | None = None
    try:
        async for message in query(prompt=prompt, options=options):
            if observer is not None:
                observer(message)
            if isinstance(message, ResultMessage):
                result = message
    except ResultError as exc:
        # The CLI reports the failed result, then exits non-zero; the SDK turns
        # that into ResultError after yielding the ResultMessage.
        cost = (result.total_cost_usd or 0.0) if result else 0.0
        if exc.subtype == "error_max_turns":
            raise _AttemptFailed(
                f"max_turns ({role.max_turns}) exhausted without a result", cost
            ) from exc
        raise _AttemptFailed(f"run failed ({exc.subtype}): {'; '.join(exc.errors)}", cost) from exc
    except Exception as exc:  # the SDK raises its own hierarchy; all of it is a role failure
        raise _AttemptFailed(f"SDK error: {exc}") from exc

    if result is None:
        raise _AttemptFailed("run ended without a result message")
    cost = result.total_cost_usd or 0.0
    if result.subtype == "error_max_turns":
        raise _AttemptFailed(f"max_turns ({role.max_turns}) exhausted without a result", cost)
    if result.is_error:
        raise _AttemptFailed(
            f"run ended in error: {'; '.join(result.errors or [result.subtype])}", cost
        )

    if result.structured_output is None:
        raise _AttemptFailed("structured output missing; it is required", cost)
    try:
        data = schemas.validate(role.schema, result.structured_output, flags)
        if check is not None:
            check(data)
    except (ValidationError, ValueError) as exc:
        raise _AttemptFailed(f"structured output invalid: {exc}", cost) from exc
    text = data.pop("markdown").strip()
    if not text:
        raise _AttemptFailed("`markdown` is empty; the Markdown section is required", cost)

    return Output(
        text=text,
        data=data,
        cost_usd=cost,
        turns=result.num_turns,
        session_id=result.session_id,
        model=model,
    )
