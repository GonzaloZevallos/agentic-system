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
to approve it. A role that executes also runs inside the hooks' cage, which sees
the parsed call where a deny rule only sees a prefix (`hooks.py`; ADR-0011, addendum).
Skills come from the one plugin pinned as a submodule under
`agentic/plugins/` (ADR-0013); `setting_sources=[]` keeps the user's own plugins,
settings and CLAUDE.md out. It does not keep their MCP servers out: those come from
the user's Claude Code config, not from settings, and `strict_mcp_config=True` is what
stops the CLI from loading them. No role declares an MCP tool, and inherited ones cost
~233k tokens of tool definitions reread on every call, 67% of a run (issue #18).

The runner's `env` reaches every role's process on top of the inherited environment:
the system's git identity travels there (ADR-0011, addendum), so a role that commits
signs as the system whatever `user.*` the product's config carries.

A run ends at its first delivery. A sub-agent the role left running in the background
reopens the session when it returns, and the model delivers again: one `ResultMessage`
per notification, turns and money on repeats of a verdict already taken (issue #16).
Leaving the stream at the first result that carries a structured output closes the SDK
generator, which ends the process and whatever it still had running.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any

from claude_agent_sdk import (
    AssistantMessage,
    CanUseTool,
    ClaudeAgentOptions,
    Message,
    PermissionResultDeny,
    ResultError,
    ResultMessage,
    ToolPermissionContext,
    ToolUseBlock,
    query,
)

from agentic.shared.environment import trace_id_of, with_resource_marker

from ...application.ports import AttemptFailed, Presubmit, RawResult
from ...domain.denials import denial_for
from ...domain.roles import Role
from ...domain.rules import ReportedFailure, exception_motive, failure_motive, max_turns_motive
from ...domain.task import FailureReason
from ..plugins import PLUGIN, PLUGIN_DIR
from ..telemetry import NoTelemetry, Telemetry
from .hooks import hooks_for
from .printer import Printer
from .schema import inline_refs

SKILL_TOOL = "Skill"
AGENT_TOOL = "Agent"
OTHER_TOOL = "other"
# The closed set `agentic.role.tool_calls` groups by; anything else is `OTHER_TOOL`
# (the module docstring's MCP tools included, since no role declares one).
KNOWN_TOOLS = frozenset(
    {"Bash", "Read", "Edit", "Write", "Grep", "Glob", AGENT_TOOL, SKILL_TOOL, "StructuredOutput"}
)


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
    presubmit: Presubmit | None = None,
    on_denied: Callable[[], None] | None = None,
    on_notice: Callable[[], None] | None = None,
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
        mcp_servers={},  # and only these: none (see the module docstring)
        strict_mcp_config=True,
        # Deltas as they are generated; without an observer nobody would read them.
        include_partial_messages=partial,
        # `TRACEPARENT` alone already parents Claude Code's own spans, and any
        # sub-agent it launches, under the chain it points to (ADR-0020's spike, run
        # against the pinned SDK and CLI: identical parenting with or without the
        # `OTEL_RESOURCE_ATTRIBUTES` marker below). The marker does not propagate
        # anything; it identifies whose run those spans belong to (GH-70).
        env=_child_env(env, role, traceparent),
        hooks=hooks_for(role, cwd, presubmit, on_notice) or None,
        # A role that executes runs under `bypassPermissions`, which already shadows
        # this callback for every call (the SDK's own warning); wiring one there would
        # be dead code the hooks cage already replaces (ADR-0011, addendum). For a role
        # that does not execute, this is the only seam a call outside `Role.allowed`
        # reaches (`hooks_for` installs nothing for it): the callback never widens what
        # `Role.allowed` permits, since the SDK only consults it for what an allow rule
        # does not already cover.
        can_use_tool=None if role.executes else _deny_writes(role, on_denied),
    )


def _child_env(env: Mapping[str, str], role: Role, traceparent: str | None) -> dict[str, str]:
    """`env` with `TRACEPARENT` set (as today) and, when `traceparent` carries a valid
    trace id, `OTEL_RESOURCE_ATTRIBUTES` refreshed with this run's marker
    (`with_resource_marker`, GH-70): every inherited key-value survives (invariant 8),
    and nothing is added when there is no trace to mark against, so a role run under
    `NoTelemetry` sees exactly the `env` it was given, as before this change."""
    child = {**env, **({"TRACEPARENT": traceparent} if traceparent else {})}
    trace_id = trace_id_of(traceparent)
    if trace_id is not None:
        child["OTEL_RESOURCE_ATTRIBUTES"] = with_resource_marker(
            env.get("OTEL_RESOURCE_ATTRIBUTES"), role_slug=role.slug, trace_id=trace_id
        )
    return child


def _deny_writes(role: Role, on_denied: Callable[[], None] | None) -> CanUseTool:
    """The `can_use_tool` callback for a role that does not execute (glossary:
    Negación): always denies, with the sentence `denial_for` composes instead of the
    SDK's generic reason, and counts the denial for `RawResult.denials`. Safe to always
    deny because the SDK only asks this for a call `Role.allowed` does not already
    cover (its own shadowing rule), so this never makes the role's permitted commands
    narrower than its `Role.allowed`."""

    async def deny(
        tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultDeny:
        if on_denied is not None:
            on_denied()
        return PermissionResultDeny(message=denial_for(role))

    return deny


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
        presubmit: Presubmit | None = None,
    ) -> RawResult:
        observer = Printer(role.name, self.stream) if self.verbose else None
        denials = 0
        notice_fired = False

        def _count_denial() -> None:
            nonlocal denials
            denials += 1

        def _mark_notice() -> None:
            nonlocal notice_fired
            notice_fired = True

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
            presubmit=presubmit,
            on_denied=_count_denial,
            on_notice=_mark_notice,
        )

        result: ResultMessage | None = None
        background = 0
        calls = 0
        tool_calls: dict[str, int] = {}
        try:
            async for message in query(prompt=prompt, options=options):
                if observer is not None:
                    observer(message)
                if isinstance(message, AssistantMessage):
                    calls += 1
                    for name in _tool_names(message):
                        tool_calls[name] = tool_calls.get(name, 0) + 1
                background += _background_agents(message)
                if isinstance(message, ResultMessage):
                    result = message
                    if _delivered(message):
                        break  # the first delivery is the verdict (module docstring)
        except ResultError as exc:
            # The CLI reports the failed result, then exits non-zero; the SDK turns
            # that into ResultError after yielding the ResultMessage.
            cost, session = _left_behind(result)
            if exc.subtype == "error_max_turns":
                raise AttemptFailed(
                    max_turns_motive(role.max_turns, _reported_error(exc)),
                    cost,
                    session,
                    reason="max_turns",
                    denials=denials,
                    turn_notice=notice_fired,
                ) from exc
            raise AttemptFailed(
                failure_motive(_reported_error(exc)),
                cost,
                session,
                reason=_reason_for_subtype(exc.subtype),
                denials=denials,
                turn_notice=notice_fired,
            ) from exc
        except Exception as exc:  # the SDK raises its own hierarchy; all of it is a failure
            raise AttemptFailed(
                f"SDK error: {exception_motive(exc)}",
                reason="sdk",
                denials=denials,
                turn_notice=notice_fired,
            ) from exc

        if result is None:
            raise AttemptFailed(
                "run ended without a result message",
                reason="sdk",
                denials=denials,
                turn_notice=notice_fired,
            )
        if background and _delivered(result):
            # The prompt forbids it; when the model does it anyway, whatever those
            # sub-agents had to say never reached the verdict, and the reader of the
            # log should know why the cost by model looks the way it does.
            self._warn(
                f"[{role.name}] delivered with {background} sub-agent(s) launched in "
                "the background; the session ended at the delivery, without them\n"
            )
        cost, session = _left_behind(result)
        if result.subtype == "error_max_turns":
            raise AttemptFailed(
                max_turns_motive(role.max_turns, _reported_result(result)),
                cost,
                session,
                reason="max_turns",
                denials=denials,
                turn_notice=notice_fired,
            )
        if result.is_error:
            raise AttemptFailed(
                failure_motive(_reported_result(result)),
                cost,
                session,
                reason=_reason_for_subtype(result.subtype or ""),
                denials=denials,
                turn_notice=notice_fired,
            )
        return RawResult(
            structured_output=result.structured_output,
            cost_usd=cost,
            turns=result.num_turns,
            session_id=session,
            denials=denials,
            turn_notice=notice_fired,
            cost_by_model=_cost_by_model(result),
            usage=_usage(result),
            calls=calls,
            tool_calls=tool_calls,
        )

    def _warn(self, text: str) -> None:
        stream = self.stream or sys.stderr
        stream.write(text)
        stream.flush()


def _reason_for_subtype(subtype: str) -> FailureReason:
    """`agentic.role.failures`'s reason for a harness failure that is not `max_turns`
    (its own raise site already knows that one): `limit` for a per-attempt money cap
    (issue #66), `sdk` for anything else the harness reports by a subtype."""
    return "limit" if "budget" in subtype else "sdk"


def _reported_error(exc: ResultError) -> ReportedFailure:
    """`exc`'s payload as `failure_motive` reads it: every field the CLI reported on
    the failed result before exiting non-zero, `exit_code` included — the shape only
    `ResultError` structurally carries (`ProcessError`'s own attribute)."""
    return ReportedFailure(
        subtype=exc.subtype,
        errors=tuple(exc.errors),
        result=exc.result,
        api_error_status=exc.api_error_status,
        terminal_reason=exc.terminal_reason,
        exit_code=exc.exit_code,
    )


def _reported_result(result: ResultMessage) -> ReportedFailure:
    """`result`'s payload as `failure_motive` reads it, for the error `ResultMessage`
    site: the module docstring's `run failed (success): ` shape, reached without the
    CLI ever raising `ResultError`. No `exit_code`: a `ResultMessage` structurally has
    none, so `failure_motive` never formats one that is absent."""
    return ReportedFailure(
        subtype=result.subtype,
        errors=tuple(result.errors or ()),
        result=result.result,
        api_error_status=result.api_error_status,
        terminal_reason=result.terminal_reason,
    )


def _delivered(result: ResultMessage) -> bool:
    """A result that carries the role's delivery. Whether that delivery is valid is the
    use case's call; here it only says the session has nothing more to give."""
    return not result.is_error and result.structured_output is not None


def _background_agents(message: Message) -> int:
    """How many sub-agents this message launched with `run_in_background`."""
    if not isinstance(message, AssistantMessage):
        return 0
    return sum(
        1
        for block in message.content
        if isinstance(block, ToolUseBlock)
        and block.name == AGENT_TOOL
        and bool(block.input.get("run_in_background"))
    )


def _tool_names(message: AssistantMessage) -> list[str]:
    """The tool this message called, once per `ToolUseBlock`, normalised to
    `KNOWN_TOOLS` (anything else, an MCP tool included, is `OTHER_TOOL`)."""
    return [
        block.name if block.name in KNOWN_TOOLS else OTHER_TOOL
        for block in message.content
        if isinstance(block, ToolUseBlock)
    ]


def _usage(result: ResultMessage | None) -> dict[str, int]:
    """The delivering result's token usage, normalised to the closed `kind` set
    `agentic.role.tokens` groups by. `RawResult.usage`'s own docstring has the
    evidence for why this is a direct read, never a subtraction against an earlier
    call. Zeroed when the SDK reports none."""
    raw = (result.usage if result else None) or {}
    return {
        "input": raw.get("input_tokens", 0),
        "output": raw.get("output_tokens", 0),
        "cache_read": raw.get("cache_read_input_tokens", 0),
        "cache_write": raw.get("cache_creation_input_tokens", 0),
    }


def _cost_by_model(result: ResultMessage) -> dict[str, float]:
    """The total's split by model. `total_cost_usd` counts the sub-agents a role spawned
    and `num_turns` does not, so this is what says where a two-turn run's money went."""
    usage = result.model_usage or {}
    return {model: float(entry.get("costUSD", 0.0)) for model, entry in usage.items()}


def _left_behind(result: ResultMessage | None) -> tuple[float, str]:
    """(cost, session) of a run as it ended. Each `query` is a new Claude Code process,
    so the cost is this run's alone, resumed session or not. A failed result still
    names its session: the money cap or the turns cut the run, but what the model did
    is in there to resume."""
    if result is None:
        return 0.0, ""
    return result.total_cost_usd or 0.0, result.session_id
