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
from typing import Literal

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

from agentic.core.process.domain.presubmit import CheckResult
from agentic.core.process.domain.rules import phase_of, run_ended
from agentic.core.process.domain.task import Failure, InterviewStats, TaskState, delta
from agentic.core.process.domain.task import RoleRun as RoleRunRecord
from agentic.shared.stage0 import CHECKOUT

from ...domain import metrics as system_metrics
from . import semconv

# One `agentic run`/`resume` call (glossary: Invocación), distinct from a Corrida (the
# whole execution over a task): `cli._invoke`'s only two shapes, and the closed set the
# `invocation.kind` label and `agentic.task.invocations` carry.
InvocationKind = Literal["run", "resume"]

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
    role_tokens: Gauge
    role_calls: Gauge
    role_turns: Gauge
    role_max_turns: Gauge
    role_tool_calls: Gauge
    role_subagent_cost: Gauge
    role_cold_fallback: Gauge
    review_findings: Gauge
    presubmit_checks: Gauge
    presubmit_duration: Gauge
    interview_rounds: Gauge
    interview_questions: Gauge
    interview_invariants: Gauge
    invocation_cost: Gauge
    invocation_rework: Gauge
    invocation_duration: Gauge
    invocation_returns: Gauge
    task_invocations: Gauge
    task_paused: Gauge
    task_human_decisions: Gauge
    delivery_outcome: Gauge
    delivery_human_commits: Gauge
    delivery_human_lines_changed: Gauge
    delivery_time_to_merge: Gauge

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
            role_tokens=m.create_gauge(
                "agentic.role.tokens",
                unit="tokens",
                description="Token usage of one run record, one sample per kind",
            ),
            role_calls=m.create_gauge(
                "agentic.role.calls", description="Calls to the model in one run record"
            ),
            role_turns=m.create_gauge(
                "agentic.role.turns", description="`num_turns` of one run record"
            ),
            role_max_turns=m.create_gauge(
                "agentic.role.max_turns",
                description=(
                    "The role's own turn cap when one run record ran, beside "
                    "agentic.role.turns with the same labels, so a calibration can read "
                    "turns spent against the budget in force"
                ),
            ),
            role_tool_calls=m.create_gauge(
                "agentic.role.tool_calls",
                description="Tool-use blocks of one run record, one sample per tool",
            ),
            role_subagent_cost=m.create_gauge(
                "agentic.role.subagent_cost_usd",
                description=(
                    "What of a run record's cost a sub-agent spent (ADR-0013); not "
                    "additive with agentic.role.cost_usd, whose run already carries "
                    "that money as part of its own total"
                ),
            ),
            role_cold_fallback=m.create_gauge(
                "agentic.role.cold_fallback",
                description=(
                    "One sample per role run where the system dropped a "
                    "caller-supplied session and started cold (ADR-0004, sixth "
                    "addendum); names the system's own decision, never a cause"
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
            interview_rounds=m.create_gauge(
                "agentic.interview.rounds",
                description="Question-answer cycles one interview ran",
            ),
            interview_questions=m.create_gauge(
                "agentic.interview.questions",
                description="Questions of one interview, one sample per destination",
            ),
            interview_invariants=m.create_gauge(
                "agentic.interview.invariants",
                description="Invariants of one interview, one sample per state",
            ),
            invocation_cost=m.create_gauge(
                "agentic.invocation.cost_usd",
                description="What one invocation (run or resume) spent, never the task's total",
            ),
            invocation_rework=m.create_gauge(
                "agentic.invocation.rework_usd",
                description="Rework this invocation paid for, never the task's total",
            ),
            invocation_duration=m.create_gauge(
                "agentic.invocation.duration",
                unit="s",
                description="Wall time of one invocation",
            ),
            invocation_returns=m.create_gauge(
                "agentic.invocation.returns",
                description="Returns this invocation recorded, one sample per category",
            ),
            task_invocations=m.create_gauge(
                "agentic.task.invocations",
                description="One per invocation (run or resume), by kind and the status it left",
            ),
            task_paused=m.create_gauge(
                "agentic.task.paused",
                description="One per pause, by the escalation's class",
            ),
            task_human_decisions=m.create_gauge(
                "agentic.task.human_decisions",
                description="One per decision the human gave on a resume",
            ),
            delivery_outcome=m.create_gauge(
                "agentic.delivery.outcome",
                description=(
                    "1, the human's verdict on the task's pull request, read by "
                    "`agentic close` (merged, merged_with_changes, closed)"
                ),
            ),
            delivery_human_commits=m.create_gauge(
                "agentic.delivery.human_commits",
                description=(
                    "Commits on the pull request that are the human's own, not the system's"
                ),
            ),
            delivery_human_lines_changed=m.create_gauge(
                "agentic.delivery.human_lines_changed",
                description=(
                    "additions + deletions of the pull request's human commits; 0 when "
                    "the human merged the delivery as it stood"
                ),
            ),
            delivery_time_to_merge=m.create_gauge(
                "agentic.delivery.time_to_merge",
                unit="s",
                description="Seconds from the pull request's own createdAt to its mergedAt",
            ),
        )


def instruments() -> Instruments:
    global _instruments
    if _instruments is None:
        _instruments = Instruments.create(meter())
    return _instruments


def _delivery_labels(
    product: str, task_type: str, prompts_fingerprint: str, system_version: str
) -> dict[str, str]:
    """The one label set every `agentic.delivery.*` sample carries: `product`,
    `system.version`, `prompts.fingerprint`, `task.type`. `agentic.delivery.outcome`
    adds its own `outcome` on top of this, in `record_delivery` below; no emission
    site spells this set by hand, the way `RoleRun._cold_fallback_labels` is the one
    helper its own family reads from. `system_version` is the caller's own: unlike
    every other family here, a delivery sample names the *delivering* run's version
    (the task document's own `system_version`, ADR-0008), never the checkout `close`
    happens to run from -- the two are typically different checkouts entirely."""
    return {
        "product": product,
        "system.version": system_version,
        "prompts.fingerprint": prompts_fingerprint,
        "task.type": task_type,
    }


def record_delivery(
    *,
    product: str,
    task_type: str,
    prompts_fingerprint: str,
    system_version: str,
    outcome: system_metrics.DeliveryOutcome,
    human_commits: int,
    human_lines_changed: int,
    time_to_merge_seconds: float | None,
) -> bool:
    """`agentic.delivery.*`, the human's verdict on a task's pull request. Its own
    function, not a `Run`: `close` runs no role and opens no span, so there is nothing
    to hang one on. Same `_EMIT_LOCK` as every other emitter -- one record, one flush
    -- but its own return, unlike every `Run._emit` caller which discards it: `close`
    orders writing the closure's frontmatter keys after a *true* flush, so it needs to
    know whether this one actually sent the sample rather than assume it did.
    `time_to_merge` is omitted when the pull request never merged (`None`): there is
    nothing to measure, the same reason `metrics.time_to_merge_seconds` returns
    `None` for it. `system_version` is the delivering run's own, read from the task
    document, not `telemetry.system_version()` -- `close` may run from a different
    checkout than the one that delivered, and the sample must still name the run it
    judges; `close` is also where the single `"unknown"` literal for an absent, empty
    or null document value belongs, this function takes whatever string it is given
    verbatim."""
    with _EMIT_LOCK:
        i = instruments()
        labels = _delivery_labels(product, task_type, prompts_fingerprint, system_version)
        i.delivery_outcome.set(1, {**labels, "outcome": outcome})
        i.delivery_human_commits.set(human_commits, labels)
        i.delivery_human_lines_changed.set(human_lines_changed, labels)
        if time_to_merge_seconds is not None:
            i.delivery_time_to_merge.set(time_to_merge_seconds, labels)
        return _flush_metrics()


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
        """Stamps the span always — a paused or crashed invocation stays readable in
        Langfuse — but emits the five task gauges only when `rules.run_ended` says the
        Corrida itself is over (ADR-0008, 2026-09-18 second addendum): a pause or a
        crash leaves the task resumable, and emitting here too is the bug this task
        closes — a task paused twice would leave two samples of its accumulated total.
        `finish_invocation` is untouched: it already reports one invocation's own
        delta, whatever status this one leaves the task in."""
        cost = final.get("cost_usd", 0.0)
        rework = system_metrics.rework_cost_usd(final)
        # The series keeps the old name on purpose: renaming it would cut every panel
        # in two at this commit. `agentic.task.returns`, the `return.category` label,
        # `role.return_categories` and the `returned` outcome are data already written,
        # so they do not follow the domain's rename (ADR-0008, addendum; ADR-0018).
        requested = system_metrics.changes_requested_total(final)
        by_category = system_metrics.changes_by_category(final)
        status = final.get("status", "open")
        labels = {
            "product": self.product,
            "task.type": final.get("type", "unknown"),
            "task.status": status,
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
        if not run_ended(status):
            return
        elapsed = self.elapsed

        def record(i: Instruments) -> None:
            i.task_cost.set(cost, labels)
            i.task_rework.set(rework, labels)
            i.task_duration.set(elapsed, labels)
            i.task_finished.set(1, labels)
            for category, count in by_category.items():
                i.task_returns.set(count, {**labels, "return.category": category})

        self._emit(record)

    def finish_invocation(
        self, before: TaskState, after: TaskState, kind: InvocationKind, decision: str | None
    ) -> None:
        """`agentic.invocation.*` and the per-invocation task samples, off the state the
        invocation entered with and the one it left with — never the task's accumulated
        figures, which `finish` above keeps emitting unchanged. `task.delta` is exactly
        the tool for this: the patch that turns `before` into `after` through the same
        reducers the graph itself applies, so a resume's own cost, rework and returns
        are what it added, not the task's running total (the bug the request opens
        with: every invocation repeating the accumulated cost and rework).

        Emitted from `cli._invoke`, off the final state, never from a system node, from
        `pause`, or from a role span (a fan-out branch cannot tell the human once; a
        resumed `pause` re-runs from its start on every resume).

        `task_paused` fires only for a pause this invocation itself caused: a resume
        enters already carrying the prior pause's `escalated`/`escalation_class` (it is
        the state as it paused, README.md/`resume.Resumption.paused`), and a crash that
        never reaches a node folds `after` from that same `before` with only `status`
        changed — without the comparison, every crashed resume of an already-paused task
        would re-emit the pause its earlier invocation already counted (one invocation
        pauses at most once)."""
        patch = delta(before, after)
        cost = max(patch.get("cost_usd", 0.0), 0.0)
        rework = max(system_metrics.rework_cost_usd(patch), 0.0)
        elapsed = self.elapsed
        by_category = system_metrics.invocation_returns_by_category(patch)
        status = after.get("status", "open")
        labels = {
            "product": self.product,
            "invocation.kind": kind,
            "system.version": system_version(),
        }
        new_pause = after.get("escalated") and (
            before.get("escalated") != after.get("escalated")
            or before.get("escalation_class") != after.get("escalation_class")
        )

        def record(i: Instruments) -> None:
            i.invocation_cost.set(cost, labels)
            i.invocation_rework.set(rework, labels)
            i.invocation_duration.set(elapsed, labels)
            for category, count in by_category.items():
                i.invocation_returns.set(count, {**labels, "return.category": category})
            i.task_invocations.set(1, {**labels, "task.status": status})
            if new_pause:
                i.task_paused.set(
                    1, {**labels, "escalation.class": after.get("escalation_class") or "unknown"}
                )
            if decision:
                i.task_human_decisions.set(1, labels)

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
        attributes: dict[str, str | int | float | bool | tuple[str, ...] | list[str]] = {
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
        if stats := patch.get("interview_stats"):
            self._emit_interview(stats, patch.get("invariants"))

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

    def _cold_fallback_labels(self, role_slug: str, phase: str) -> dict[str, str]:
        """The single label set of `agentic.role.cold_fallback`: `product`, `role`,
        `role.slug`, `phase`, `system.version` — no `model`, `rework` or
        `role.outcome`. Both `_emit_run` and `_emit_failures` call this rather than
        spelling the set themselves, so the series never drifts between the two paths
        that can produce it. The run path deliberately loses `model`, `rework` and
        `role.outcome`: the sample records that the system dropped the caller's
        session and started cold (ADR-0004, sixth addendum), a fact about the role and
        the phase, not about a model (ADR-0008, 2026-09-21 addendum)."""
        return {
            "product": self.product,
            "role": self.role,
            "role.slug": role_slug,
            "phase": phase,
            "system.version": system_version(),
        }

    def _emit_run(self, patch: TaskState, run: RoleRunRecord) -> None:
        """`agentic.role.cost_usd`, `.run_duration`, `.queue_wait`, plus the tokens,
        calls, turns, max turns, tool calls and sub-agent cost of this run record, one
        record-and-flush. Every one of those gauges now carries `agentic.role.cost_usd`'s
        own label set verbatim, `system.version` included (ADR-0008, 2026-09-21
        addendum: a token panel used to group by role and model alone, never by the
        checkout that ran it — that decision changes so tokens, calls and turns can be
        compared across versions too). `tokens`, `calls`, `tool_calls`,
        `subagent_cost_usd` and `max_turns` are absent on a run recorded before the
        field existed; `turns` is not, so it is the one always emitted. `max_turns`
        shares `turns`'s own label set (issue #81), so the two series line up in a
        panel without a join.

        `role_cost` here only ever carries this run's own delivering money (`cost_usd`
        minus `failed_cost_usd`, the fact `run_record` already recorded): a second,
        separate record-and-flush below carries the failed-attempt money alone, same
        labels but `rework="true"` (ADR-0008, 2026-09-18 second addendum), only when
        there is any — so the two samples still sum to `cost_usd`, and the one marked
        `rework` sums to what `metrics.run_rework_usd` says this run's rework is worth,
        whether or not the run is itself a return (`is_rework`)."""
        labels = self._run_labels(patch, run)
        role_slug = system_metrics.role_slug(run)
        phase = phase_of(run.get("section", run["role"]))
        duration = system_metrics.run_duration_s(run)
        queue_wait = run.get("queue_wait_s")
        tokens = run.get("tokens", {})
        tool_calls = run.get("tool_calls", {})
        failed_cost = run.get("failed_cost_usd", 0.0)
        delivered_cost = run["cost_usd"] - failed_cost

        def record(i: Instruments) -> None:
            i.role_cost.set(delivered_cost, labels)
            if duration is not None:
                i.role_run_duration.set(duration, labels)
            if queue_wait is not None:
                i.role_queue_wait.set(queue_wait, labels)
            for kind, count in tokens.items():
                i.role_tokens.set(count, {**labels, "kind": kind})
            if "calls" in run:
                i.role_calls.set(run["calls"], labels)
            i.role_turns.set(run["turns"], labels)
            if "max_turns" in run:
                i.role_max_turns.set(run["max_turns"], labels)
            for tool, count in tool_calls.items():
                i.role_tool_calls.set(count, {**labels, "tool": tool})
            if "subagent_cost_usd" in run:
                i.role_subagent_cost.set(run["subagent_cost_usd"], labels)
            if run.get("cold_fallback", False):
                cold_labels = self._cold_fallback_labels(role_slug, phase)
                i.role_cold_fallback.set(1, cold_labels)

        self._emit(record)

        if failed_cost > 0:
            rework_labels = {**labels, "rework": True}

            def record_failed(i: Instruments) -> None:
                i.role_cost.set(failed_cost, rework_labels)

            self._emit(record_failed)

    def _emit_residual(self, patch: TaskState) -> None:
        """The cost no run record carries: an escalation's failed attempts, or an
        empty patch, at value zero so the panel never silently misses the node. No
        `model`, since no run backs it. `phase` is never absent (ADR-0008, 2026-09-18
        second addendum): `residual_phase`'s own cascade — the run this patch
        delivered, else its first failed attempt's section, else `"unknown"` — so an
        omitted label and one with a value never read as the same series."""
        labels: dict[str, str | bool] = {
            "product": self.product,
            "role": self.role,
            "model": "",
            "rework": system_metrics.residual_rework(patch),
            "role.slug": self.slug or "unknown",
            "phase": system_metrics.residual_phase(patch),
            "role.outcome": system_metrics.residual_outcome(patch),
            "system.version": system_version(),
        }
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
        this task) still reads `unknown` instead of borrowing the span's identity.
        `phase` is never absent (ADR-0008, 2026-09-18 second addendum): `unknown` when
        the failures carry no `section` to derive one from."""
        section = failures[0].get("section")
        role_slug = failures[0].get("slug") or "unknown"
        phase = phase_of(section) if section else "unknown"
        common: dict[str, str] = {
            "product": self.product,
            "role": self.role,
            "role.slug": role_slug,
            "phase": phase,
            "system.version": system_version(),
        }
        counts = system_metrics.failures_by_reason(failures)
        costs = system_metrics.failure_cost_by_reason(failures)
        fell_back_cold = any(f.get("cold_fallback", False) for f in failures)

        def record(i: Instruments) -> None:
            for reason, count in counts.items():
                i.role_failures.set(count, {**common, "reason": reason})
            for reason, total in costs.items():
                i.role_failure_cost.set(total, {**common, "reason": reason})
            if fell_back_cold:
                cold_labels = self._cold_fallback_labels(role_slug, phase)
                i.role_cold_fallback.set(1, cold_labels)

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
        the delivery, or the last the backstop let through — is `final`. `role` and
        `role.slug` are the node's own, like `_emit_failures`; `phase` comes from the
        run the patch's own verdict describes (`target_run`), the same source the
        residual sample reads, since a `CheckResult` carries no section of its own —
        never absent (ADR-0008, 2026-09-18 second addendum): `"unknown"` when the
        patch has no such run, for example a failure before the role delivered
        anything."""
        invocations = patch.get("presubmit_checks", [])
        last = len(invocations) - 1
        target = system_metrics.target_run(patch)
        common: dict[str, str] = {
            "product": self.product,
            "role": self.role,
            "role.slug": self.slug or "unknown",
            "phase": phase_of(target.get("section", target["role"])) if target else "unknown",
            "system.version": system_version(),
        }
        for index, results in enumerate(invocations):
            final = index == last

            def record(
                i: Instruments,
                results: list[CheckResult] = results,
                final: bool = final,
            ) -> None:
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

    def _emit_interview(self, stats: InterviewStats, adopted: list[str] | None) -> None:
        """`agentic.interview.rounds`, `.questions{to}` and `.invariants{state}`, from
        the patch's own counts (`InterviewStats`, measurement-only). `adopted` is
        `None` unless the patch also carries the plan's own `invariants` — the solution
        stage's Tech Lead run, in the same patch as the interview that proposed them."""
        common = {
            "product": self.product,
            "interview": stats["interview"],
            "system.version": system_version(),
        }
        questions = system_metrics.interview_questions(stats)

        def record(i: Instruments) -> None:
            i.interview_rounds.set(stats["rounds"], common)
            for to, count in questions.items():
                i.interview_questions.set(count, {**common, "to": to})
            i.interview_invariants.set(
                stats["invariants_proposed"], {**common, "state": "proposed"}
            )
            if adopted is not None:
                i.interview_invariants.set(len(adopted), {**common, "state": "adopted"})

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


def _flush_metrics() -> bool:
    """Export what was set since the last export, and report what `force_flush()`
    itself returned: `Run._emit` calls this per event and keeps discarding the
    return, byte for byte, but `record_delivery` returns it to its own caller.
    `False` with no meter provider -- `configure()` leaves it silently unset without
    `OTEL_EXPORTER_OTLP_ENDPOINT`, which is not the same fact as a flush that ran and
    failed, but both are "no export happened" to a caller deciding whether to trust
    the sample landed."""
    if _meter_provider is None:
        return False
    return bool(_meter_provider.force_flush())


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
