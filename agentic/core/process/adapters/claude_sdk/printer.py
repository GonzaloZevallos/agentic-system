"""Watching a role generate: what `agentic run --verbose` shows.

`Printer` writes the SDK stream of one role to a terminal as it happens: text and
thinking deltas, tool calls, and the closing cost line, each prefixed with the role
name. It consumes SDK messages, so it lives with the SDK adapter.
"""

from __future__ import annotations

import json
import sys
from typing import IO

from claude_agent_sdk import AssistantMessage, Message, ResultMessage, StreamEvent, ToolUseBlock

STRUCTURED_OUTPUT = "StructuredOutput"
_UNESCAPE = (("\\n", "\n"), ('\\"', '"'), ("\\\\", "\\"))


class Printer:
    """Callable over the stream. Needs partial messages to show generation.

    A role's delivery is the input of the `StructuredOutput` tool, streamed as JSON
    deltas: that is what gets shown live, lightly unescaped. Thinking arrives with no
    content (signature only) unless the model exposes it, so its label waits for text.
    """

    def __init__(self, role_name: str, stream: IO[str] | None = None):
        self.role = role_name
        self.stream = stream or sys.stderr
        self._open: str | None = None  # kind of the content block being streamed
        self._labelled = False

    def __call__(self, message: Message) -> None:
        if isinstance(message, StreamEvent):
            self._event(message.event)
        elif isinstance(message, AssistantMessage):
            # Text and the delivery arrived as deltas; other tool calls are new here.
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.name != STRUCTURED_OUTPUT:
                    self._close()
                    self._write(f"[{self.role}] tool {block.name} {_short(block.input)}\n")
        elif isinstance(message, ResultMessage):
            self._close()
            cost = message.total_cost_usd or 0.0
            self._write(f"[{self.role}] done: {message.num_turns} turns, ${cost:.4f}\n")

    def _event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block", {})
            self._close()
            if block.get("type") in ("text", "thinking"):
                self._open = block["type"]
            elif block.get("type") == "tool_use" and block.get("name") == STRUCTURED_OUTPUT:
                self._open = "delivery"
        elif kind == "content_block_delta":
            delta = event.get("delta", {})
            text = {
                "text_delta": delta.get("text"),
                "thinking_delta": delta.get("thinking"),
                "input_json_delta": _unescape(delta.get("partial_json", "")),
            }.get(delta.get("type"))
            if text and self._open:
                if not self._labelled:
                    label = {"text": "", "thinking": " thinking", "delivery": " delivery"}
                    self._write(f"[{self.role}]{label[self._open]}:\n")
                    self._labelled = True
                self._write(text)
        elif kind == "content_block_stop":
            self._close()

    def _close(self) -> None:
        if self._open and self._labelled:
            self._write("\n")
        self._open, self._labelled = None, False

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()


def _unescape(partial_json: str) -> str:
    # Enough to read a Markdown string as it streams; a chunk split mid-escape stays raw.
    for escaped, plain in _UNESCAPE:
        partial_json = partial_json.replace(escaped, plain)
    return partial_json


def _short(data: object, limit: int = 160) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"
