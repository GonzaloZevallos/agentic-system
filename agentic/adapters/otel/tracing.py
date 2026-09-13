"""Spans that make a task one trace in Langfuse.

`task_span` around the graph run, `role_span` around each role, and `traceparent()`
handed to the Claude Code subprocess, whose own OpenTelemetry spans then nest under
the role. Nothing is exported unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Tracing
is cross-cutting: the graph adapter opens the spans and the SDK adapter propagates
them; the use cases never see it (ADR-0007).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

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
