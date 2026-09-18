"""Spans, propagation and system metrics. No exporter: in-memory providers stand in."""

import re

import pytest
from opentelemetry.sdk.metrics.export import MetricReader, MetricsData
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentic.core.measurement.adapters.otel import semconv, telemetry
from agentic.shared.stage0 import CHECKOUT

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
    with (
        telemetry.task_span("T-1", "t", "gym-saas"),
        telemetry.role_span("PjM", "pjm", "gym-saas"),
    ):
        assert telemetry.traceparent() is None  # nothing configured: nothing to propagate


def test_role_span_nests_under_the_task_and_propagates(spans):
    with telemetry.task_span("GH-12", "Metrics", "gym-saas") as task:
        with telemetry.role_span("PjM", "pjm", "gym-saas") as role:
            header = telemetry.traceparent()
    assert TRACEPARENT.match(header)
    trace_id, span_id = header.split("-")[1:3]
    assert int(trace_id, 16) == task.span.get_span_context().trace_id
    assert int(span_id, 16) == role.span.get_span_context().span_id
    assert role.span.parent.span_id == task.span.get_span_context().span_id
    assert task.span.name == "GH-12" and role.span.attributes["role"] == "PjM"
    # On both spans from the moment they open, so a run that crashes is still one
    # product's crash and not the other's (issue #47).
    assert task.span.attributes["product"] == role.span.attributes["product"] == "gym-saas"


def test_role_span_is_named_and_identified_by_the_gen_ai_convention(spans):
    """invoke_agent <role>, the convention's span-name shape; the role identified by
    `gen_ai.agent.name` (`Role.name`) and `gen_ai.agent.id` (`Role.slug`) verbatim
    (invariant 6), never re-derived from one another."""
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas"):
        pass
    (role,) = spans.get_finished_spans()
    assert role.name == "invoke_agent Tech Lead"
    assert role.attributes[semconv.OPERATION_NAME] == semconv.INVOKE_AGENT
    assert role.attributes[semconv.AGENT_NAME] == "Tech Lead"
    assert role.attributes[semconv.AGENT_ID] == "tech_lead"
    assert role.attributes[semconv.PROVIDER_NAME] == semconv.ANTHROPIC
    assert role.attributes[semconv.SYSTEM] == semconv.ANTHROPIC


def test_role_span_is_an_agent_for_langfuse_and_the_task_is_not(spans):
    """Langfuse shows a role as an agent run; the task keeps its default type and no
    gen_ai attribute at all — it is not a single model invocation."""
    with (
        telemetry.task_span("GH-12", "Metrics", "gym-saas"),
        telemetry.role_span("PjM", "pjm", "gym-saas"),
    ):
        pass
    role, task = spans.get_finished_spans()
    assert role.attributes["langfuse.observation.type"] == "agent"
    assert "langfuse.observation.type" not in task.attributes
    assert not any(k.startswith("gen_ai.") for k in task.attributes)


def test_role_span_carries_no_chat_attribute_and_no_dollar_under_gen_ai(spans):
    """invariant 5: `invoke_agent` is not `chat` — no token counts, no request or
    response model, no sampling parameters — and no dollar figure rides under any
    `gen_ai.*` key, whatever it is named: a role run spends across models, so a single
    model or token count on its span would be a false claim to a conforming reader."""
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run:
        run.finish(
            {
                "runs": [
                    {
                        "role": "PjM",
                        "version": 1,
                        "session_id": "s",
                        "model": "claude-opus-5",
                        "cost_usd": 0.2,
                        "turns": 2,
                    }
                ],
                "cost_usd": 0.2,
            }
        )
    (role,) = spans.get_finished_spans()
    chat_only = {
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.request.temperature",
    }
    assert not (chat_only & role.attributes.keys())
    assert not any(k.startswith("gen_ai.") and "cost" in k for k in role.attributes)


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
    with telemetry.role_span("PM", "pm", "gym-saas") as run:
        run.finish(patch)
    (span,) = spans.get_finished_spans()
    assert span.attributes["role.outcome"] == "returned"
    assert (
        span.attributes["agentic.role.version"] == 2
        and span.attributes["agentic.role.rework"] is True
    )
    assert span.attributes["agentic.role.model"] == "claude-opus-5"
    assert span.attributes["agentic.role.session_id"] == "s-1"
    assert span.attributes["agentic.role.prompt_hash"] == "deadbeef"
    assert span.attributes["agentic.role.failed_attempts"] == 0


def test_role_finish_stamps_the_categories_of_the_returns_it_made(spans):
    history = [
        {"pair": "Tech Lead→Developer:a", "category": "process-suite-red", "reason": "r"},
        {"pair": "Tech Lead→Developer:b", "category": "spec-missing-test", "reason": "r"},
    ]
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas") as run:
        run.finish({"cost_usd": 0.2, "change_history": history})
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas") as run:
        run.finish({"cost_usd": 0.2})
    returned, approved = spans.get_finished_spans()
    assert returned.attributes["agentic.role.return_categories"] == (
        "process-suite-red",
        "spec-missing-test",
    )
    assert "agentic.role.return_categories" not in approved.attributes


def test_role_finish_on_a_broken_role(spans):
    patch = {
        "failures": [{"role": "PA", "attempt": 1, "error": "e", "cost_usd": 0.05}],
        "cost_usd": 0.05,
        "escalated": "PA failed",
        "status": "waiting_on_human",
    }
    with telemetry.role_span("Product Analyst", "product_analyst", "gym-saas") as run:
        run.finish(patch)
    (span,) = spans.get_finished_spans()
    assert span.attributes["role.outcome"] == "failed"
    assert (
        span.attributes["agentic.role.rework"] is True
        and "agentic.role.model" not in span.attributes
    )


# Every branch `RoleRun.finish` takes, each with its own fixture, asserted over the
# complete set of keys the span carries — not a subset (invariant 10). `ON_OPEN` is
# what `role_span` stamps before `finish` ever runs; `ALWAYS_ON_FINISH` is what every
# branch of `finish` adds regardless of the patch it gets.
ON_OPEN = {
    semconv.OPERATION_NAME,
    semconv.AGENT_NAME,
    semconv.AGENT_ID,
    semconv.PROVIDER_NAME,
    semconv.SYSTEM,
    "role",
    "product",
    "langfuse.observation.type",
}
ALWAYS_ON_FINISH = {
    "role.outcome",
    "agentic.role.cost_usd",
    "agentic.role.failed_attempts",
    "agentic.role.rework",
}
DELIVERY_KEYS = {
    "agentic.role.version",
    "agentic.role.model",
    "agentic.role.turns",
    "agentic.role.session_id",
}


@pytest.mark.parametrize(
    ("patch", "extra"),
    [
        pytest.param(
            {
                "failures": [{"role": "PA", "attempt": 1, "error": "e", "cost_usd": 0.05}],
                "cost_usd": 0.05,
                "escalated": "PA failed",
                "status": "waiting_on_human",
            },
            set(),
            id="no-delivery",
        ),
        pytest.param(
            {
                "runs": [
                    {
                        "role": "PM",
                        "version": 1,
                        "session_id": "s-1",
                        "model": "m",
                        "cost_usd": 0.2,
                        "turns": 3,
                    }
                ],
                "failures": [],
                "cost_usd": 0.2,
                "status": "delivered",
            },
            DELIVERY_KEYS,
            id="delivery",
        ),
        pytest.param(
            {
                "runs": [
                    {
                        "role": "PM",
                        "version": 2,
                        "session_id": "s-1",
                        "model": "m",
                        "cost_usd": 0.2,
                        "turns": 3,
                    }
                ],
                "failures": [],
                "cost_usd": 0.2,
                "status": "returned",
                "prompts": {"pm": "deadbeef"},
            },
            DELIVERY_KEYS | {"agentic.role.prompt_hash"},
            id="prompts-present",
        ),
        pytest.param(
            {
                "cost_usd": 0.2,
                "change_history": [
                    {
                        "pair": "Tech Lead→Developer:a",
                        "category": "process-suite-red",
                        "reason": "r",
                    }
                ],
            },
            {"agentic.role.return_categories"},
            id="change-categories-present",
        ),
    ],
)
def test_role_finish_attribute_keys_are_exactly_the_declared_set_per_branch(spans, patch, extra):
    with telemetry.role_span("PM", "pm", "gym-saas") as run:
        run.finish(patch)
    (span,) = spans.get_finished_spans()
    assert set(span.attributes) == ON_OPEN | ALWAYS_ON_FINISH | extra


def test_every_role_span_attribute_key_is_gen_ai_agentic_or_an_exception(spans):
    """invariant 3, checked over the keys a real span actually emits — the richest
    patch, so every optional branch of `finish` contributes its keys too — rather than
    over a hand-written list."""
    with telemetry.role_span("PM", "pm", "gym-saas") as run:
        run.finish(
            {
                "runs": [
                    {
                        "role": "PM",
                        "version": 1,
                        "session_id": "s-1",
                        "model": "m",
                        "cost_usd": 0.2,
                        "turns": 3,
                    }
                ],
                "failures": [],
                "cost_usd": 0.2,
                "status": "returned",
                "prompts": {"pm": "deadbeef"},
                "change_history": [
                    {
                        "pair": "Tech Lead→Developer:a",
                        "category": "process-suite-red",
                        "reason": "r",
                    }
                ],
            }
        )
    (span,) = spans.get_finished_spans()
    for key in span.attributes:
        assert (
            key.startswith("gen_ai.") or key.startswith("agentic.") or key in semconv.EXCEPTIONS
        ), key


def test_task_finish_emits_the_system_metrics(metrics):
    final = {
        "type": "feature",
        "status": "definition_ready",
        "cost_usd": 0.5,
        "changes_requested": {"PM→Product Analyst": 1, "Tech Lead→Developer:api": 2},
        "change_history": [
            {"pair": "PM→Product Analyst", "category": "definition", "reason": "r"},
            {"pair": "Tech Lead→Developer:api", "category": "spec-behaviour", "reason": "r"},
            {"pair": "Tech Lead→Developer:api", "category": "spec-behaviour", "reason": "r"},
        ],
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
    with telemetry.task_span("T-1", "t", "gym-saas") as run:
        run.finish(final)
    got = points(metrics)
    labels = {
        "product": "gym-saas",
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
    # One sample per category of return, the task's labels plus the category.
    returns = {p.attributes["return.category"]: p for p in got["agentic.task.returns"]}
    assert {c: p.value for c, p in returns.items()} == {"definition": 1, "spec-behaviour": 2}
    assert dict(returns["definition"].attributes) == {**labels, "return.category": "definition"}
    (duration,) = got["agentic.task.duration"]
    assert duration.value >= 0
    # The finish exported on its own: the sample left the process with the event.
    assert len(metrics.batches) == 1


def test_role_finish_emits_duration_and_cost_by_role(metrics):
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run:
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
    # The product is on the role's samples too: cost by role adds up across products
    # otherwise, and a Developer of the gym is not a Developer of the system (issue #47).
    assert dict(cost.attributes) == {
        "product": "gym-saas",
        "role": "PjM",
        "model": "m",
        "rework": False,
    }
    (duration,) = got["agentic.role.duration"]
    assert dict(duration.attributes) == {
        "product": "gym-saas",
        "role": "PjM",
        "role.outcome": "delivered",
    }


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
        with telemetry.role_span("PM", "pm", "gym-saas") as run:
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
        with telemetry.role_span("Developer", "developer", "gym-saas") as run:
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


def test_the_version_is_the_shas_of_the_checkout_that_executes():
    """Not the product's: running from stage0 they are two different copies, and the
    version has to name the one whose code is doing the work (ADR-0017)."""
    assert telemetry.REPO == CHECKOUT
    assert (CHECKOUT / "agentic" / "shared" / "stage0.py").exists()
