"""Spike (issue #88, subtask `gate`): can a hook over `StructuredOutput` see a
structured-delivery call the CLI itself rejects, and is what it sees the same object
the harness later validates? Excluded from CI: `uv run pytest -m llm`.

The task's evidence (#32) shows the Developer calling the structured-output tool 3-13
times in one turn, every rejected attempt shaped the same way: a single `markdown` key
with `summary` and `assumptions` written as tags inside it instead of as their own JSON
keys. `ClaudeSdkRunner.run_once` only ever sees `AssistantMessage`, `StreamEvent` and
`ResultMessage`; our own `schemas.validate` runs once, on the `ResultMessage` at the
end. So in #32 it ran zero times, not thirteen: the retries happened *inside the CLI*,
against its own `output_format` schema, before our code ever saw a thing. Whether a
`PreToolUse`/`PostToolUse` hook can observe that and whether what it sees is the same
object `schemas.validate` would later check are exactly the two facts a schema guard
(the sibling subtask `schema-guard`) needs before it can exist at all.

Four conditions, decided from the printed evidence below and pasted into the ADR-0011
fifth addendum:

(i) does the CLI's own rejection reach the stream, and as what message type;
(ii) does a `PostToolUse` on `StructuredOutput` fire on the rejected call, with what
    payload;
(iii) does a `PreToolUse` on `StructuredOutput` see the invalid input, and does it fire
    before or after the CLI's own rejection reaches the stream;
(iv) the one that governs: is the `tool_input` a `PreToolUse` sees on a *valid* delivery
    equal, once deserialised, to `structured_output` on that call's `ResultMessage`
    (the invalid call never produces a `structured_output` to compare against).

The toy role is built here, not in `agentic/`: a `Role` this file constructs itself,
`executes=True` so it runs the way Developer does, with no prompt file and no entry in
`domain/roles.ROLES`. Its schema is `Delivery`, the real one two turns of #32 kept
missing — reused as a read, the same way every other spike in this directory reads a
schema off the domain, never as a new schema of its own. Using it also side-steps the
line-list note in the plan's brief for condition (iv) by construction: `json_schema`
turns `assumptions` (a `list[str]` in the schema) into a `string` field for the model
(`schemas.line_lists`), so both `tool_input` and `structured_output` carry it as the
same string, never one as a list and the other as text.

No presubmit is wired (`presubmit=None`, the default): the brief asks for the CLI's and
the hooks' own behaviour, unclouded by our own puerta de entrega.
"""

from __future__ import annotations

from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from agentic.core.process.adapters.claude_sdk.runner import options_for
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import Role

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

# The tool name `ClaudeSdkRunner` itself treats as the delivery (`runner.KNOWN_TOOLS`).
STRUCTURED_OUTPUT = "StructuredOutput"

# Built here, never in `agentic/domain/roles.py`: a role of the spike, not of the
# product (invariant: no subtask adds a `Role` to the domain, not even for a test).
TOY_ROLE = Role(
    name="Toy Developer",
    slug="toy_developer_gate_spike",
    kind="actor",
    schema=schemas.Delivery,
    executes=True,
    effort="low",
    max_turns=6,
)

TOY_SCHEMA = schemas.json_schema(schemas.Delivery, {})

INVALID_FIRST_PROMPT = (
    "Call the `StructuredOutput` tool. On your FIRST call, pass exactly one top-level "
    "JSON key, `markdown`, whose string value embeds the other two required fields as "
    "tags instead of separate JSON keys, exactly like this shape (fill in short "
    "placeholder text for each section, keep it short): "
    '{"markdown": "### Changes\\nsomething.\\n</markdown>\\n<summary>one line</summary>\\n'
    '<assumptions>none</assumptions>\\n"}. '
    "Do this on purpose, even though the schema requires `summary` and `assumptions` as "
    "their own top-level keys: this is a deliberate probe of how the harness reports a "
    "schema violation, not a real delivery. If that call comes back with an error, call "
    "`StructuredOutput` again, this time correctly: `markdown`, `summary` and "
    "`assumptions` as three separate top-level JSON keys, each a short one-line string. "
    "Then stop."
)

VALID_PROMPT = (
    "Call the `StructuredOutput` tool exactly once, with three separate top-level JSON "
    "keys: `markdown` (a short string, e.g. '### Changes\\nNothing, this is a probe.'), "
    "`summary` (one short line), and `assumptions` (one short line, or an empty string). "
    "Do not embed `summary` or `assumptions` inside `markdown`. Then stop."
)


def _spike_options(tmp_path: Any) -> Any:
    return options_for(
        TOY_ROLE,
        system_prompt="You are a probe for a harness spike. Follow the user's "
        "instructions on how to call `StructuredOutput` exactly.",
        model="claude-sonnet-5",
        json_schema=TOY_SCHEMA,
        cwd=tmp_path,
        resume=None,
        max_budget_usd=1.0,
        traceparent=None,
        partial=False,
        env={},
        resource_attributes=None,
    )


def _wire_spike_hooks(options: Any, timeline: list[tuple[str, object]]) -> None:
    """Adds a `PreToolUse` and a `PostToolUse` matcher on `StructuredOutput` that only
    record, on top of whatever `options_for` already wired for `TOY_ROLE.executes`
    (its Bash/Edit cage, which this probe never triggers: it declares no tools)."""

    async def pre(data: HookInput, tool_use_id: str | None, context: HookContext) -> HookJSONOutput:
        timeline.append(("PreToolUse", dict(data.get("tool_input") or {})))
        return {}

    async def post(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        timeline.append(
            (
                "PostToolUse",
                {
                    "tool_input": dict(data.get("tool_input") or {}),
                    "tool_response": data.get("tool_response"),
                },
            )
        )
        return {}

    existing = options.hooks or {}
    options.hooks = {
        **existing,
        "PreToolUse": [
            *existing.get("PreToolUse", []),
            HookMatcher(matcher=STRUCTURED_OUTPUT, hooks=[pre]),
        ],
        "PostToolUse": [
            *existing.get("PostToolUse", []),
            HookMatcher(matcher=STRUCTURED_OUTPUT, hooks=[post]),
        ],
    }


async def test_invalid_delivery_visibility(tmp_path):
    """Conditions (i), (ii), (iii): forces the toy role into the exact malformed shape
    #32's log shows, and prints, in call order, everything the stream and the two hooks
    saw, so the ADR addendum can quote it verbatim."""
    timeline: list[tuple[str, object]] = []
    options = _spike_options(tmp_path)
    _wire_spike_hooks(options, timeline)

    async for message in query(prompt=INVALID_FIRST_PROMPT, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.name == STRUCTURED_OUTPUT:
                    timeline.append(("AssistantMessage.ToolUseBlock", dict(block.input)))
        if isinstance(message, UserMessage):
            blocks = message.content if isinstance(message.content, list) else []
            for block in blocks:
                if isinstance(block, ToolResultBlock):
                    timeline.append(
                        (
                            "UserMessage.ToolResultBlock",
                            {"is_error": block.is_error, "content": block.content},
                        )
                    )
        if isinstance(message, ResultMessage):
            timeline.append(
                (
                    "ResultMessage",
                    {
                        "is_error": message.is_error,
                        "subtype": message.subtype,
                        "structured_output": message.structured_output,
                    },
                )
            )
            if not message.is_error and message.structured_output is not None:
                break  # the harness's own first-delivery cut (ADR-0004; invariant 3)

    print("TIMELINE (in call order):")
    for i, (tag, payload) in enumerate(timeline):
        print(f"  [{i}] {tag}: {payload}")

    rejections = [
        i
        for i, (tag, payload) in enumerate(timeline)
        if tag == "UserMessage.ToolResultBlock" and payload.get("is_error")
    ]
    pre_on_invalid = [
        i
        for i, (tag, payload) in enumerate(timeline)
        if tag == "PreToolUse" and "summary" not in payload
    ]
    post_on_invalid = [
        i
        for i, (tag, payload) in enumerate(timeline)
        if tag == "PostToolUse" and "summary" not in payload.get("tool_input", {})
    ]
    print("condition (i) CLI rejection reached the stream:", bool(rejections), "at", rejections)
    print(
        "condition (ii) PostToolUse fired on the invalid call:",
        bool(post_on_invalid),
        "at",
        post_on_invalid,
    )
    print(
        "condition (iii) PreToolUse saw the invalid input:",
        bool(pre_on_invalid),
        "at",
        pre_on_invalid,
    )
    if pre_on_invalid and rejections:
        order = "before" if pre_on_invalid[0] < rejections[0] else "after"
        print("condition (iii) PreToolUse ran", order, "the CLI's own rejection reached the stream")


async def test_valid_delivery_pretooluse_input_matches_result(tmp_path):
    """Condition (iv): on a delivery the CLI accepts, is what `PreToolUse` saw the same
    object, deserialised, as `structured_output` on the `ResultMessage` of that call?"""
    timeline: list[tuple[str, object]] = []
    options = _spike_options(tmp_path)
    _wire_spike_hooks(options, timeline)

    result: ResultMessage | None = None
    async for message in query(prompt=VALID_PROMPT, options=options):
        if isinstance(message, ResultMessage):
            result = message
            if not message.is_error and message.structured_output is not None:
                break

    pre_inputs = [payload for tag, payload in timeline if tag == "PreToolUse"]
    print("PreToolUse tool_input(s) seen:", pre_inputs)
    print("ResultMessage.structured_output:", result.structured_output if result else None)
    print("ResultMessage.is_error:", result.is_error if result else None)

    assert result is not None and not result.is_error
    assert pre_inputs, (
        "no PreToolUse fired on the delivering call: condition (iv) has nothing to compare"
    )
    same = pre_inputs[-1] == result.structured_output
    print("condition (iv) tool_input == structured_output (deserialised, same object):", same)
