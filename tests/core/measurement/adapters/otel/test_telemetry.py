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


@pytest.fixture
def spans_and_metrics(monkeypatch):
    """Both signals at once, for the acceptance the spine's per-run emission has to
    keep: a run record's cost sample and its child span are the same fact, told twice."""
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "abc1234")
    monkeypatch.setattr(telemetry, "_version", None)
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = BatchReader()
    telemetry.configure(
        tracer_provider=tracer_provider, meter_provider=telemetry.meter_provider_for(reader)
    )
    yield exporter, reader
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
    # The run's own child span ends first, inside `finish`; the role span last, when
    # the `with` block closes.
    role = spans.get_finished_spans()[-1]
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
    span = spans.get_finished_spans()[-1]  # the role span, after its run's own child
    assert span.attributes["role.outcome"] == "returned"
    assert (
        span.attributes["agentic.role.version"] == 2
        and span.attributes["agentic.role.rework"] is True
    )
    assert span.attributes["agentic.role.model"] == "claude-opus-5"
    assert span.attributes["agentic.role.session_id"] == "s-1"
    # The tuple of every prompt hash the patch ran, not the last write: a patch may run
    # more than one role (an interview), each with its own hash.
    assert span.attributes["agentic.role.prompt_hash"] == ("deadbeef",)
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
    span = spans.get_finished_spans()[-1]  # the role span, after any run's own child
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
    # The run's own child span carries new keys too (invariant 3 covers it as well).
    for span in spans.get_finished_spans():
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
    # `role.slug` reads `unknown`: this run was recorded before that field existed.
    assert dict(cost.attributes) == {
        "product": "gym-saas",
        "role": "PjM",
        "model": "m",
        "rework": False,
        "role.slug": "unknown",
        "phase": "definition",
        "role.outcome": "delivered",
        "system.version": "abc1234",
    }
    # `agentic.role.duration` is untouched: node-grained, its own labels, byte-identical.
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
    # Four exports: `agentic.role.duration` and the one run's `cost_usd` each flush on
    # their own (`Run._emit` moved from once per node to once per record), twice over.
    # The idle flush at the end exported nothing at all.
    assert len(metrics.batches) == 4


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


def _run(role, slug, section, cost, model="m", session="s"):
    return {
        "role": role,
        "slug": slug,
        "section": section,
        "version": 1,
        "session_id": session,
        "model": model,
        "cost_usd": cost,
        "turns": 1,
        "started_at": "2026-09-18T10:00:00.000+00:00",
        "ended_at": "2026-09-18T10:00:01.000+00:00",
        "queue_wait_s": 0.0,
    }


def test_role_finish_emits_tokens_calls_turns_tool_calls_and_subagent_cost(metrics):
    run = _run("PjM", "pjm", "PjM", 0.1)
    run |= {
        "tokens": {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40},
        "calls": 5,
        "tool_calls": {"Read": 2, "Bash": 1},
        "subagent_cost_usd": 0.02,
    }
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run_span:
        run_span.finish({"runs": [run], "sections": {"PjM": "t"}, "cost_usd": 0.1})
    got = points(metrics)
    # Same labels as `agentic.role.cost_usd`, minus `system.version`.
    base = {
        "product": "gym-saas",
        "role": "PjM",
        "model": "m",
        "rework": False,
        "role.slug": "pjm",
        "phase": "definition",
        "role.outcome": "delivered",
    }
    tokens = {t.attributes["kind"]: t.value for t in got["agentic.role.tokens"]}
    assert tokens == {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40}
    for sample in got["agentic.role.tokens"]:
        assert dict(sample.attributes) == {**base, "kind": sample.attributes["kind"]}

    (calls,) = got["agentic.role.calls"]
    assert calls.value == 5 and dict(calls.attributes) == base

    (turns,) = got["agentic.role.turns"]
    assert turns.value == 1 and dict(turns.attributes) == base  # `_run`'s own turns=1

    tool_calls = {t.attributes["tool"]: t.value for t in got["agentic.role.tool_calls"]}
    assert tool_calls == {"Read": 2, "Bash": 1}
    for sample in got["agentic.role.tool_calls"]:
        assert dict(sample.attributes) == {**base, "tool": sample.attributes["tool"]}

    (subagent,) = got["agentic.role.subagent_cost_usd"]
    assert subagent.value == pytest.approx(0.02) and dict(subagent.attributes) == base


def test_a_run_record_without_the_new_fields_emits_only_turns(metrics):
    """A run recorded before this task has `turns` (always required) but none of
    `tokens`, `calls`, `tool_calls`, `subagent_cost_usd`: no sample for those, no
    crash, `turns` still there."""
    run = _run("PjM", "pjm", "PjM", 0.1)
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run_span:
        run_span.finish({"runs": [run], "sections": {"PjM": "t"}, "cost_usd": 0.1})
    got = points(metrics)
    assert "agentic.role.tokens" not in got
    assert "agentic.role.calls" not in got
    assert "agentic.role.tool_calls" not in got
    assert "agentic.role.subagent_cost_usd" not in got
    (turns,) = got["agentic.role.turns"]
    assert turns.value == 1


def test_four_run_records_leave_four_cost_samples_and_four_child_spans(spans_and_metrics):
    """The vertical's acceptance: one interview-shaped patch, four run records across
    two roles, four `cost_usd` samples and four child spans under the role span."""
    exporter, reader = spans_and_metrics
    interview = "Interview: Tech Lead efímero→Tech Lead"
    runs = [
        _run("Tech Lead efímero", "tech_lead_ephemeral", interview, 0.1),
        _run("Tech Lead", "tech_lead_interviewee", interview, 0.2),
        _run("Tech Lead efímero", "tech_lead_ephemeral", interview, 0.15),
        _run("Tech Lead", "tech_lead", "Tech Lead", 0.3),
    ]
    patch = {"runs": runs, "sections": {"Tech Lead": "plan text"}, "cost_usd": 0.75}
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas") as run:
        run.finish(patch)
    costs = points(reader)["agentic.role.cost_usd"]
    assert sorted(c.value for c in costs) == sorted(r["cost_usd"] for r in runs)
    finished = exporter.get_finished_spans()
    role_span = finished[-1]
    children = [s for s in finished if s is not role_span]
    assert len(children) == 4
    assert all(s.parent.span_id == role_span.get_span_context().span_id for s in children)


@pytest.mark.parametrize(
    "patch",
    [
        pytest.param({"cost_usd": 0.0}, id="empty"),
        pytest.param(
            {
                "runs": [_run("PjM", "pjm", "PjM", 0.1)],
                "cost_usd": 0.1,
            },
            id="single-run-no-residual",
        ),
        pytest.param(
            {
                "runs": [_run("PjM", "pjm", "PjM", 0.05)],
                "failures": [{"cost_usd": 0.2, "reason": "sdk"}],
                "cost_usd": 0.25,
            },
            id="run-plus-residual",
        ),
        pytest.param(
            {
                "failures": [{"cost_usd": 0.08, "reason": "max_turns"}],
                "cost_usd": 0.08,
                "escalated": "PjM failed",
            },
            id="pure-escalation",
        ),
    ],
)
def test_a_patchs_cost_usd_samples_sum_to_its_cost_usd(metrics, patch):
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run:
        run.finish(patch)
    total = sum(c.value for c in points(metrics)["agentic.role.cost_usd"])
    assert total == pytest.approx(patch["cost_usd"])


def test_an_empty_patch_leaves_exactly_one_zero_cost_sample(metrics):
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run:
        run.finish({})
    (cost,) = points(metrics)["agentic.role.cost_usd"]
    assert cost.value == 0.0


def test_a_run_record_without_slug_emits_unknown(metrics):
    run = {
        "role": "PjM",
        "section": "PjM",
        "version": 1,
        "session_id": "",
        "model": "m",
        "cost_usd": 0.1,
        "turns": 1,
    }
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run_span:
        run_span.finish({"runs": [run], "cost_usd": 0.1})
    (cost,) = points(metrics)["agentic.role.cost_usd"]
    assert cost.attributes["role.slug"] == "unknown"


def test_failures_leave_one_sample_per_reason_with_the_count_and_the_cost(metrics):
    patch = {
        "failures": [
            {
                "role": "PjM",
                "attempt": 1,
                "error": "a",
                "cost_usd": 0.01,
                "session_id": "",
                "reason": "schema",
                "slug": "pjm",
                "section": "PjM",
            },
            {
                "role": "PjM",
                "attempt": 2,
                "error": "b",
                "cost_usd": 0.02,
                "session_id": "",
                "reason": "schema",
                "slug": "pjm",
                "section": "PjM",
            },
        ],
        "cost_usd": 0.03,
        "escalated": "PjM failed 2 times: b",
        "status": "waiting_on_human",
    }
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run:
        run.finish(patch)
    got = points(metrics)
    (count,) = got["agentic.role.failures"]
    assert count.value == 2
    assert dict(count.attributes) == {
        "product": "gym-saas",
        "role": "PjM",
        "role.slug": "pjm",
        "phase": "definition",
        "reason": "schema",
    }
    (cost,) = got["agentic.role.failure_cost_usd"]
    assert cost.value == pytest.approx(0.03)
    assert dict(cost.attributes) == {
        "product": "gym-saas",
        "role": "PjM",
        "role.slug": "pjm",
        "phase": "definition",
        "reason": "schema",
    }


def test_a_failure_without_slug_or_section_emits_unknown_and_no_phase(metrics):
    patch = {
        "failures": [
            {"role": "PjM", "attempt": 1, "error": "a", "cost_usd": 0.01, "session_id": ""}
        ],
        "cost_usd": 0.01,
        "escalated": "PjM failed",
        "status": "waiting_on_human",
    }
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run:
        run.finish(patch)
    (count,) = points(metrics)["agentic.role.failures"]
    assert count.attributes["role.slug"] == "unknown"
    assert "phase" not in count.attributes


def test_role_duration_labels_are_untouched_by_the_new_per_run_labels(metrics):
    """`agentic.role.duration` byte-identical: `role.slug`, `phase` and
    `system.version` land on `cost_usd`, never here."""
    run = _run("PjM", "pjm", "PjM", 0.1)
    with telemetry.role_span("PjM", "pjm", "gym-saas") as run_span:
        run_span.finish({"runs": [run], "sections": {"PjM": "t"}, "cost_usd": 0.1})
    (duration,) = points(metrics)["agentic.role.duration"]
    assert dict(duration.attributes) == {
        "product": "gym-saas",
        "role": "PjM",
        "role.outcome": "delivered",
    }


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
