"""Spans and trace propagation. No exporter: an in-memory provider stands in."""

import re

import pytest
from opentelemetry.sdk.trace import TracerProvider

from agentic.adapters.otel import tracing

TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


@pytest.fixture
def provider(monkeypatch):
    provider = TracerProvider()
    monkeypatch.setattr(tracing, "_provider", provider)
    monkeypatch.setattr(tracing, "_configured", True)
    return provider


def test_no_span_no_traceparent(monkeypatch):
    monkeypatch.setattr(tracing, "_provider", None)
    monkeypatch.setattr(tracing, "_configured", True)
    assert tracing.traceparent() is None
    with tracing.task_span("T-1", "t"), tracing.role_span("PjM"):
        assert tracing.traceparent() is None  # nothing configured: nothing to propagate


def test_role_span_nests_under_the_task_and_propagates(provider):
    with tracing.task_span("GH-12", "Metrics") as task:
        with tracing.role_span("PjM") as role:
            header = tracing.traceparent()
    assert TRACEPARENT.match(header)
    trace_id, span_id = header.split("-")[1:3]
    assert int(trace_id, 16) == task.get_span_context().trace_id
    assert int(span_id, 16) == role.get_span_context().span_id
    assert role.parent.span_id == task.get_span_context().span_id
    assert task.name == "GH-12" and role.attributes["role"] == "PjM"


def test_configure_without_endpoint_is_a_noop(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(tracing, "_provider", None)
    monkeypatch.setattr(tracing, "_configured", False)
    tracing.tracer()
    assert tracing._provider is None
