"""Spans, propagation and system metrics. No exporter: in-memory providers stand in."""

import re

import pytest
from opentelemetry.sdk.metrics.export import MetricReader, MetricsData
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


class BatchReader(MetricReader):
    """Keeps every export as its own batch: a `finish` flushes, so one event is one batch."""

    def __init__(self):
        super().__init__()
        self.batches: list[MetricsData] = []

    def _receive_metrics(self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kw):
        self.batches.append(metrics_data)

    def shutdown(self, timeout_millis: float = 30_000, **kw):
        pass


@pytest.fixture
def metrics(monkeypatch):
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "abc1234")
    monkeypatch.setattr(telemetry, "_version", None)
    reader = BatchReader()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(reader))
    yield reader
    telemetry.configure()


def points(reader: BatchReader) -> dict[str, list]:
    """{metric name: [data points]} of everything exported so far."""
    out: dict[str, list] = {}
    for data in reader.batches:
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
    assert cost.value == 0.5 and dict(cost.attributes) == labels
    (rework,) = got["agentic.task.rework_usd"]
    assert rework.value == 0.15
    (finished,) = got["agentic.task.finished"]
    assert finished.value == 1
    (returns,) = got["agentic.task.returns"]
    assert returns.value == 1
    (duration,) = got["agentic.task.duration"]
    assert duration.value >= 0
    # The finish exported on its own: the sample left the process with the event.
    assert len(metrics.batches) == 1


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
    assert cost.value == 0.01
    assert dict(cost.attributes) == {"role": "PjM", "model": "m", "rework": False}
    (duration,) = got["agentic.role.duration"]
    assert dict(duration.attributes) == {"role": "PjM", "role.outcome": "delivered"}


def test_two_runs_with_the_same_labels_are_two_samples(metrics):
    """Metrics are events: a second run never overwrites the first, and a quiet
    period exports nothing (a gauge only carries what was set since the last export)."""
    patch = {
        "runs": [
            {
                "role": "PM",
                "version": 1,
                "session_id": "",
                "model": "m",
                "cost_usd": 0.1,
                "turns": 1,
            }
        ],
        "cost_usd": 0.1,
    }
    for _ in range(2):
        with telemetry.role_span("PM") as run:
            run.finish(patch)
    telemetry.flush()
    costs = points(metrics)["agentic.role.cost_usd"]
    assert [c.value for c in costs] == [0.1, 0.1]
    # Two events, two exports; the idle flush at the end exported nothing at all.
    assert len(metrics.batches) == 2


def test_parallel_runs_with_the_same_labels_are_two_samples(metrics):
    """Developers run in parallel (ADR-0004) and finish on different threads. A gauge
    keeps only the last value set before an export, so set-and-export is atomic."""
    import threading

    patch = {
        "runs": [
            {
                "role": "Developer",
                "version": 1,
                "session_id": "",
                "model": "m",
                "cost_usd": 0.3,
                "turns": 1,
            }
        ],
        "cost_usd": 0.3,
    }
    gate = threading.Barrier(2)

    def finish_one():
        with telemetry.role_span("Developer") as run:
            gate.wait()
            run.finish(patch)

    threads = [threading.Thread(target=finish_one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [c.value for c in points(metrics)["agentic.role.cost_usd"]] == [0.3, 0.3]


def test_system_version_prefers_the_env_var(monkeypatch):
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "v-test")
    monkeypatch.setattr(telemetry, "_version", None)
    assert telemetry.system_version() == "v-test"


def test_system_version_from_git_is_a_short_sha(monkeypatch):
    monkeypatch.delenv("AGENTIC_SYSTEM_VERSION", raising=False)
    monkeypatch.setattr(telemetry, "_version", None)
    version = telemetry.system_version()
    assert re.match(r"^[0-9a-f]{7,}(\+dirty)?$", version) or version == "unknown"
