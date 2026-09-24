"""Spans, one trace per task (ADR-0008; ADR-0036: Langfuse is the only destination).

`task_span` around the graph run and `role_span` around each role yield a `Run`;
`finish(patch)` on it stamps the run's outcome, cost and model on the span. Every fact
that used to leave a Prometheus/Grafana Cloud sample now either already lives on a span
attribute (unchanged), or is folded into a new `agentic.<family>.<name>` attribute on
the span whose grain the family already has -- an array of `"<dimension>=<count>"`
strings where the old metric grouped by a label, the same shape ADR-0008's 2026-09-17
addendum already chose for `agentic.role.return_categories` (`observability/README.md`
is the normative table of what moved where, and what has no span home at all).
`traceparent()` goes to the Claude Code subprocess, whose own spans then nest under the
role. Nothing is exported unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

Every span carries the `product` the run worked on, handed in by the caller, because
since ADR-0017 there are two and a panel that adds them up compares a gym SaaS with an
agentic system (issue #47). Which name a product goes by is the product's own
declaration, `.agentic/stack.yaml`; this adapter only stamps it.

Telemetry is cross-cutting: the graph adapter opens the spans and finishes them with
the patch each use case returns, the SDK adapter propagates the context; the use cases
never see it (ADR-0007). The collector decides where each signal goes -- today, the one
place: Langfuse.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Literal

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from agentic.core.process.domain.presubmit import CheckResult
from agentic.core.process.domain.task import FindingRecord, TaskState, delta
from agentic.core.process.domain.task import RoleRun as RoleRunRecord
from agentic.shared.stage0 import CHECKOUT

from ...domain import metrics as system_metrics
from . import semconv

# One `agentic run`/`resume` call (glossary: Invocación), distinct from a Corrida (the
# whole execution over a task): `cli._invoke`'s only two shapes, and the closed set the
# `agentic.invocation.kind` attribute carries.
InvocationKind = Literal["run", "resume"]

SERVICE_NAME = "agentic-system"
# The checkout that executes this run, and whose sha the version names. With the system
# as a product, the copy being edited is another one (`shared.stage0`; ADR-0017).
REPO = CHECKOUT
# The shape ADR-0028 gives a baseline tag (`bl-YYYY-MM-DD`), so `system_baseline`'s
# `git describe --match` only ever resolves a tag the human declared as one, never the
# first `v0.1` or spike tag someone pushes into the same repository (ADR-0030).
BASELINE_TAG_GLOB = "bl-*"

_tracer_provider: TracerProvider | None = None
_configured = False
_version: str | None = None
_baseline: str | None = None


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


def system_baseline() -> str:
    """The human-declared baseline this checkout is compared against (ADR-0028): the
    exact twin of `system_version()` above. `AGENTIC_SYSTEM_BASELINE` overrides it;
    else `git describe --tags --abbrev=0 --match` `BASELINE_TAG_GLOB` against `REPO`,
    the same repository `_git_version` reads; else the literal `untagged` -- git
    answered and no `bl-*` tag reaches this commit, a real label value, distinct from
    `unknown` (the document said nothing, `cli.UNKNOWN_SYSTEM_VERSION`'s own case).
    Same module-level memoisation as `system_version`."""
    global _baseline
    if _baseline is None:
        _baseline = os.environ.get("AGENTIC_SYSTEM_BASELINE") or _git_baseline()
    return _baseline


def _git_baseline() -> str:
    try:
        git = ["git", "-C", str(REPO)]
        out = subprocess.run(
            [*git, "describe", "--tags", "--abbrev=0", "--match", BASELINE_TAG_GLOB],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "untagged"
    return out or "untagged"


# Per-`(repo, sha)` cache for `resolve_baseline` below: ten documents sharing one
# `system_commit` cost one pair of git calls, not ten (Tech Lead plan, subtask
# `scorecard`). Module-level like `_version`/`_baseline` above, but keyed rather than
# singular -- `eval compare` reads many distinct shas in one invocation, unlike a live
# run's own single `system_version`.
_BASELINE_OF_SHA: dict[tuple[str, str], str] = {}
# `eval compare`'s own default timeout for `resolve_baseline`'s two git calls: long
# enough for a cold `git describe` on a large history, short enough that one
# unreachable checkout cannot stall a read-only comparison that spends nothing.
BASELINE_RESOLUTION_TIMEOUT_S = 5.0


def resolve_baseline(
    system_commit: str | None, repo: Path = REPO, *, timeout: float = BASELINE_RESOLUTION_TIMEOUT_S
) -> str:
    """The bucket `eval compare` groups a task document into when the document carries
    no `system_baseline` of its own (a document written before ADR-0030, or one whose
    live run never resolved a tag) -- `pre-baseline`, a resolved `bl-*` tag, or
    `unknown`. A document that already has its own `system_baseline` (a `bl-*` tag, or
    the literal `untagged`) wins outright and never reaches this function: `eval
    compare` (`cli.py`) is the only caller, and it calls this only for the documents
    that need it (module docstring, `system_baseline`'s own neighbour).

    Keys on `system_commit`, never on `system_version` (`+dirty`/`unknown`, not a git
    object -- module docstring). `repo` defaults to `REPO`, the pinned stage0 checkout
    `_git_version`/`_git_baseline` already read; a stage0 pinned before a task's own
    sha, or before the `bl-*` tag that would resolve it, leaves most documents
    `unknown` until re-pinned or `--system-repo` names another (Tech Lead plan's own
    accepted risk). Memoised per `(repo, sha)`.

    Order: `git rev-parse --verify <sha>^{commit}` first -- an absent or empty
    `system_commit`, a sha that does not resolve, or an ambiguous abbreviation (a
    non-zero exit either way) is `unknown` -- then `git describe --tags --abbrev=0
    --match` `BASELINE_TAG_GLOB` against the resolved sha, `pre-baseline` when nothing
    reachable. `TimeoutExpired`, `CalledProcessError` and `OSError` alike land in
    `unknown` after the first call and `pre-baseline` after the second: a read-only
    comparison that spends nothing must not raise out of one unreachable git (Tech
    Lead plan invariant)."""
    key = (str(repo), system_commit or "")
    if key not in _BASELINE_OF_SHA:
        _BASELINE_OF_SHA[key] = _resolve_baseline(system_commit, repo, timeout)
    return _BASELINE_OF_SHA[key]


def _resolve_baseline(system_commit: str | None, repo: Path, timeout: float) -> str:
    if not system_commit:
        return "unknown"
    git = ["git", "-C", str(repo)]
    try:
        verified = subprocess.run(
            [*git, "rev-parse", "--verify", f"{system_commit}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        return "unknown"
    if verified.returncode != 0:
        return "unknown"
    sha = verified.stdout.strip()
    try:
        described = subprocess.run(
            [*git, "describe", "--tags", "--abbrev=0", "--match", BASELINE_TAG_GLOB, sha],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        return "pre-baseline"
    tag = described.stdout.strip()
    return tag if described.returncode == 0 and tag else "pre-baseline"


def endpoint_declared() -> bool:
    """Whether the collector's target is declared in this process --
    `OTEL_EXPORTER_OTLP_ENDPOINT` read verbatim, `bool` of whatever string is there.
    The only executable statement under `agentic/` that reads this variable:
    `configure()`'s own third conjunct below calls this instead of its own
    `os.environ.get(...)`. The OTel HTTP exporter reads the same variable itself to
    find its target; that is the whole reason it exists and is outside this rule, same
    as the docstrings and `observability/README.md` that name it in prose."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def configure(tracer_provider: TracerProvider | None = None) -> None:
    """Install the tracer provider. Called once, lazily, from the endpoint env var;
    tests pass their own provider instead. No meter provider exists any more (ADR-0036:
    Langfuse ingests traces, observations and scores, never OTel metrics) -- the whole
    metric half (`Instruments`, `record_delivery`, `metrics_configured`) is gone with
    Grafana Cloud, the destination it existed for."""
    global _tracer_provider, _configured
    _configured = True
    if tracer_provider is None and endpoint_declared():
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider = TracerProvider(resource=resource())
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _tracer_provider = tracer_provider


def tracer() -> trace.Tracer:
    if not _configured:
        configure()
    if _tracer_provider is None:
        return trace.get_tracer("agentic")
    return _tracer_provider.get_tracer("agentic")


def _counts(pairs: Mapping[str, int | float]) -> tuple[str, ...]:
    """One `"<dimension>=<count>"` string per key, sorted for a deterministic span
    attribute -- the array shape ADR-0008's 2026-09-17 addendum already chose for
    `agentic.role.return_categories`, generalised from one entry per occurrence to one
    entry per dimension with its count (or cost) as the value, for every family that
    lost its metric label without a document to re-derive it from."""
    return tuple(f"{key}={value}" for key, value in sorted(pairs.items()))


def _aggregate(runs: list[RoleRunRecord], key: Literal["tokens", "tool_calls"]) -> dict[str, int]:
    """Sums a per-run dict-valued field (`tokens{kind}`, `tool_calls{tool}`) across
    every run record of a patch, onto the one array this stamps on the role span --
    the family's grain moves from "per run" to "per patch" in the move (module
    docstring): a patch with several runs (an interview) reports the total, not one
    array per run."""
    total: dict[str, int] = {}
    for run in runs:
        for name, count in run.get(key, {}).items():
            total[name] = total.get(name, 0) + count
    return total


def _findings_counts(findings: list[FindingRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in findings:
        cell = f"{item['severity']}/{item['category']}/{item['status']}"
        counts[cell] = counts.get(cell, 0) + 1
    return counts


def _presubmit_arrays(
    invocations: list[list[CheckResult]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`agentic.presubmit.checks` (one `"check/result/final=count"` cell per invocation
    of the delivery gate that ran the checks) and `agentic.presubmit.duration` (one
    `"check=seconds"` entry per check occurrence, unaggregated since a duration cannot
    be summed into a count without losing the figure a panel would want)."""
    last = len(invocations) - 1
    counts: dict[str, int] = {}
    durations: list[str] = []
    for index, results in enumerate(invocations):
        final = index == last
        for check in results:
            result = "green" if check["passed"] else "red"
            cell = f"{check['name']}/{result}/{final}"
            counts[cell] = counts.get(cell, 0) + 1
            durations.append(f"{check['name']}={check.get('duration_s', 0.0)}")
    return _counts(counts), tuple(durations)


@dataclass
class Run:
    """A span plus its clock. `finish` is called by the adapter that has the patch."""

    span: trace.Span
    # The product this run worked on. Not a default: a sample with no product is a
    # sample that adds up with the other product's, which is the bug (issue #47).
    product: str = ""
    # The arm this run's own `Configuration` carried at the span's open
    # (`metrics.arm_label`), ADR-0030 -- stamped on every attribute helper below by way
    # of `self.arm`, the same way `product` is.
    arm: str = ""
    started: float = field(default_factory=monotonic)

    @property
    def elapsed(self) -> float:
        return monotonic() - self.started


@dataclass
class TaskRun(Run):
    def finish(self, final: TaskState) -> None:
        """Stamps the task span, always -- a paused or crashed invocation stays
        readable in Langfuse. `agentic.task.returns` is the one new array here: the
        per-category breakdown the removed `agentic.task.returns` metric used to carry
        as a label, `changes_by_category`'s own dict turned into `"category=count"`
        entries (module docstring)."""
        cost = final.get("cost_usd", 0.0)
        rework = system_metrics.rework_cost_usd(final)
        requested = system_metrics.changes_requested_total(final)
        by_category = system_metrics.changes_by_category(final)
        status = final.get("status", "open")
        self.span.set_attributes(
            {
                "product": self.product,
                "task.type": final.get("type", "unknown"),
                "task.status": status,
                "system.version": system_version(),
                "system.baseline": system_baseline(),
                "arm": self.arm,
                "prompts.fingerprint": system_metrics.prompts_fingerprint(final),
                "task.cost_usd": cost,
                "task.rework_usd": rework,
                "task.returns": requested,
                "task.failures": len(final.get("failures", [])),
                "task.runs": len(final.get("runs", [])),
                "task.escalated": bool(final.get("escalated")),
                "agentic.task.returns": _counts(by_category),
            }
        )

    def finish_invocation(
        self, before: TaskState, after: TaskState, kind: InvocationKind, decision: str | None
    ) -> None:
        """`agentic.invocation.*`, from the delta between the state this invocation
        entered with and the one it left with -- never the task's accumulated figures,
        which `finish` above keeps stamping unchanged. One `task_span` is opened per
        invocation (`cli._invoke`), so unlike the removed metrics (one sample per
        invocation, told apart by a label) every attribute here already has its own
        span to live on: no array, no dimension, just this invocation's own numbers.
        `agentic.task.paused`/`agentic.task.human_decisions` are stamped only when this
        invocation itself caused them -- the same two guards the deleted
        `agentic.task.paused`/`.human_decisions` gauges used, `new_pause` unchanged."""
        patch = delta(before, after)
        cost = max(patch.get("cost_usd", 0.0), 0.0)
        rework = max(system_metrics.rework_cost_usd(patch), 0.0)
        by_category = system_metrics.invocation_returns_by_category(patch)
        new_pause = after.get("escalated") and (
            before.get("escalated") != after.get("escalated")
            or before.get("escalation_class") != after.get("escalation_class")
        )
        attributes: dict[str, str | int | float | bool | tuple[str, ...]] = {
            "agentic.invocation.kind": kind,
            "agentic.invocation.cost_usd": cost,
            "agentic.invocation.rework_usd": rework,
            "agentic.invocation.duration": self.elapsed,
        }
        if by_category:
            attributes["agentic.invocation.returns"] = _counts(by_category)
        if new_pause:
            attributes["agentic.task.paused"] = after.get("escalation_class") or "unknown"
        if decision:
            attributes["agentic.task.human_decisions"] = True
        self.span.set_attributes(attributes)


@dataclass
class RoleRun(Run):
    role: str = ""
    # `Role.slug` of the node this span represents. Unlike a stored run's own `slug`
    # (which can be missing on a document from before that field existed, and reads
    # `unknown`), this one names the role that is running right now.
    slug: str = ""

    def finish(self, patch: TaskState) -> None:
        """Stamps the role span. Every bare/`agentic.role.*` key here is byte-identical
        to before this task (module docstring); every `agentic.<family>.*` array below
        is new, one per by-dimension count this patch's families used to leave as a
        Prometheus label -- stamped only when the patch actually carries that family,
        same as the removed metrics only emitted when there was something to say."""
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
        if failures := patch.get("failures", []):
            attributes["agentic.role.failures"] = _counts(
                system_metrics.failures_by_reason(failures)
            )
            attributes["agentic.role.failure_cost_usd"] = _counts(
                system_metrics.failure_cost_by_reason(failures)
            )
            if any(f.get("cold_fallback", False) for f in failures):
                attributes["agentic.role.cold_fallback"] = True
        if run is not None and run.get("cold_fallback", False):
            attributes["agentic.role.cold_fallback"] = True
        if tokens := _aggregate(runs, "tokens"):
            attributes["agentic.role.tokens"] = _counts(tokens)
        if tool_calls := _aggregate(runs, "tool_calls"):
            attributes["agentic.role.tool_calls"] = _counts(tool_calls)
        if patch.get("findings"):
            attributes["agentic.review.findings"] = _counts(_findings_counts(patch["findings"]))
        if invocations := patch.get("presubmit_checks"):
            checks, durations = _presubmit_arrays(invocations)
            attributes["agentic.presubmit.checks"] = checks
            attributes["agentic.presubmit.duration"] = durations
        if stats := patch.get("interview_stats"):
            attributes["agentic.interview.rounds"] = stats["rounds"]
            attributes["agentic.interview.questions"] = _counts(
                system_metrics.interview_questions(stats)
            )
            invariant_counts: dict[str, int] = {"proposed": stats["invariants_proposed"]}
            if (adopted := patch.get("invariants")) is not None:
                invariant_counts["adopted"] = len(adopted)
            attributes["agentic.interview.invariants"] = _counts(invariant_counts)
        self.span.set_attributes(attributes)
        for one_run in runs:
            self._child_span(patch, one_run)

    def _child_span(self, patch: TaskState, run: RoleRunRecord) -> None:
        """One span per run record, under the role span, carrying that run's own agent,
        model, session and prompt hash -- the shape ADR-0008's semconv addendum defined
        for a role run, applied per run instead of once for a patch that may hold
        several (an interview's rounds, each a different role). Byte-identical to
        before this task."""
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
def task_span(task_id: str, title: str, product: str, *, arm: str) -> Iterator[TaskRun]:
    """The root of the task's trace; its name is what Langfuse lists. `product`,
    `system.baseline` and `arm` are on the span from the start, so a crashed run is
    still attributable to all three (ADR-0030) -- `system.baseline` and `arm` spelled
    bare, like `product`, since ADR-0008's 2026-09-17 rule keeps a key's bare spelling
    wherever the span already writes it identically to what used to be a metric label.
    `arm` is required and keyword-only: a default would let a missed wiring mislabel a
    whole arm silently, exactly what the label exists to prevent."""
    with tracer().start_as_current_span(
        task_id,
        attributes={
            "task.id": task_id,
            "task.title": title,
            "product": product,
            "system.baseline": system_baseline(),
            "arm": arm,
        },
    ) as span:
        yield TaskRun(span, product=product, arm=arm)


@contextmanager
def role_span(role_name: str, role_slug: str, product: str, *, arm: str) -> Iterator[RoleRun]:
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
            "system.baseline": system_baseline(),
            "arm": arm,
        },
    ) as span:
        yield RoleRun(span, product=product, role=role_name, slug=role_slug, arm=arm)


def traceparent() -> str | None:
    """The W3C header for the current span, or None when there is no recording span."""
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def flush() -> None:
    """Send what is still buffered; call before the process exits. Traces only: there
    is no metric exporter left to flush (ADR-0036)."""
    if _tracer_provider is not None:
        _tracer_provider.force_flush()
