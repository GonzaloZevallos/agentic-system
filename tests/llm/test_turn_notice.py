"""Spike (issue #85, `turn-notice`): the declared gate for a live turn-budget notice
(ADR-0004, seventh addendum, "the declared gate for the subtask B"). Excluded from CI:
`uv run pytest -m llm`.

Three things have to hold before `hooks_for` gains a `PostToolUse` mechanism:

(a) some hook-side signal tracks the cap the CLI actually enforces (`num_turns` is not
    it: a recorded run showed `turns: 17` under `max_turns: 12`; `calls` is not it
    either — 168 calls against 3 turns on `tech_lead_review`, already on record, no
    fresh run needed to see it);
(b) a sub-agent's tool call is distinguishable from the main thread's, by `agent_id`;
(c) the hook-side count at a deliberate cut lines up with `max_turns` and with the
    `num_turns` the cut result reports.

Two probes: the first forces a real `DEVELOPER`-shaped role into real work under a
starved `max_turns`, so it lands on an actual `error_max_turns` cut and answers (a)
and (c) together; the second, with turns to spare, only needs one sub-agent launch to
answer (b). This file only produces the evidence; the outcome (a hook, or the
prompt-only fallback) is decided from what it prints, quoted verbatim into the task
document's `turn-notice` section.
"""

from dataclasses import replace

import pytest
from claude_agent_sdk import (
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    ResultError,
    ResultMessage,
    query,
)

from agentic.core.process.adapters.claude_sdk.runner import options_for
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import DEVELOPER

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

# Starved on purpose: real multi-step work (several `Bash` calls) does not fit, so the
# harness cuts the run for real instead of the model choosing to stop early.
CUT_MAX_TURNS = 2

CUT_PROMPT = (
    "Do all of this, one Bash call per turn, never combining two into one command "
    "and never skipping ahead: (1) run `pwd`, (2) run `whoami`, (3) run `date`, (4) "
    "run `echo done`. Only after all four have each run once, reply with one short "
    "sentence summarising the four outputs. Do not call StructuredOutput; a plain "
    "text reply is what is wanted here."
)


def _record(events: list[tuple[str, object]]) -> HookMatcher:
    async def hook(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        # `.get()` with a default: `agent_id` is `NotRequired` on `PostToolUseHookInput`
        # (absent on the main thread), never a plain subscript.
        events.append((data.get("tool_name", ""), data.get("agent_id")))
        return {}

    return HookMatcher(hooks=[hook])


async def test_posttooluse_count_at_a_deliberate_cut(tmp_path):
    """Prints every `PostToolUse` event a starved, real-work run left before the
    harness cut it, plus the cut's own `max_turns` and reported `num_turns`, so (a)
    and (c) can be read off one run."""
    events: list[tuple[str, object]] = []
    options = options_for(
        replace(DEVELOPER, max_turns=CUT_MAX_TURNS),
        system_prompt=CUT_PROMPT,
        model="claude-sonnet-5",
        json_schema=schemas.json_schema(DEVELOPER.schema, {}),
        cwd=tmp_path,
        resume=None,
        max_budget_usd=2.0,
        traceparent=None,
        partial=False,
        env={},
    )
    options.hooks = {**(options.hooks or {}), "PostToolUse": [_record(events)]}

    result: ResultMessage | None = None
    error: ResultError | None = None
    try:
        async for message in query(prompt="Go.", options=options):
            if isinstance(message, ResultMessage):
                result = message
    except ResultError as exc:
        error = exc

    print("EVENTS (tool, agent_id):", events)
    print("PostToolUse COUNT AT THE CUT:", len(events))
    print("max_turns (enforced-cap turns):", CUT_MAX_TURNS)
    subtype = error.subtype if error is not None else (result.subtype if result else None)
    print("RESULT subtype:", subtype)
    print("num_turns (reported):", result.num_turns if result is not None else None)


SUBAGENT_PROMPT = (
    "Use the Agent tool once to launch a general-purpose sub-agent whose prompt is "
    "exactly: 'Call Bash with `echo sub` once, then reply done.' Wait for it to "
    "finish, then call Bash yourself with `echo main` once. Then reply with one "
    "short sentence. Do not call StructuredOutput."
)


async def test_agent_id_on_a_sub_agent_call_versus_the_main_thread(tmp_path):
    """Prints every `PostToolUse` event of a run that launches one sub-agent and also
    calls a tool on the main thread itself, so (b) can be read off `agent_id`."""
    events: list[tuple[str, object]] = []
    options = options_for(
        replace(DEVELOPER, max_turns=10),
        system_prompt=SUBAGENT_PROMPT,
        model="claude-sonnet-5",
        json_schema=schemas.json_schema(DEVELOPER.schema, {}),
        cwd=tmp_path,
        resume=None,
        max_budget_usd=2.0,
        traceparent=None,
        partial=False,
        env={},
    )
    options.hooks = {**(options.hooks or {}), "PostToolUse": [_record(events)]}

    async for message in query(prompt="Go.", options=options):
        if isinstance(message, ResultMessage):
            print("RESULT subtype:", message.subtype, "num_turns:", message.num_turns)

    print("EVENTS (tool, agent_id):", events)
    print("MAIN-THREAD (agent_id is None) COUNT:", sum(1 for _, a in events if a is None))
    print("SUB-AGENT (agent_id is not None) COUNT:", sum(1 for _, a in events if a is not None))
