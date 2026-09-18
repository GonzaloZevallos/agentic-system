"""Spike (issue #69, #81; `denial-reason`): does `can_use_tool` reach the model through
the one-shot `query()` path `ClaudeSdkRunner.run_once` actually uses, the way
`_configure_can_use_tool` documents it being wired from `ClaudeSDKClient.connect()`
too? `options_for` wires `denial_for`'s sentence as the callback for a role that does
not execute (`runner._deny_writes`); if `query()` never consulted it, that wiring would
be dead code, and the fallback (ADR-0011, fourth addendum) would be the prompt sentence
in `product_analyst_bug.md` alone. Excluded from CI: `uv run pytest -m llm`.

Result, pasted into `GH-81`'s section: it does reach the model. The unit tests over a
fake `query` in `tests/core/process/adapters/claude_sdk/test_runner.py` encode the
callback's own behaviour (denies, counts, carries `denial_for`'s text); this file only
proves the SDK actually calls it for the real, unattended one-shot path the runner uses.
"""

from dataclasses import replace

import pytest
from claude_agent_sdk import AssistantMessage, PermissionResultDeny, TextBlock, query

from agentic.core.process.adapters.claude_sdk.runner import options_for
from agentic.core.process.domain.denials import denial_for
from agentic.core.process.domain.roles import PJM

pytestmark = [pytest.mark.llm, pytest.mark.anyio]


async def test_a_denied_write_reaches_the_model_through_query(tmp_path):
    # A role that does not execute, with `Write` in its tools but not in `allowed`:
    # exactly `PRODUCT_ANALYST_BUG`'s shape, minus the read-only rules that are not the
    # point of this probe.
    role = replace(PJM, tools=("Read", "Write"), allowed=("Read",))
    seen: list[tuple[str, dict]] = []

    async def observed_deny(tool_name, tool_input, context):
        seen.append((tool_name, tool_input))
        return PermissionResultDeny(message=denial_for(role))

    options = options_for(
        role,
        system_prompt=(
            "Call the Write tool once to create ./notes.txt with the content 'hi'. "
            "Whatever the tool's result says, quote it back verbatim as your reply, "
            "then stop."
        ),
        model="claude-sonnet-5",
        json_schema={"type": "object", "properties": {"markdown": {"type": "string"}}},
        cwd=tmp_path,
        resume=None,
        max_budget_usd=None,
        traceparent=None,
        partial=False,
        env={},
    )
    # The real wiring already set `options.can_use_tool` to `_deny_writes(role, ...)`;
    # this spike only needs to see that the SDK calls *some* callback at all through
    # `query()`, so it swaps in an observing one with the exact same message.
    assert options.can_use_tool is not None  # `options_for`'s own wiring, unswapped
    options.can_use_tool = observed_deny

    reply = ""
    async for message in query(prompt="Go.", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    reply += block.text

    print("CALLBACK SAW:", seen)
    print("MODEL REPLY:", reply)
    assert seen and seen[0][0] == "Write"  # the SDK asked the callback through `query()`
    assert "writes nothing" in reply  # the role's own rule reached the model, quoted back
    assert not (tmp_path / "notes.txt").exists()  # denied, not just asked about
