"""Spans and system metrics, one trace per task (ADR-0008).

`task_span` around the graph run and `role_span` around each role yield a `Run`;
`finish(patch)` on it stamps the run's outcome, cost and model on the span and emits
the system metrics of the Medición glossary (`core/measurement/CONTEXT.md`).
`traceparent()` goes to the Claude Code subprocess, whose own spans then nest under
the role. Nothing is exported unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set.

Every span and every metric carries the `product` the run worked on, handed in by the
caller, because since ADR-0017 there are two and a panel that adds them up compares a
gym SaaS with an agentic system (issue #47). Which name a product goes by is the
product's own declaration, `.agentic/stack.yaml`; this adapter only stamps it.

The metrics are events, not rates (ADR-0008, addendum): every finished task and every
role run leaves one sample per metric, with its value; `agentic.task.returns` leaves one
per category of return (ADR-0016). Gauges give exactly that, and a
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
from datetime import datetime

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

from agentic.core.process.domain.rules import phase_of
from agentic.core.process.domain.task import Failure, TaskState
from agentic.core.process.domain.task import RoleRun as RoleRunRecord
from agentic.shared.stage0 import CHECKOUT

from ...domain import metrics as system_metrics
from . import semconv

SERVICE_NAME = "agentic-system"
# The checkout that executes this run, and whose sha the version names. With the system
# as a product, the copy being edited is another one (`shared.stage0`; ADR-0017).
REPO = CHECKOUT

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
    """Short git SHA of the checkout this run executes from, `+dirty` when its tree has
    uncommitted changes, so a run with edited prompts never passes for the committed
    version. Running from stage0 that tree is never the one a task edits, so the sha is
    a real one whatever the product is doing (ADR-0017). `AGENTIC_SYSTEM_VERSION`
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
    role_run_duration: Gauge
    role_queue_wait: Gauge
    role_failures: Gauge
    role_failure_cost: Gauge
    review_findings: Gauge
    presubmit_checks: Gauge
    presubmit_duration: Gauge

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
                "agentic.task.returns",
                description="Returns of a task across every pair, one sample per category",
            ),
            role_cost=m.create_gauge("agentic.role.cost_usd", description="Cost of one role run"),
            role_duration=m.create_gauge(
                "agentic.role.duration", unit="s", description="Wall time of one role run"
            ),
            role_run_duration=m.create_gauge(
                "agentic.role.run_duration",
                unit="s",
                description="Wall time of one run record inside its concurrency slot",
            ),
            role_queue_wait=m.create_gauge(
                "agentic.role.queue_wait",
                unit="s",
                description="Seconds one run record waited for a concurrency slot",
            ),
            role_failures=m.create_gauge(
                "agentic.role.failures",
                description="Failed attempts of a patch, one sample per reason with the count",
            ),
            role_failure_cost=m.create_gauge(
                "agentic.role.failure_cost_usd",
                description=(
                    "Cost of a patch's failed attempts by reason; not additive with "
                    "agentic.role.cost_usd, whose runs and residual already carry that money"
                ),
            ),
            review_findings=m.create_gauge(
                "agentic.review.findings",
                description=(
                    "Findings of one review, one sample per (severity, category, status) "
                    "with the count of findings that share it as the value"
                ),
            ),
            presubmit_checks=m.create_gauge(
                "agentic.presubmit.checks",
                description=(
                    "One sample per check of one presubmit invocation; a Developer run may "
                    "be refused and try again, so only the last invocation is `final`"
                ),
            ),
            presubmit_duration=m.create_gauge(
                "agentic.presubmit.duration",
                unit="s",
                description="How long one check took to run, in one presubmit invocation",
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
    # The product this run worked on. Not a default: a sample with no product is a sample
    # that adds up with the other product's, which is the bug (issue #47).
    product: str = ""
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
        # The series keeps the old name on purpose: renaming it would cut every panel
        # in two at this commit. `agentic.task.returns`, the `return.category` label,
        # `role.return_categories` and the `returned` outcome are data already written,
        # so they do not follow the domain's rename (ADR-0008, addendum; ADR-0018).
        requested = system_metrics.changes_requested_total(final)
        by_category = system_metrics.changes_by_category(final)
        labels = {
            "product": self.product,
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
                "task.returns": requested,
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
            for category, count in by_category.items():
                i.task_returns.set(count, {**labels, "return.category": category})

        self._emit(record)


@dataclass
class RoleRun(Run):
    role: str = ""
    # `Role.slug` of the node this span represents. Unlike a stored run's own `slug`
    # (which can be missing on a document from before that field existed, and reads
    # `unknown`), this one names the role that is running right now, so it labels the
    # residual sample, which has no run record of its own to read a slug off.
    slug: str = ""

    def finish(self, patch: TaskState) -> None:
        """Stamps the span (unchanged) and emits `agentic.role.duration` once per node,
        byte-identical to before this task. Every other sample here is new: one
        `agentic.role.cost_usd` (plus `.run_duration`, `.queue_wait`) and one child span
        per run record in the patch, then the residual — the cost no run record
        carries — and the patch's failures by reason. Each of those is its own
        record-and-flush (`Run._emit`): two run records sharing every label but their
        session would otherwise collapse into one sample on a gauge that only keeps the
        last value set before an export."""
        runs = patch.get("runs", [])
        run = runs[-1] if runs else None
        failed_attempts = len(patch.get("failures", []))
        cost = patch.get("cost_usd", 0.0)
        outcome = _outcome(patch, delivered=run is not None)
        rework = failed_attempts > 0 or (run is not None and system_metrics.is_rework(run))
        attributes: dict[str, str | int | float | bool | tuple[str, ...]] = {
            "product": self.product,
            "role.outcome": outcome,
            "agentic.role.cost_usd": cost,
            "agentic.role.failed_attempts": failed_attempts,
            "agentic.role.rework": rework,
        }
        if run is not None:
            attributes |= {
                "agentic.role.version": run["version"],
                "agentic.role.model": run["model"],
                "agentic.role.turns": run["turns"],
                "agentic.role.session_id": run["session_id"],
            }
        # A patch may run more than one role (an interview): the tuple of every prompt
        # that ran, not the last one written, which used to overwrite the others.
        if prompt_hashes := tuple(patch.get("prompts", {}).values()):
            attributes["agentic.role.prompt_hash"] = prompt_hashes
        if categories := system_metrics.change_categories(patch):
            attributes["agentic.role.return_categories"] = categories
        self.span.set_attributes(attributes)
        elapsed = self.elapsed

        def record_duration(i: Instruments) -> None:
            i.role_duration.set(
                elapsed, {"product": self.product, "role": self.role, "role.outcome": outcome}
            )

        self._emit(record_duration)

        for one_run in runs:
            self._child_span(patch, one_run)
            self._emit_run(patch, one_run)
        if system_metrics.has_residual_sample(patch):
            self._emit_residual(patch)
        if failures := patch.get("failures", []):
            self._emit_failures(failures)
        if patch.get("findings"):
            self._emit_findings(patch)
        if patch.get("presubmit_checks"):
            self._emit_presubmit(patch)

    def _run_labels(self, patch: TaskState, run: RoleRunRecord) -> dict[str, str | bool]:
        return {
            "product": self.product,
            "role": run["role"],
            "model": run["model"],
            "rework": system_metrics.is_rework(run),
            "role.slug": system_metrics.role_slug(run),
            "phase": phase_of(run.get("section", run["role"])),
            "role.outcome": system_metrics.role_run_outcome(patch, run),
            "system.version": system_version(),
        }

    def _emit_run(self, patch: TaskState, run: RoleRunRecord) -> None:
        labels = self._run_labels(patch, run)
        duration = system_metrics.run_duration_s(run)
        queue_wait = run.get("queue_wait_s")

        def record(i: Instruments) -> None:
            i.role_cost.set(run["cost_usd"], labels)
            if duration is not None:
                i.role_run_duration.set(duration, labels)
            if queue_wait is not None:
                i.role_queue_wait.set(queue_wait, labels)

        self._emit(record)

    def _emit_residual(self, patch: TaskState) -> None:
        """The cost no run record carries: an escalation's failed attempts, or an
        empty patch, at value zero so the panel never silently misses the node. No
        `model`, since no run backs it; `phase` only when a run in the patch did
        deliver (`target_run`) and this residual is a separate fact about the same
        patch — there is no section to derive one from otherwise."""
        labels: dict[str, str | bool] = {
            "product": self.product,
            "role": self.role,
            "model": "",
            "rework": system_metrics.residual_rework(patch),
            "role.slug": self.slug or "unknown",
            "role.outcome": system_metrics.residual_outcome(patch),
            "system.version": system_version(),
        }
        if target := system_metrics.target_run(patch):
            labels["phase"] = phase_of(target.get("section", target["role"]))
        value = system_metrics.residual_cost_usd(patch)

        def record(i: Instruments) -> None:
            i.role_cost.set(value, labels)

        self._emit(record)

    def _emit_failures(self, failures: list[Failure]) -> None:
        """`agentic.role.failures` and `.failure_cost_usd`, one sample per reason: every
        reason is its own label set, so both fit in one record-and-flush. `role.slug`
        and `phase` come from the failures themselves — the same role and roughly the
        same section every attempt of one `run_with_retry` call shares — rather than
        the node's own span, so a failure recorded with none (a document from before
        this task) still reads `unknown` instead of borrowing the span's identity."""
        common: dict[str, str] = {
            "product": self.product,
            "role": self.role,
            "role.slug": failures[0].get("slug") or "unknown",
        }
        if section := failures[0].get("section"):
            common["phase"] = phase_of(section)
        counts = system_metrics.failures_by_reason(failures)
        costs = system_metrics.failure_cost_by_reason(failures)

        def record(i: Instruments) -> None:
            for reason, count in counts.items():
                i.role_failures.set(count, {**common, "reason": reason})
            for reason, total in costs.items():
                i.role_failure_cost.set(total, {**common, "reason": reason})

        self._emit(record)

    def _emit_findings(self, patch: TaskState) -> None:
        """`agentic.review.findings`, one sample per (severity, category, status) of
        this review's own findings, the count as value: several findings landing in the
        same cell at the same review are one fact told once, not one `.set()` per
        finding, which would overwrite itself before the flush. `review.kind` and
        `system.version` are the review's own, shared by every cell it leaves."""
        common = {
            "product": self.product,
            "review.kind": patch.get("review_kind", "unknown"),
            "system.version": system_version(),
        }
        counts: dict[tuple[str, str, str], int] = {}
        for item in patch["findings"]:
            key = (item["severity"], item["category"], item["status"])
            counts[key] = counts.get(key, 0) + 1

        def record(i: Instruments) -> None:
            for (severity, category, status), count in counts.items():
                i.review_findings.set(
                    count,
                    {**common, "severity": severity, "category": category, "status": status},
                )

        self._emit(record)

    def _emit_presubmit(self, patch: TaskState) -> None:
        """`agentic.presubmit.checks` and `.duration`, one record-and-flush per
        invocation of the Developer's presubmit that actually ran the checks: a
        delivery refused once or twice tries again, and each try is its own fact, not a
        count folded into the next one. Only the last invocation — the one that became
        the delivery, or the last the backstop let through — is `final`."""
        invocations = patch.get("presubmit_checks", [])
        last = len(invocations) - 1
        common = {"product": self.product}
        for index, results in enumerate(invocations):
            final = index == last

            def record(i: Instruments, results: list = results, final: bool = final) -> None:
                for check in results:
                    labels = {
                        **common,
                        "check": check["name"],
                        "result": "green" if check["passed"] else "red",
                        "final": final,
                    }
                    i.presubmit_checks.set(1, labels)
                    i.presubmit_duration.set(check.get("duration_s", 0.0), labels)

            self._emit(record)

    def _child_span(self, patch: TaskState, run: RoleRunRecord) -> None:
        """One span per run record, under the role span, carrying that run's own agent,
        model, session and prompt hash — the shape ADR-0008's semconv addendum defined
        for a role run, applied per run instead of once for a patch that may hold
        several (an interview's rounds, each a different role)."""
        prompt_hash = patch.get("prompts", {}).get(run.get("slug", ""), "")
        attributes: dict[str, str | float] = {
            semconv.OPERATION_NAME: semconv.INVOKE_AGENT,
            semconv.AGENT_NAME: run["role"],
            semconv.AGENT_ID: system_metrics.role_slug(run),
            semconv.PROVIDER_NAME: semconv.ANTHROPIC,
            semconv.SYSTEM: semconv.ANTHROPIC,
            "role": run["role"],
            "product": self.product,
            "agentic.role.model": run["model"],
            "agentic.role.session_id": run["session_id"],
            "agentic.role.cost_usd": run["cost_usd"],
        }
        if prompt_hash:
            attributes["agentic.role.prompt_hash"] = prompt_hash
        started = run.get("started_at")
        span = tracer().start_span(
            semconv.span_name(run["role"]),
            context=trace.set_span_in_context(self.span),
            attributes=attributes,
            start_time=_epoch_ns(started) if started else None,
        )
        ended = run.get("ended_at")
        span.end(end_time=_epoch_ns(ended) if ended else None)


def _epoch_ns(iso: str) -> int:
    """`RoleRun.started_at`/`.ended_at`, ISO-8601, as nanoseconds since the epoch: the
    unit a span's own `start_time`/`end_time` take."""
    return int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000)


def _flush_metrics() -> None:
    """Export what was set since the last export; `Run._emit` calls it per event."""
    if _meter_provider is not None:
        _meter_provider.force_flush()


def _outcome(patch: TaskState, *, delivered: bool) -> str:
    """failed: no delivery (the role broke). escalated: delivered, but the use case
    handed the task to the human. returned: a verifier sent it back. delivered: the rest."""
    if not delivered:
        return "failed"
    if patch.get("escalated"):
        return "escalated"
    if patch.get("status") == "returned":
        return "returned"
    return "delivered"


@contextmanager
def task_span(task_id: str, title: str, product: str) -> Iterator[TaskRun]:
    """The root of the task's trace; its name is what Langfuse lists. The title stays
    local: the collector strips it before Grafana Cloud. `product` is on the span from
    the start, so a crashed run is still attributable to one of the two."""
    with tracer().start_as_current_span(
        task_id, attributes={"task.id": task_id, "task.title": title, "product": product}
    ) as span:
        yield TaskRun(span, product=product)


@contextmanager
def role_span(role_name: str, role_slug: str, product: str) -> Iterator[RoleRun]:
    """One role run, named and typed for the OpenTelemetry GenAI semantic conventions
    (ADR-0008, addendum): `invoke_agent <role_name>`, `gen_ai.operation.name` =
    `invoke_agent`, the role identified by `gen_ai.agent.name` (`Role.name`) and
    `gen_ai.agent.id` (`Role.slug`) verbatim, never re-derived (invariant 6). No
    `chat`-level attribute rides on it: a role run spends across models, so a single
    request or response model, or a token count, would be a false claim to a
    conforming reader (`semconv.py`).

    Langfuse also lists it as an agent run from `langfuse.observation.type`; its cost
    there is the sum of the Claude Code calls nested under it, priced by Langfuse from
    their tokens (ADR-0008, addendum). `agentic.role.cost_usd` is the system's own
    figure and stays an attribute, never a `gen_ai.*` key: sending it as cost too would
    count every dollar twice, and no dollar figure belongs under `gen_ai.*` regardless
    (invariant 5)."""
    with tracer().start_as_current_span(
        semconv.span_name(role_name),
        attributes={
            semconv.OPERATION_NAME: semconv.INVOKE_AGENT,
            semconv.AGENT_NAME: role_name,
            semconv.AGENT_ID: role_slug,
            semconv.PROVIDER_NAME: semconv.ANTHROPIC,
            semconv.SYSTEM: semconv.ANTHROPIC,
            "role": role_name,
            "product": product,
            "langfuse.observation.type": "agent",
        },
    ) as span:
        yield RoleRun(span, product=product, role=role_name, slug=role_slug)


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
