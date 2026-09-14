"""`RoleRunner` over the Claude Agent SDK: one attempt of one role, raw.

Authenticates through the Claude Code subscription, not an API key. Anything that
goes wrong in here is an `AttemptFailed` with the cost so far; whether to retry is
the use case's decision (README.md, 3). Validation of the structured output is the
use case's too: the SDK only knows JSON.

Roles have no tools yet: they reason and write Markdown. Once Developer joins,
`tools` gets a real list and a deliberate permission mode.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

from claude_agent_sdk import ClaudeAgentOptions, ResultError, ResultMessage, query

from ...application.ports import AttemptFailed, RawResult
from ...domain.roles import Role
from ..otel import telemetry
from .printer import Printer
from .schema import inline_refs


class ClaudeSdkRunner:
    def __init__(self, verbose: bool = False, stream: IO[str] | None = None):
        # `verbose` streams each role's generation to `stream` (stderr by default).
        self.verbose = verbose
        self.stream = stream

    async def run_once(
        self,
        role: Role,
        prompt: str,
        *,
        system_prompt: str,
        model: str,
        json_schema: dict[str, Any],
        cwd: Path,
        tools: Sequence[str] = (),
    ) -> RawResult:
        observer = Printer(role.name, self.stream) if self.verbose else None
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            # No built-in tools unless asked; `allowed_tools` would only skip the prompt.
            tools=list(tools),
            cwd=cwd,
            model=model,
            effort=role.effort,
            max_turns=role.max_turns,
            output_format={"type": "json_schema", "schema": inline_refs(json_schema)},
            setting_sources=[],  # ignore project CLAUDE.md and settings
            # Deltas as they are generated; without an observer nobody would read them.
            include_partial_messages=observer is not None,
            # Claude Code parents its own spans under the role's (SDK mode honours it).
            env={"TRACEPARENT": parent} if (parent := telemetry.traceparent()) else {},
        )

        exhausted = f"max_turns ({role.max_turns}) exhausted without a result"
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
                raise AttemptFailed(exhausted, cost) from exc
            raise AttemptFailed(
                f"run failed ({exc.subtype}): {'; '.join(exc.errors)}", cost
            ) from exc
        except Exception as exc:  # the SDK raises its own hierarchy; all of it is a failure
            raise AttemptFailed(f"SDK error: {exc}") from exc

        if result is None:
            raise AttemptFailed("run ended without a result message")
        cost = result.total_cost_usd or 0.0
        if result.subtype == "error_max_turns":
            raise AttemptFailed(exhausted, cost)
        if result.is_error:
            raise AttemptFailed(
                f"run ended in error: {'; '.join(result.errors or [result.subtype])}", cost
            )
        return RawResult(
            structured_output=result.structured_output,
            cost_usd=cost,
            turns=result.num_turns,
            session_id=result.session_id,
        )
