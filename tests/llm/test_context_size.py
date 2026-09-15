"""A role's context is its own prompt and its tools, nothing inherited from the user's
Claude Code. Excluded from CI: `uv run pytest -m llm`.

Issue #18 (README.md, 14, Observabilidad): every role loaded the account's claude.ai
connectors, ~233k tokens of tool definitions per call. The connectors attach 3-6 s after
the session starts (run 4's transcripts), so a run that ends in one call never shows it:
this one makes a second call after a pause. The leak is only visible on an account with
connectors; on any other this passes vacuously.
"""

import anyio
import pytest
from claude_agent_sdk import ClaudeSDKClient, ResultMessage, StreamEvent

from agentic.core.process.adapters.claude_sdk.runner import options_for
from agentic.core.process.adapters.filesystem import FilesystemPrompts
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import PJM, render

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

# Claude Code's own prompt plus the role's and its tools weigh under 20k tokens; a call
# that carries the account's connectors reads over 230k. Anything in between is a leak too.
MAX_CONTEXT_PER_CALL = 30_000
# Longer than the connectors take to attach.
PAUSE_SECONDS = 10

TASKS = [
    "Task: Fix typo in footer\n\nThe footer says 'Copyrigth'.",
    "Task: Export members as CSV\n\nAdmins want to download the member list.",
]


async def test_a_roles_context_per_call_stays_small(tmp_path):
    prompt = render(*FilesystemPrompts(tmp_path).raw(PJM))
    options = options_for(
        PJM,
        system_prompt=prompt.text,
        model="claude-sonnet-5",
        json_schema=schemas.json_schema(PJM.schema, {}),
        cwd=tmp_path,
        resume=None,
        max_budget_usd=1.0,
        traceparent=None,
        partial=True,  # `message_start` carries each API call's usage
        env={},
    )
    contexts: list[int] = []
    async with ClaudeSDKClient(options) as client:
        for task in TASKS:
            await client.query(task)
            async for message in client.receive_response():
                if isinstance(message, StreamEvent) and (n := _context_of(message)) is not None:
                    contexts.append(n)
                if isinstance(message, ResultMessage):
                    assert not message.is_error, message.errors
            await anyio.sleep(PAUSE_SECONDS)

    assert len(contexts) >= 2, "one API call per task expected: the stream lost its usage"
    assert max(contexts) < MAX_CONTEXT_PER_CALL, f"context per call: {contexts}"


def _context_of(event: StreamEvent) -> int | None:
    """The tokens an API call carried in, as its `message_start` reports them."""
    if event.event.get("type") != "message_start":
        return None
    usage = event.event["message"]["usage"]
    return (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
