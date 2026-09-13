"""Watching a role work.

`Printer` writes the SDK stream of one role to a terminal as it happens: text and
thinking deltas, tool calls, and the closing cost line, each prefixed with the role
name. It is what `agentic run --verbose` shows.

The spans make a task one trace in Langfuse: `task_span` around the graph run,
`role_span` around each role, and `traceparent()` handed to the Claude Code
subprocess, whose own OpenTelemetry spans then nest under the role. Nothing is
exported unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set (DISENO.md, 14).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO

from claude_agent_sdk import AssistantMessage, Message, ResultMessage, StreamEvent, ToolUseBlock
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_provider: TracerProvider | None = None
_configured = False


def configure(provider: TracerProvider | None = None) -> None:
    """Install a tracer provider. Called once, lazily, from the endpoint env var;
    tests pass their own provider instead."""
    global _provider, _configured
    _configured = True
    if provider is None and os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(resource=Resource.create({"service.name": "agentic-system"}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _provider = provider


def tracer() -> trace.Tracer:
    if not _configured:
        configure()
    return _provider.get_tracer("agentic") if _provider else trace.get_tracer("agentic")


@contextmanager
def task_span(task_id: str, title: str) -> Iterator[trace.Span]:
    """The root of the task's trace; its name is what Langfuse lists."""
    with tracer().start_as_current_span(
        task_id, attributes={"task.id": task_id, "task.title": title}
    ) as span:
        yield span


@contextmanager
def role_span(role_name: str) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(role_name, attributes={"role": role_name}) as span:
        yield span


def traceparent() -> str | None:
    """The W3C header for the current span, or None when there is no recording span."""
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def flush() -> None:
    """Send what is still buffered; call before the process exits."""
    if _provider is not None:
        _provider.force_flush()


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
