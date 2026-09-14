"""Spans and system metrics, one trace per task (ADR-0008).

`task_span` around the graph run and `role_span` around each role yield a `Run`;
`finish(patch)` on it stamps the run's outcome, cost and model on the span and emits
the system metrics of the Medición glossary (`core/measurement/CONTEXT.md`).
`traceparent()` goes to the Claude Code subprocess, whose own spans then nest under
the role. Nothing is exported unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set.

The metrics are events, not rates (ADR-0008, addendum): every finished task and every
role run leaves one sample per metric, with its value. Gauges give exactly that, and a
flush after each `finish` guarantees two runs with the same labels never collapse into
one sample. Prometheus reads them with `sum_over_time` and `count_over_time`, exact from
the first task of a version; `increase()` never counts the first sample of a series.

Telemetry is cross-cutting: the graph adapter opens the spans and finishes them with
the patch each use case returns, the SDK adapter propagates the context; the use
cases never see it (ADR-0007). The collector decides where each signal goes.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from agentic.core.process.domain.task import TaskState

from ...domain import metrics as system_metrics

SERVICE_NAME = "agentic-system"
# agentic/core/measurement/adapters/otel/telemetry.py -> the system's repo, for its git version.
REPO = Path(__file__).resolve().parents[5]

# The API still exports the synchronous gauge under a private name.
Gauge = metrics._Gauge
_EMIT_LOCK = threading.Lock()

_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_configured = False
_instruments: Instruments | None = None
_version: str | None = None


def resource() -> Resource:
    return Resource.create({"service.name": SERVICE_NAME, "service.version": system_version()})


def system_version() -> str:
    """Short git SHA of this repo, `+dirty` when the tree has uncommitted changes, so a
    run with edited prompts never passes for the committed version. `AGENTIC_SYSTEM_VERSION`
    overrides it; `unknown` when git is not there."""
    global _version
    if _version is None:
        _version = os.environ.get("AGENTIC_SYSTEM_VERSION") or _git_version()
    return _version


def _git_version() -> str:
    try:
        git = ["git", "-C", str(REPO)]
        sha = subprocess.run(
            [*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            [*git, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}+dirty" if dirty else sha


def meter_provider_for(reader: MetricReader) -> MeterProvider:
    """A meter provider with the system's resource; tests pass their own reader."""
    return MeterProvider(metric_readers=[reader], resource=resource())


def configure(
    tracer_provider: TracerProvider | None = None, meter_provider: MeterProvider | None = None
) -> None:
    """Install the providers. Called once, lazily, from the endpoint env var; tests pass
    their own providers instead (either one; the other stays a no-op)."""
    global _tracer_provider, _meter_provider, _configured, _instruments
    _configured = True
    _instruments = None
    if (
        tracer_provider is None
        and meter_provider is None
        and os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    ):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider = TracerProvider(resource=resource())
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        meter_provider = meter_provider_for(PeriodicExportingMetricReader(metric_exporter()))
    _tracer_provider = tracer_provider
    _meter_provider = meter_provider


def metric_exporter() -> MetricExporter:
    """OTLP over HTTP. Gauges have no temporality: each `agentic run` is a new process,
    and a gauge sample carries its own value, so nothing accumulates across runs and the
    collector has nothing to convert."""
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    return OTLPMetricExporter()


def tracer() -> trace.Tracer:
    if not _configured:
        configure()
    if _tracer_provider is None:
        return trace.get_tracer("agentic")
    return _tracer_provider.get_tracer("agentic")


def meter() -> metrics.Meter:
    if not _configured:
        configure()
    if _meter_provider is None:
        return metrics.get_meter("agentic")
    return _meter_provider.get_meter("agentic")


@dataclass
class Instruments:
    """The system metrics, created once per meter, one gauge each. Prometheus names
    are these with underscores and the unit: `agentic.task.cost_usd` lands as
    `agentic_task_cost_usd`, `agentic.task.duration` as `agentic_task_duration_seconds`."""

    task_cost: Gauge
    task_rework: Gauge
    task_duration: Gauge
    task_finished: Gauge
    task_returns: Gauge
    role_cost: Gauge
    role_duration: Gauge

    @classmethod
    def create(cls, m: metrics.Meter) -> Instruments:
        return cls(
            task_cost=m.create_gauge("agentic.task.cost_usd", description="Cost of a task"),
            task_rework=m.create_gauge(
                "agentic.task.rework_usd", description="Cost of the task's rework"
            ),
            task_duration=m.create_gauge(
                "agentic.task.duration", unit="s", description="Wall time of a task"
            ),
            task_finished=m.create_gauge(
                "agentic.task.finished", description="One per task that reached an end, by status"
            ),
            task_returns=m.create_gauge(
                "agentic.task.returns", description="Returns of a task, across every pair"
            ),
            role_cost=m.create_gauge("agentic.role.cost_usd", description="Cost of one role run"),
            role_duration=m.create_gauge(
                "agentic.role.duration", unit="s", description="Wall time of one role run"
            ),
        )


def instruments() -> Instruments:
    global _instruments
    if _instruments is None:
        _instruments = Instruments.create(meter())
    return _instruments


@dataclass
class Run:
    """A span plus its clock. `finish` is called by the adapter that has the patch."""

    span: trace.Span
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _emit(self, record: Callable[[Instruments], None]) -> None:
        """One event, one export, atomically. Parallel Developers (ADR-0004) finish on
        different threads with the same labels, and a gauge keeps only the last value
        set before an export: without the lock, A.set, B.set, A.flush loses B."""
        with _EMIT_LOCK:
            record(instruments())
            _flush_metrics()


@dataclass
class TaskRun(Run):
    def finish(self, final: TaskState) -> None:
        cost = final.get("cost_usd", 0.0)
        rework = system_metrics.rework_cost_usd(final)
        returns = system_metrics.returns_total(final)
        labels = {
            "task.type": final.get("type", "unknown"),
            "task.status": final.get("status", "open"),
            "system.version": system_version(),
            "prompts.fingerprint": system_metrics.prompts_fingerprint(final),
        }
        self.span.set_attributes(
            {
                **labels,
                "task.cost_usd": cost,
                "task.rework_usd": rework,
                "task.returns": returns,
                "task.failures": len(final.get("failures", [])),
                "task.runs": len(final.get("runs", [])),
                "task.escalated": bool(final.get("escalated")),
            }
        )
        elapsed = self.elapsed

        def record(i: Instruments) -> None:
            i.task_cost.set(cost, labels)
            i.task_rework.set(rework, labels)
            i.task_duration.set(elapsed, labels)
            i.task_finished.set(1, labels)
            i.task_returns.set(returns, labels)

        self._emit(record)


@dataclass
class RoleRun(Run):
    role: str = ""

    def finish(self, patch: TaskState) -> None:
        runs = patch.get("runs", [])
        run = runs[-1] if runs else None
        failed_attempts = len(patch.get("failures", []))
        cost = patch.get("cost_usd", 0.0)
        outcome = _outcome(patch, delivered=run is not None)
        rework = failed_attempts > 0 or (run is not None and system_metrics.is_rework(run))
        attributes: dict[str, str | int | float | bool] = {
            "role.outcome": outcome,
            "role.cost_usd": cost,
            "role.failed_attempts": failed_attempts,
            "role.rework": rework,
        }
        if run is not None:
            attributes |= {
                "role.version": run["version"],
                "role.model": run["model"],
                "role.turns": run["turns"],
                "role.session_id": run["session_id"],
            }
        for prompt_hash in patch.get("prompts", {}).values():
            attributes["role.prompt_hash"] = prompt_hash
        self.span.set_attributes(attributes)
        elapsed = self.elapsed
        model = run["model"] if run else ""

        def record(i: Instruments) -> None:
            i.role_duration.set(elapsed, {"role": self.role, "role.outcome": outcome})
            i.role_cost.set(cost, {"role": self.role, "model": model, "rework": rework})

        self._emit(record)


def _flush_metrics() -> None:
    """Export what was set since the last export; `Run._emit` calls it per event."""
    if _meter_provider is not None:
        _meter_provider.force_flush()


def _outcome(patch: TaskState, *, delivered: bool) -> str:
    """failed: no delivery (the role broke). escalated: delivered, but the use case
    handed the task to the human. returned: a validator sent it back. delivered: the rest."""
    if not delivered:
        return "failed"
    if patch.get("escalated"):
        return "escalated"
    if patch.get("status") == "returned":
        return "returned"
    return "delivered"


@contextmanager
def task_span(task_id: str, title: str) -> Iterator[TaskRun]:
    """The root of the task's trace; its name is what Langfuse lists. The title stays
    local: the collector strips it before Grafana Cloud."""
    with tracer().start_as_current_span(
        task_id, attributes={"task.id": task_id, "task.title": title}
    ) as span:
        yield TaskRun(span)


@contextmanager
def role_span(role_name: str) -> Iterator[RoleRun]:
    with tracer().start_as_current_span(role_name, attributes={"role": role_name}) as span:
        yield RoleRun(span, role=role_name)


def traceparent() -> str | None:
    """The W3C header for the current span, or None when there is no recording span."""
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def flush() -> None:
    """Send what is still buffered; call before the process exits."""
    if _tracer_provider is not None:
        _tracer_provider.force_flush()
    _flush_metrics()
