"""Whether `ResultMessage.usage` is per-turn or session-cumulative. Excluded from CI:
`uv run pytest -m llm`.

`agentic.role.tokens` needs to know whether to read `usage` as-is or subtract the
previous turn's figures from it (README.md, 14, Observabilidad; issue #24). This probe
resumes a session across two separate `query()` calls the way `run_with_retry` resumes a
failed attempt, and prints both turns' `usage` so the answer can be pasted into the task
document and `observability/README.md` as evidence. The unit test over fabricated
payloads in `tests/core/process/adapters/claude_sdk/test_runner.py` encodes whatever the
printed evidence says; this file only produces the evidence.
"""

import json

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

SYSTEM = "Reply with exactly one short sentence, nothing else."


async def test_usage_across_a_resumed_session(capsys):
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM,
        tools=[],
        setting_sources=[],
        mcp_servers={},
        strict_mcp_config=True,
        max_turns=1,
        model="claude-sonnet-5",
    )
    session_id = None
    async with ClaudeSDKClient(options) as client:
        await client.query("Say the word 'alpha' and nothing else.")
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                session_id = message.session_id
                print("TURN 1 usage:", json.dumps(message.usage))
                print("TURN 1 total_cost_usd:", message.total_cost_usd)
                print("TURN 1 num_turns:", message.num_turns)

    assert session_id
    resumed = ClaudeAgentOptions(
        system_prompt=SYSTEM,
        tools=[],
        setting_sources=[],
        mcp_servers={},
        strict_mcp_config=True,
        max_turns=1,
        model="claude-sonnet-5",
        resume=session_id,
    )
    async with ClaudeSDKClient(resumed) as client:
        await client.query("Now say the word 'beta' and nothing else.")
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                print("TURN 2 usage:", json.dumps(message.usage))
                print("TURN 2 total_cost_usd:", message.total_cost_usd)
                print("TURN 2 num_turns:", message.num_turns)
