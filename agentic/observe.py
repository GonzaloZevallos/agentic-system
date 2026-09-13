"""Watching a role work.

`Printer` writes the SDK stream of one role to a terminal as it happens: text and
thinking deltas, tool calls, and the closing cost line, each prefixed with the role
name. It is what `agentic run --verbose` shows. Langfuse gets the same run afterwards
through Claude Code's own OpenTelemetry export (DISENO.md, 14).
"""

from __future__ import annotations

import json
import sys
from typing import IO

from claude_agent_sdk import AssistantMessage, Message, ResultMessage, StreamEvent, ToolUseBlock


class Printer:
    """Callable for `agent.run(observer=...)`. Needs partial messages to show generation."""

    def __init__(self, role_name: str, stream: IO[str] | None = None):
        self.role = role_name
        self.stream = stream or sys.stderr
        self._open: str | None = None  # kind of the content block being streamed

    def __call__(self, message: Message) -> None:
        if isinstance(message, StreamEvent):
            self._event(message.event)
        elif isinstance(message, AssistantMessage):
            # Text arrived as deltas already; only tool calls are new information here.
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    self._close()
                    self._write(f"[{self.role}] tool {block.name} {_short(block.input)}\n")
        elif isinstance(message, ResultMessage):
            self._close()
            cost = message.total_cost_usd or 0.0
            self._write(f"[{self.role}] done: {message.num_turns} turns, ${cost:.4f}\n")

    def _event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block", {}).get("type")
            if block in ("text", "thinking"):
                self._close()
                self._open = block
                label = " thinking" if block == "thinking" else ""
                self._write(f"[{self.role}]{label}:\n")
        elif kind == "content_block_delta":
            delta = event.get("delta", {})
            text = delta.get("text") if delta.get("type") == "text_delta" else None
            if delta.get("type") == "thinking_delta":
                text = delta.get("thinking")
            if text:
                self._write(text)
        elif kind == "content_block_stop":
            self._close()

    def _close(self) -> None:
        if self._open:
            self._write("\n")
            self._open = None

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()


def _short(data: object, limit: int = 160) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"
