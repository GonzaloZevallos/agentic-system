"""Thin layer over the Claude Agent SDK: run one role, return its output.

Authenticates through the Claude Code subscription, not an API key.

Roles have no tools yet: they reason and write Markdown. Once Developer joins,
it will need tools and a deliberate permission mode.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)


@dataclass(frozen=True)
class Role:
    name: str
    prompt: str
    model: str = "claude-sonnet-5"
    effort: str = "medium"
    max_turns: int = 4


@dataclass
class Output:
    text: str
    cost_usd: float = 0.0
    turns: int = 0
    data: dict = field(default_factory=dict)


_JSON_BLOCK = re.compile(r"```json\s*(.+?)\s*```", re.S)


def _extract_json(text: str) -> dict:
    """Pull the first ```json``` block out of a reply. Empty dict if absent."""
    match = _JSON_BLOCK.search(text)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


async def run(role: Role, prompt: str) -> Output:
    options = ClaudeAgentOptions(
        system_prompt=role.prompt,
        allowed_tools=[],
        model=role.model,
        effort=role.effort,
        max_turns=role.max_turns,
        setting_sources=[],  # ignore project CLAUDE.md and settings
    )

    chunks: list[str] = []
    output = Output(text="")
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            chunks.extend(b.text for b in message.content if isinstance(b, TextBlock))
        elif isinstance(message, ResultMessage):
            output.cost_usd = message.total_cost_usd or 0.0
            output.turns = message.num_turns

    output.text = "\n".join(chunks).strip()
    output.data = _extract_json(output.text)
    return output
