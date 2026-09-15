"""`RoleRunner` over the Claude Agent SDK: one attempt of one role, raw.

Authenticates through the Claude Code subscription, not an API key. Anything that
goes wrong in here is an `AttemptFailed` with the cost so far; whether to retry is
the use case's decision (README.md, 3). Validation of the structured output is the
use case's too: the SDK only knows JSON.

What a role may do comes off the `Role` (README.md, 4; ADR-0013): its built-in
tools, the permission rules that cage a role that does not execute, and the plugin
skills it may invoke. A role that executes runs unattended (`bypassPermissions`)
inside its working directory under a money cap, and its `denied` rules still hold
(the SDK evaluates deny rules before the mode); every other role runs in the default
permission mode, where anything outside its rules is denied because nobody is there
to approve it. Skills come from the one plugin pinned as a submodule under
`agentic/plugins/` (ADR-0013); `setting_sources=[]` keeps the user's own plugins,
settings and CLAUDE.md out.

The runner's `env` reaches every role's process on top of the inherited environment:
the system's git identity travels there (ADR-0011, addendum), so a role that commits
signs as the system whatever `user.*` the product's config carries.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

from claude_agent_sdk import ClaudeAgentOptions, ResultError, ResultMessage, query

from ...application.ports import AttemptFailed, RawResult
from ...domain.roles import Role
from ..plugins import PLUGIN, PLUGIN_DIR
from ..telemetry import NoTelemetry, Telemetry
from .printer import Printer
from .schema import inline_refs

SKILL_TOOL = "Skill"


def options_for(
    role: Role,
    *,
    system_prompt: str,
    model: str,
    json_schema: dict[str, Any],
    cwd: Path,
    resume: str | None,
    max_budget_usd: float | None,
    traceparent: str | None,
    partial: bool,
    env: Mapping[str, str],
) -> ClaudeAgentOptions:
    tools, allowed = list(role.tools), list(role.allowed)
    if role.skills and SKILL_TOOL not in tools:
        tools.append(SKILL_TOOL)
    if role.skills and allowed and SKILL_TOOL not in allowed:
        # A caged role must still be allowed to invoke its skills without a prompt.
        allowed.append(SKILL_TOOL)
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        # No built-in tools unless the role declares them.
        tools=tools,
        allowed_tools=allowed,
        disallowed_tools=list(role.denied),
        permission_mode="bypassPermissions" if role.executes else "default",
        skills=[f"{PLUGIN}:{skill}" for skill in role.skills] or None,
        plugins=[{"type": "local", "path": str(PLUGIN_DIR)}] if role.skills else [],
        max_budget_usd=max_budget_usd,
        resume=resume,
        cwd=cwd,
        model=model,
        effort=role.effort,
        max_turns=role.max_turns,
        output_format={"type": "json_schema", "schema": inline_refs(json_schema)},
        setting_sources=[],  # ignore project CLAUDE.md and settings
        # Deltas as they are generated; without an observer nobody would read them.
        include_partial_messages=partial,
        # Claude Code parents its own spans under the role's (SDK mode honours it).
        env={**env, **({"TRACEPARENT": traceparent} if traceparent else {})},
    )


class ClaudeSdkRunner:
    def __init__(
        self,
        verbose: bool = False,
        stream: IO[str] | None = None,
        telemetry: Telemetry = NoTelemetry(),
        env: Mapping[str, str] | None = None,
    ):
        # `verbose` streams each role's generation to `stream` (stderr by default).
        # `env` is added to every role's process: the git identity, for one.
        self.verbose = verbose
        self.stream = stream
        self.telemetry = telemetry
        self.env = dict(env or {})

    async def run_once(
        self,
        role: Role,
        prompt: str,
        *,
        system_prompt: str,
        model: str,
        json_schema: dict[str, Any],
        cwd: Path,
        resume: str | None = None,
        max_budget_usd: float | None = None,
    ) -> RawResult:
        observer = Printer(role.name, self.stream) if self.verbose else None
        options = options_for(
            role,
            system_prompt=system_prompt,
            model=model,
            json_schema=json_schema,
            cwd=cwd,
            resume=resume,
            max_budget_usd=max_budget_usd,
            traceparent=self.telemetry.traceparent(),
            partial=observer is not None,
            env=self.env,
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
        # Each `query` is a new Claude Code process, so the cost is this run's alone,
        # resumed session or not.
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
