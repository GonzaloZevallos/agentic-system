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
stops the CLI from loading them. Only the Developer and its integrator declare an MCP
tool, `run_check` (ADR-0065; `check_server.py`), in-process and handed over by `develop`;
every other role keeps `mcp_servers={}`. Inherited ones cost ~233k tokens of tool
definitions reread on every call, 67% of a run (issue #18). The attempt that carries the
tool also carries `MCP_TOOL_TIMEOUT` in its own `options.env`, so a call as long as a suite
is not cut by the CLI's default.

The runner's `env` reaches every role's process on top of the inherited environment:
the system's git identity travels there (ADR-0011, addendum), so a role that commits
signs as the system whatever `user.*` the product's config carries.

A run ends at its first delivery. A sub-agent the role left running in the background
reopens the session when it returns, and the model delivers again: one `ResultMessage`
per notification, turns and money on repeats of a verdict already taken (issue #16).
Leaving the stream at the first result that carries a structured output closes the SDK
generator, which ends the process and whatever it still had running.

Whatever a role started in the background (`nohup`, `setsid`, `run_in_background`) left
the process group, and the closing of the stream does not reach it (GH-282, ADR-0052).
Every attempt therefore carries one environment variable that every child inherits, and
once the stream is closed the reaper stops every process that carries it. The variable
goes only into the `options.env` of that attempt, never into `self.env` or `os.environ`,
which every other attempt and the system's own children share.

A CLI that hangs holds its slot forever: the stream loop has no clock of its own. A
watchdog beside the loop therefore asks `domain/stall.py` every `CHECK_EVERY_S` whether
the attempt is stalled, and when it is, reaps the attempt by its mark before anything is
cancelled; only a CLI that survives the reap has its scope cancelled. The attempt then
fails as `AttemptFailed(reason="stall")` -- never as the "interrupted" of an external
cancellation, which stays what it was (GH-299, ADR-0057).
"""

from __future__ import annotations

import asyncio
import errno
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Any

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    CanUseTool,
    ClaudeAgentOptions,
    Message,
    PermissionResultDeny,
    ResultError,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from agentic.shared.environment import trace_id_of, with_resource_marker

from ...application.ports import (
    AttemptFailed,
    CheckTool,
    FreeSpace,
    Presubmit,
    ProcessReaper,
    RawResult,
    SchemaGuard,
    Scratch,
)
from ...domain import reaping
from ...domain.denials import denial_for
from ...domain.roles import Role
from ...domain.rules import (
    ReportedFailure,
    exception_motive,
    failure_motive,
    failure_reason,
    max_turns_motive,
)
from ...domain.scratch import SpaceReading
from ...domain.stall import (
    CHECK_EVERY_S,
    ChildActivity,
    LastEvent,
    Stall,
    StallSnapshot,
    judge,
)
from ...domain.suppressions import Forbidden
from ..plugins import PLUGIN, PLUGIN_DIR
from ..telemetry import NoTelemetry, Telemetry
from .check_server import SERVER, TIMEOUT_ENV, check_server, tool_timeout_ms
from .hooks import DELIVERY_TOOL, HookActivity, TurnCount, hooks_for
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

# The SDK's own cap on one JSON message off the CLI's stdout (`_DEFAULT_MAX_BUFFER_SIZE`,
# 1 MiB, when this option is left unset): a `Read` on a screenshot over ~750 KB
# (base64 in the tool result) exceeds it and kills the SDK's reader, which counts as an
# `AttemptFailed(reason="sdk")` (apostrophe GH-58). The API accepts images up to ~5 MB,
# ~6.7 MB base64-encoded plus JSON escaping; 16 MiB leaves more than double that margin,
# so the API -- with its status visible -- always rejects first, and this local reader
# never does. The same SDK value also thresholds its stderr buffer, but only when
# `options.stderr` is set; `options_for` below never sets it, so that use is dead code
# here.
MAX_BUFFER_BYTES: int = 16 * 1024 * 1024


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
    resource_attributes: str | None,
    presubmit: Presubmit | None = None,
    on_denied: Callable[[], None] | None = None,
    on_notice: Callable[[], None] | None = None,
    turns: TurnCount | None = None,
    on_presubmit_denied: Callable[[], None] | None = None,
    guard: SchemaGuard | None = None,
    forbidden: Sequence[Forbidden] | None = None,
    owner_mark: str | None = None,
    hook_activity: HookActivity | None = None,
    check_tool: CheckTool | None = None,
    check_results_dir: Path | None = None,
    writable: tuple[Path, ...] = (),
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
        max_buffer_size=MAX_BUFFER_BYTES,
        resume=resume,
        cwd=cwd,
        model=model,
        effort=role.effort,
        max_turns=role.max_turns,
        output_format={"type": "json_schema", "schema": inline_refs(json_schema)},
        setting_sources=[],  # ignore project CLAUDE.md and settings
        # None but the Developer's `run_check` (see the module docstring)
        mcp_servers={SERVER: check_server(check_tool, hook_activity)} if check_tool else {},
        strict_mcp_config=True,
        # Deltas as they are generated; without an observer nobody would read them.
        include_partial_messages=partial,
        # `TRACEPARENT` alone already parents Claude Code's own spans, and any
        # sub-agent it launches, under the chain it points to (ADR-0020's spike, run
        # against the pinned SDK and CLI: identical parenting with or without the
        # `OTEL_RESOURCE_ATTRIBUTES` marker below). The marker does not propagate
        # anything; it identifies whose run those spans belong to (GH-70).
        # `resource_attributes` is the ambient `os.environ` value, never `env` (the
        # runner's own git-identity overlay, which does not carry it in production) --
        # composing off `env` would overwrite whatever the real process inherited.
        env={
            **_child_env(env, role, traceparent, resource_attributes, owner_mark),
            **({TIMEOUT_ENV: tool_timeout_ms(check_tool)} if check_tool else {}),
        },
        hooks=hooks_for(
            role,
            cwd,
            presubmit,
            on_notice,
            turns,
            on_presubmit_denied,
            guard,
            forbidden,
            hook_activity,
            check_results_dir,
            writable,
        )
        or None,
        # A role that executes runs under `bypassPermissions`, which already shadows
        # this callback for every call (the SDK's own warning); wiring one there would
        # be dead code the hooks cage already replaces (ADR-0011, addendum). For a role
        # that does not execute, this is the only seam a call outside `Role.allowed`
        # reaches (`hooks_for` installs nothing for it): the callback never widens what
        # `Role.allowed` permits, since the SDK only consults it for what an allow rule
        # does not already cover.
        can_use_tool=None if role.executes else _deny_writes(role, on_denied),
    )


def _child_env(
    env: Mapping[str, str],
    role: Role,
    traceparent: str | None,
    resource_attributes: str | None,
    owner_mark: str | None = None,
) -> dict[str, str]:
    """`env` with `TRACEPARENT` set (as today) and, when `traceparent` carries a valid
    trace id, `OTEL_RESOURCE_ATTRIBUTES` refreshed with this run's marker
    (`with_resource_marker`, GH-70), composed off `resource_attributes` -- the caller's
    own ambient `os.environ` value, never `env` (the runner's `git identity` overlay,
    which does not carry it in production; composing off `env` would silently drop
    whatever the real process inherited, invariant 8). Nothing is added when there is
    no trace to mark against, so a role run under `NoTelemetry` sees exactly the `env`
    it was given, as before this change.

    `owner_mark`, when given, is the value of the process-owner variable (GH-282,
    ADR-0052) and lands in this copy only: `env` is shared by every attempt, so the
    mark of one must never be written into it."""
    child = {**env, **({"TRACEPARENT": traceparent} if traceparent else {})}
    if owner_mark is not None:
        child[reaping.MARK_NAME] = owner_mark
    trace_id = trace_id_of(traceparent)
    if trace_id is not None:
        child["OTEL_RESOURCE_ATTRIBUTES"] = with_resource_marker(
            resource_attributes, role_slug=role.slug, trace_id=trace_id
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
        *,
        reaper: ProcessReaper,
        product_key: str,
        stall_timeout_s: float = 2400.0,
        clock: Callable[[], float] = time.monotonic,
        check_results_dir: Path | None = None,
        scratch: Scratch | None = None,
        free_space: FreeSpace | None = None,
    ):
        # `verbose` streams each role's generation to `stream` (stderr by default).
        # `env` is added to every role's process: the git identity, for one.
        # `reaper` and `product_key` are required, never defaulted (GH-282): a runner
        # that marks its attempts and cannot stop what carries the mark would leak
        # silently. `product_key` is the one the reaper was built with.
        # `stall_timeout_s` (the composition passes `Configuration.stall_timeout_s`) and
        # the monotonic `clock` are what the watchdog judges an attempt by (ADR-0057).
        self.verbose = verbose
        self.stream = stream
        self.telemetry = telemetry
        self.env = dict(env or {})
        self.reaper = reaper
        self.product_key = product_key
        self.stall_timeout_s = stall_timeout_s
        self.clock = clock
        # The machine's store of check results (ADR-0065): a role that executes may not
        # read or write it (`hooks.store_path_denial`).
        self.check_results_dir = check_results_dir
        # Where each attempt's temporary files go, and how a failed one measures the
        # disk (GH-391, ADR-0069). Both `None` is an attempt with the inherited
        # `TMPDIR` and no reading, as before.
        self.scratch = scratch
        self.free_space = free_space

    async def run_once(
        self,
        role: Role,
        prompt: str,
        *,
        system_prompt: str,
        model: str,
        json_schema: dict[str, Any],
        cwd: Path,
        owner_task: str,
        resume: str | None = None,
        max_budget_usd: float | None = None,
        presubmit: Presubmit | None = None,
        guard: SchemaGuard | None = None,
        forbidden: Sequence[Forbidden] | None = None,
        check_tool: CheckTool | None = None,
    ) -> RawResult:
        """One attempt, marked with `owner_task` (the parent task, never a subtask) and a
        token of its own, and reaped once its stream is closed, whichever way it ended."""
        token = uuid.uuid4().hex
        hooks = HookActivity(self.clock)
        # Opened before the session and closed after the reap, so the directory outlives
        # every process of the attempt; its removal, then its record, is the last thing.
        with self._scratch_for(owner_task, role, token) as (scratch_dir, kept):
            try:
                raw = await self._session(
                    role,
                    prompt,
                    system_prompt=system_prompt,
                    model=model,
                    json_schema=json_schema,
                    cwd=cwd,
                    owner_mark=reaping.mark(self.product_key, owner_task, os.getpid(), token),
                    owner_task=owner_task,
                    token=token,
                    resume=resume,
                    max_budget_usd=max_budget_usd,
                    presubmit=presubmit,
                    guard=guard,
                    forbidden=forbidden,
                    check_tool=check_tool,
                    hooks=hooks,
                    scratch_dir=scratch_dir,
                    kept=kept,
                )
            except AttemptFailed as exc:
                exc.reaped = list(await self._reap(owner_task, token))
                # Only a failing attempt reads the disk, and before its directory is
                # removed: the space the directory held is not the space it left.
                exc.space = self._read_space(scratch_dir)
                exc.enospc = exc.enospc or hooks.enospc or _caused_by_enospc(exc)
                raise
            except BaseException:
                # A cancellation, or the process being asked to die: nothing to record, and
                # the reap still has to happen (`_reap` finishes it even if this is
                # cancelled again).
                await self._reap(owner_task, token)
                raise
            reaped = await self._reap(owner_task, token)
        return replace(raw, reaped=list(reaped)) if reaped else raw

    def _scratch_for(
        self, owner_task: str, role: Role, token: str
    ) -> AbstractContextManager[tuple[Path | None, Path | None]]:
        """The attempt's scratch directory and its task's evidence folder, or `(None,
        None)` when no scratch is composed. The task is the owner task, the root one."""
        if self.scratch is None:
            return nullcontext((None, None))
        return _opened(self.scratch, owner_task, role.slug, token)

    def _read_space(self, scratch_dir: Path | None) -> SpaceReading | None:
        """The lower of the scratch volume's and `/tmp`'s free bytes and free inodes."""
        if self.free_space is None or scratch_dir is None:
            return None
        try:
            readings = [self.free_space.read(scratch_dir), self.free_space.read(Path("/tmp"))]
        except OSError:
            # A reading that cannot be taken must not replace the failure it annotates.
            return None
        return SpaceReading(
            free_bytes=min(r.free_bytes for r in readings),
            free_inodes=min(r.free_inodes for r in readings),
        )

    async def _reap(self, owner_task: str, token: str) -> tuple[reaping.ReapedProcess, ...]:
        """Stops what this attempt left running, off the event loop: the reap waits out a
        grace period, and the sessions of a fan-out share one loop. Shielded and scheduled
        before it is awaited, so a cancellation re-raises at once and the thread still
        finishes the reap."""
        work = asyncio.ensure_future(
            asyncio.to_thread(self.reaper.reap_attempt, owner_task, os.getpid(), token)
        )
        return await asyncio.shield(work)

    async def _session(
        self,
        role: Role,
        prompt: str,
        *,
        system_prompt: str,
        model: str,
        json_schema: dict[str, Any],
        cwd: Path,
        owner_mark: str,
        owner_task: str,
        token: str,
        resume: str | None,
        max_budget_usd: float | None,
        presubmit: Presubmit | None,
        guard: SchemaGuard | None,
        forbidden: Sequence[Forbidden] | None,
        check_tool: CheckTool | None,
        hooks: HookActivity,
        scratch_dir: Path | None,
        kept: Path | None,
    ) -> RawResult:
        denials = 0
        notice_firings = 0
        presubmit_denials = 0
        # Defined before the observer and the `async for` below, so both the six raise
        # sites and the `Printer` (which only reads it, never derives its own count
        # from the `StreamEvent`s that only exist with `--verbose`) see the one number.
        tool_calls: dict[str, int] = {}

        # The sink decides whether the stream is watched, not `verbose`: with a log file
        # composed in, a role's tool lines and its `done:` line always reach it.
        # `--verbose` is additive on top of that (PM's decision, README.md, 14): with a
        # sink, the same lines also mirror to stderr; with none, stderr is the only
        # destination, exactly as before this task. `include_partial_messages` below
        # follows `verbose` alone, so the deltas either `Printer` shows only exist when
        # it is set.
        def _deliveries() -> int:
            return tool_calls.get(DELIVERY_TOOL, 0)

        observers = (
            [Printer(role.name, self.stream, _deliveries)] if self.stream is not None else []
        )
        if self.verbose:
            observers.append(Printer(role.name, sys.stderr, _deliveries))

        def _count_denial() -> None:
            nonlocal denials
            denials += 1

        # The counter `_turn_notice` reads (`hooks.TurnCount`'s own docstring):
        # constructed here, beside `_mark_notice`, and incremented in exactly one
        # place below, inside the stream loop's existing `parent_tool_use_id is None`
        # branch -- never by the hook, which only ever reads `.value`.
        turns = TurnCount()
        # `hooks`, the delivery hooks in flight, is one object per attempt like `turns`
        # (never module state, never shared with another session): the watchdog reads it.

        def _mark_notice() -> None:
            nonlocal notice_firings
            notice_firings += 1

        def _count_presubmit_denial() -> None:
            nonlocal presubmit_denials
            presubmit_denials += 1

        options = options_for(
            role,
            system_prompt=system_prompt,
            model=model,
            json_schema=json_schema,
            cwd=cwd,
            resume=resume,
            max_budget_usd=max_budget_usd,
            traceparent=self.telemetry.traceparent(),
            partial=self.verbose,
            env={**self.env, **_scratch_env(scratch_dir, kept)},
            # The real inherited value, not `self.env` (the git identity overlay
            # composition builds on top of it): the process this runner itself is in
            # is the one thing that actually has the ambient `OTEL_RESOURCE_ATTRIBUTES`
            # a spawned role's telemetry should be marked against (invariant 8).
            resource_attributes=os.environ.get("OTEL_RESOURCE_ATTRIBUTES"),
            presubmit=presubmit,
            on_denied=_count_denial,
            on_notice=_mark_notice,
            turns=turns,
            on_presubmit_denied=_count_presubmit_denial,
            guard=guard,
            forbidden=forbidden,
            owner_mark=owner_mark,
            hook_activity=hooks,
            check_tool=check_tool,
            check_results_dir=self.check_results_dir,
            writable=tuple(p for p in (scratch_dir, kept) if p is not None),
        )

        result: ResultMessage | None = None
        background = 0
        calls = 0
        # The last main-thread `AssistantMessage.usage` seen: what a cancellation
        # writes as its lower bound, never a sub-agent's (invariant 10).
        main_usage: dict[str, Any] | None = None
        pulse = _Pulse(last_activity=self.clock())
        stalled: Stall | None = None
        watch_scope = anyio.CancelScope()

        def _stall_failure() -> AttemptFailed:
            """The stall as the attempt's failure, with its own log line: the silence and
            the last event's kind, the counts the interruption line also carries, and no
            text of any message."""
            assert stalled is not None
            usage = _usage(main_usage)
            self._warn(
                f"[{role.name}] stalled {stalled.seconds}s, last event {stalled.last_event}, "
                f"{calls} turns, {usage['input']} input / {usage['output']} output tokens "
                "seen on the main thread, cost unknown\n"
            )
            return AttemptFailed(
                f"stalled: no sign of life for {stalled.seconds}s, last event {stalled.last_event}",
                session_id=pulse.session_id,
                reason="stall",
                denials=denials,
                turn_notice=notice_firings > 0,
                turn_notice_firings=notice_firings,
                deliveries=tool_calls.get(DELIVERY_TOOL, 0),
                presubmit_denials=presubmit_denials,
                main_thread_turns=turns.value,
            )

        async def watch() -> None:
            """One tick after another, never piling up: scan, judge, and on a stall reap
            the attempt by its mark first and cancel the scope only if the reap left
            something alive."""
            nonlocal stalled
            previous: ChildActivity | None = None
            while True:
                child = await asyncio.to_thread(
                    self.reaper.child_activity, owner_task, os.getpid(), token, os.getpid()
                )
                now = self.clock()
                verdict = judge(
                    StallSnapshot(
                        now=now,
                        last_activity=pulse.last_activity,
                        pending_tools=len(pulse.pending),
                        hooks_in_flight=hooks.in_flight,
                        last_hook_exit=hooks.last_exit,
                        child=child,
                        previous_child=previous,
                        timeout_s=self.stall_timeout_s,
                    )
                )
                pulse.last_activity, previous = verdict.last_activity, child
                if verdict.stalled:
                    stalled = Stall(int(now - verdict.last_activity), pulse.last_event)
                    left = await asyncio.to_thread(
                        self.reaper.reap_attempt, owner_task, os.getpid(), token
                    )
                    if any(entry["outcome"] in {"survived", "error"} for entry in left):
                        watch_scope.cancel()
                    return
                await anyio.sleep(CHECK_EVERY_S)

        stream = query(prompt=prompt, options=options)
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(watch)
                try:
                    with watch_scope:
                        try:
                            async for message in stream:
                                pulse.see(message, self.clock())
                                for observer in observers:
                                    observer(message)
                                if isinstance(message, AssistantMessage):
                                    calls += 1
                                    if message.parent_tool_use_id is None:
                                        main_usage = message.usage
                                        # The quantity `domain/turn_notice.py`'s
                                        # threshold is stated in: a main-thread
                                        # `AssistantMessage` count, >=1 per harness turn by
                                        # construction, unlike the `PostToolUse` tally it
                                        # replaces (`hooks.TurnCount`'s own docstring).
                                        turns.value += 1
                                    for name in _tool_names(message):
                                        tool_calls[name] = tool_calls.get(name, 0) + 1
                                background += _background_agents(message)
                                if isinstance(message, ResultMessage):
                                    result = message
                                    if _delivered(message):
                                        # the first delivery is the verdict (module docstring)
                                        break
                        except anyio.get_cancelled_exc_class():
                            if stalled is not None and watch_scope.cancel_called:
                                # the watchdog's own cancel: the stall is told below
                                raise
                            # Never an `AttemptFailed`: the process is being asked to die,
                            # and turning this into a retry would spend money while it
                            # happens (invariant 9). The figure is a read, never a sum, off
                            # the same normaliser `_usage` uses.
                            usage = _usage(main_usage)
                            self._warn(
                                f"[{role.name}] interrupted after {calls} turns, "
                                f"{usage['input']} input / {usage['output']} output tokens "
                                "seen on the main thread, sub-agents not counted, "
                                "cost unknown\n"
                            )
                            raise
                        except ResultError as exc:
                            if stalled is not None:
                                # the kill's symptom, not its own news
                                raise _stall_failure() from exc
                            # The CLI reports the failed result, then exits non-zero; the
                            # SDK turns that into ResultError after yielding the
                            # ResultMessage.
                            cost, session = _left_behind(result)
                            if exc.subtype == "error_max_turns":
                                raise AttemptFailed(
                                    max_turns_motive(role.max_turns, _reported_error(exc)),
                                    cost,
                                    session,
                                    reason="max_turns",
                                    denials=denials,
                                    turn_notice=notice_firings > 0,
                                    turn_notice_firings=notice_firings,
                                    deliveries=tool_calls.get(DELIVERY_TOOL, 0),
                                    presubmit_denials=presubmit_denials,
                                    main_thread_turns=turns.value,
                                ) from exc
                            raise AttemptFailed(
                                failure_motive(_reported_error(exc)),
                                cost,
                                session,
                                reason=failure_reason(_reported_error(exc)),
                                denials=denials,
                                turn_notice=notice_firings > 0,
                                turn_notice_firings=notice_firings,
                                deliveries=tool_calls.get(DELIVERY_TOOL, 0),
                                presubmit_denials=presubmit_denials,
                                main_thread_turns=turns.value,
                            ) from exc
                        except (
                            Exception
                        ) as exc:  # the SDK raises its own hierarchy; all of it is a failure
                            if stalled is not None:
                                raise _stall_failure() from exc
                            raise AttemptFailed(
                                f"SDK error: {exception_motive(exc)}",
                                reason="sdk",
                                denials=denials,
                                turn_notice=notice_firings > 0,
                                turn_notice_firings=notice_firings,
                                deliveries=tool_calls.get(DELIVERY_TOOL, 0),
                                presubmit_denials=presubmit_denials,
                                main_thread_turns=turns.value,
                            ) from exc
                        finally:
                            # The stream is closed before anything is reaped
                            # (`run_once`): a generator left suspended by the `break`
                            # above would still own the CLI.
                            await _close(stream)
                finally:
                    group.cancel_scope.cancel()  # the watchdog's work ends with the loop
        except BaseExceptionGroup as grouped:
            # The task group wraps whatever its body raised; the raise sites above are
            # the attempt's contract, so a lone exception leaves as itself.
            raise (grouped.exceptions[0] if len(grouped.exceptions) == 1 else grouped)
        if stalled is not None:
            raise _stall_failure()

        if result is None:
            raise AttemptFailed(
                "run ended without a result message",
                reason="sdk",
                denials=denials,
                turn_notice=notice_firings > 0,
                turn_notice_firings=notice_firings,
                deliveries=tool_calls.get(DELIVERY_TOOL, 0),
                presubmit_denials=presubmit_denials,
                main_thread_turns=turns.value,
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
                turn_notice=notice_firings > 0,
                turn_notice_firings=notice_firings,
                deliveries=tool_calls.get(DELIVERY_TOOL, 0),
                presubmit_denials=presubmit_denials,
                main_thread_turns=turns.value,
            )
        if result.is_error:
            raise AttemptFailed(
                failure_motive(_reported_result(result)),
                cost,
                session,
                reason=failure_reason(_reported_result(result)),
                denials=denials,
                turn_notice=notice_firings > 0,
                turn_notice_firings=notice_firings,
                deliveries=tool_calls.get(DELIVERY_TOOL, 0),
                presubmit_denials=presubmit_denials,
                main_thread_turns=turns.value,
            )
        return RawResult(
            structured_output=result.structured_output,
            cost_usd=cost,
            turns=result.num_turns,
            session_id=session,
            denials=denials,
            turn_notice=notice_firings > 0,
            turn_notice_firings=notice_firings,
            cost_by_model=_cost_by_model(result),
            usage=_usage(result.usage),
            calls=calls,
            tool_calls=tool_calls,
            deliveries=tool_calls.get(DELIVERY_TOOL, 0),
            presubmit_denials=presubmit_denials,
            main_thread_turns=turns.value,
        )

    def _warn(self, text: str) -> None:
        stream = self.stream or sys.stderr
        stream.write(text)
        stream.flush()


def _opened(
    scratch: Scratch, owner_task: str, slug: str, token: str
) -> AbstractContextManager[tuple[Path | None, Path | None]]:
    """`scratch.open` for a role attempt, yielding `(directory, evidence folder)`."""

    @contextmanager
    def opened() -> Iterator[tuple[Path, Path]]:
        with ExitStack() as stack:
            try:
                directory = stack.enter_context(scratch.open(owner_task, "r", token, slug))
                kept = scratch.ensure_kept(owner_task)
            except OSError as exc:
                # A full disk is when this matters most: the attempt never started, and
                # it is the measured errno, not a raw error from `run_once`, that says why.
                if exc.errno != errno.ENOSPC:
                    raise
                raise AttemptFailed(
                    "the attempt's scratch directory could not be created: no space left",
                    enospc=True,
                ) from exc
            yield directory, kept

    return opened()


def _scratch_env(scratch_dir: Path | None, kept: Path | None) -> dict[str, str]:
    """What an attempt's processes are told about their disk: `TMPDIR` for what they
    write and throw away, `TASK_EVIDENCE_DIR` for what they cite. Not `AGENTIC_*`: the
    composition blanks those for a role."""
    if scratch_dir is None or kept is None:
        return {}
    return {"TMPDIR": str(scratch_dir), "TASK_EVIDENCE_DIR": str(kept)}


def _caused_by_enospc(failure: BaseException) -> bool:
    """Whether the chain behind `failure` holds an `OSError` that carries `ENOSPC`: the
    errno, a structured fact, never the message."""
    seen: set[int] = set()
    error: BaseException | None = failure
    while error is not None and id(error) not in seen:
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


@dataclass
class _Pulse:
    """What the stream has shown of one attempt, for the watchdog: the instant of the
    last sign of life, the kind of the last message, the ids of the main-thread tool
    calls still waiting for their result and the session the CLI opened."""

    last_activity: float
    last_event: LastEvent = "none"
    pending: set[str] = field(default_factory=set)
    session_id: str = ""

    def see(self, message: Message, now: float) -> None:
        self.last_activity = now
        self.last_event = _event_of(message)
        if isinstance(message, SystemMessage) and message.subtype == "init":
            self.session_id = str(message.data.get("session_id") or self.session_id)
        elif isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
            self.pending.update(
                block.id for block in message.content if isinstance(block, ToolUseBlock)
            )
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            # A sub-agent's result carries an id that was never pending: nothing to drop.
            self.pending.difference_update(
                block.tool_use_id for block in message.content if isinstance(block, ToolResultBlock)
            )


def _event_of(message: Message) -> LastEvent:
    """The kind of `message`, from its type alone: the one place `LastEvent` is derived.
    The SDK's two other event types are system-side notices."""
    if isinstance(message, AssistantMessage):
        return "assistant"
    if isinstance(message, UserMessage):
        return "user"
    if isinstance(message, ResultMessage):
        return "result"
    if isinstance(message, StreamEvent):
        return "stream_event"
    return "system"


async def _close(stream: AsyncIterator[Message]) -> None:
    """Closes `stream` when it is an async generator, as `query` returns."""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


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


def _usage(usage: dict[str, Any] | None) -> dict[str, int]:
    """A token usage payload, normalised to the closed `kind` set `agentic.role.tokens`
    groups by. `RawResult.usage`'s own docstring has the evidence for why this is a
    direct read, never a subtraction against an earlier call: fed a `ResultMessage`'s
    `usage` for a delivered attempt, or an interrupted one's last main-thread
    `AssistantMessage.usage` (invariant 10) -- the one normaliser either way, never a
    second token-counting function. Zeroed when there is none to read.
    """
    raw = usage or {}
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
