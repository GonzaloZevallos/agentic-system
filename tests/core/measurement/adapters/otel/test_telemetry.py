"""Spans, propagation and system metrics. No exporter: in-memory providers stand in."""

import re

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentic.core.measurement.adapters.otel import telemetry

TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


@pytest.fixture
def spans(monkeypatch):
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "abc1234")
    monkeypatch.setattr(telemetry, "_version", None)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    yield exporter
    telemetry.configure()


@pytest.fixture
def metrics(monkeypatch):
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "abc1234")
    monkeypatch.setattr(telemetry, "_version", None)
    reader = InMemoryMetricReader()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(reader))
    yield reader
    telemetry.configure()


def points(reader: InMemoryMetricReader) -> dict[str, list]:
    """{metric name: [data points]} of everything collected so far."""
    data = reader.get_metrics_data()
    out: dict[str, list] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out.setdefault(metric.name, []).extend(metric.data.data_points)
    return out


def test_no_provider_no_traceparent():
    telemetry.configure(tracer_provider=None)
    assert telemetry.traceparent() is None
    with telemetry.task_span("T-1", "t"), telemetry.role_span("PjM"):
        assert telemetry.traceparent() is None  # nothing configured: nothing to propagate


def test_role_span_nests_under_the_task_and_propagates(spans):
    with telemetry.task_span("GH-12", "Metrics") as task:
        with telemetry.role_span("PjM") as role:
            header = telemetry.traceparent()
    assert TRACEPARENT.match(header)
    trace_id, span_id = header.split("-")[1:3]
    assert int(trace_id, 16) == task.span.get_span_context().trace_id
    assert int(span_id, 16) == role.span.get_span_context().span_id
    assert role.span.parent.span_id == task.span.get_span_context().span_id
    assert task.span.name == "GH-12" and role.span.attributes["role"] == "PjM"


def test_configure_without_endpoint_is_a_noop(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    telemetry.configure()
    assert telemetry._tracer_provider is None and telemetry._meter_provider is None


def test_role_finish_stamps_the_run_on_the_span(spans):
    patch = {
        "runs": [
            {
                "role": "PM",
                "version": 2,
                "session_id": "s-1",
                "model": "claude-opus-5",
                "cost_usd": 0.2,
                "turns": 3,
            }
        ],
        "failures": [],
        "cost_usd": 0.2,
        "status": "returned",
        "prompts": {"pm": "deadbeef"},
    }
    with telemetry.role_span("PM") as run:
        run.finish(patch)
    (span,) = spans.get_finished_spans()
    assert span.attributes["role.outcome"] == "returned"
    assert span.attributes["role.version"] == 2 and span.attributes["role.rework"] is True
    assert span.attributes["role.model"] == "claude-opus-5"
    assert span.attributes["role.session_id"] == "s-1"
    assert span.attributes["role.prompt_hash"] == "deadbeef"
    assert span.attributes["role.failed_attempts"] == 0


def test_role_finish_on_a_broken_role(spans):
    patch = {
        "failures": [{"role": "PA", "attempt": 1, "error": "e", "cost_usd": 0.05}],
        "cost_usd": 0.05,
        "escalated": "PA failed",
        "status": "waiting_on_human",
    }
    with telemetry.role_span("Product Analyst") as run:
        run.finish(patch)
    (span,) = spans.get_finished_spans()
    assert span.attributes["role.outcome"] == "failed"
    assert span.attributes["role.rework"] is True and "role.model" not in span.attributes


def test_task_finish_emits_the_system_metrics(metrics):
    final = {
        "type": "feature",
        "status": "definition_ready",
        "cost_usd": 0.5,
        "returns": {"PM→Product Analyst": 1},
        "failures": [],
        "runs": [
            {
                "role": "PjM",
                "version": 1,
                "session_id": "",
                "model": "m",
                "cost_usd": 0.1,
                "turns": 1,
            },
            {
                "role": "PM",
                "version": 2,
                "session_id": "",
                "model": "m",
                "cost_usd": 0.15,
                "turns": 1,
            },
        ],
        "prompts": {"pjm": "a", "pm": "b"},
    }
    with telemetry.task_span("T-1", "t") as run:
        run.finish(final)
    got = points(metrics)
    labels = {
        "task.type": "feature",
        "task.status": "definition_ready",
        "system.version": "abc1234",
        "prompts.fingerprint": telemetry.system_metrics.prompts_fingerprint(final),
    }
    (cost,) = got["agentic.task.cost_usd"]
    assert cost.sum == 0.5 and cost.count == 1 and dict(cost.attributes) == labels
    (rework,) = got["agentic.task.rework_usd"]
    assert rework.sum == 0.15
    assert got["agentic.task.finished"][0].value == 1
    assert got["agentic.task.returns"][0].value == 1
    (duration,) = got["agentic.task.duration"]
    assert duration.count == 1 and list(duration.explicit_bounds) == list(
        telemetry.DURATION_BUCKETS
    )
    assert list(cost.explicit_bounds) == list(telemetry.COST_BUCKETS)


def test_role_finish_emits_duration_and_cost_by_role(metrics):
    with telemetry.role_span("PjM") as run:
        run.finish(
            {
                "runs": [
                    {
                        "role": "PjM",
                        "version": 1,
                        "session_id": "",
                        "model": "m",
                        "cost_usd": 0.01,
                        "turns": 1,
                    }
                ],
                "cost_usd": 0.01,
            }
        )
    got = points(metrics)
    (cost,) = got["agentic.role.cost_usd"]
    assert cost.sum == 0.01
    assert dict(cost.attributes) == {"role": "PjM", "model": "m", "rework": False}
    (duration,) = got["agentic.role.duration"]
    assert dict(duration.attributes) == {"role": "PjM", "role.outcome": "delivered"}


def test_metrics_leave_the_process_as_deltas():
    from opentelemetry.sdk.metrics import Counter, Histogram
    from opentelemetry.sdk.metrics.export import AggregationTemporality

    exporter = telemetry.metric_exporter()
    assert exporter._preferred_temporality[Counter] is AggregationTemporality.DELTA
    assert exporter._preferred_temporality[Histogram] is AggregationTemporality.DELTA


def test_system_version_prefers_the_env_var(monkeypatch):
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "v-test")
    monkeypatch.setattr(telemetry, "_version", None)
    assert telemetry.system_version() == "v-test"


def test_system_version_from_git_is_a_short_sha(monkeypatch):
    monkeypatch.delenv("AGENTIC_SYSTEM_VERSION", raising=False)
    monkeypatch.setattr(telemetry, "_version", None)
    version = telemetry.system_version()
    assert re.match(r"^[0-9a-f]{7,}(\+dirty)?$", version) or version == "unknown"
