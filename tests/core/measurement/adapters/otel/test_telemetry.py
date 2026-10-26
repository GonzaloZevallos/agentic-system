"""Spans and propagation (ADR-0037: Langfuse is the only destination, no meter
provider left to configure). No exporter: an in-memory provider stands in."""

import os
import re
import subprocess

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentic.core.measurement.adapters.otel import semconv, telemetry
from agentic.shared.stage0 import CHECKOUT
from tests.readme_metrics import Family, _documented

TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


@pytest.fixture
def spans(monkeypatch):
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "abc1234")
    monkeypatch.setattr(telemetry, "_version", None)
    monkeypatch.setenv("AGENTIC_SYSTEM_BASELINE", "bl-2026-09-23")
    monkeypatch.setattr(telemetry, "_baseline", None)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    yield exporter
    telemetry.configure()


def test_no_provider_no_traceparent():
    telemetry.configure(tracer_provider=None)
    assert telemetry.traceparent() is None
    with (
        telemetry.task_span("T-1", "t", "gym-saas", arm="default"),
        telemetry.role_span("PjM", "pjm", "gym-saas", arm="default"),
    ):
        assert telemetry.traceparent() is None  # nothing configured: nothing to propagate


def test_role_span_nests_under_the_task_and_propagates(spans):
    with telemetry.task_span("GH-12", "Metrics", "gym-saas", arm="default") as task:
        with telemetry.role_span("PjM", "pjm", "gym-saas", arm="default") as role:
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


def test_task_span_carries_the_issue_number(spans):
    """ADR-0037: the cardinality restriction that used to keep a task id off every span
    was Prometheus's own (a per-task series explodes a time-series backend); with no
    metrics backend left, one trace per task is the normal Langfuse read, so the task
    span's own name and `task.id` carry it, unlike the frozen bare label keys around it."""
    with telemetry.task_span("GH-129", "Spans", "gym-saas", arm="default") as run:
        pass
    assert run.span.name == "GH-129" and run.span.attributes["task.id"] == "GH-129"


def test_role_span_is_named_and_identified_by_the_gen_ai_convention(spans):
    """invoke_agent <role>, the convention's span-name shape; the role identified by
    `gen_ai.agent.name` (`Role.name`) and `gen_ai.agent.id` (`Role.slug`) verbatim
    (invariant 6), never re-derived from one another."""
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas", arm="default"):
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
        telemetry.task_span("GH-12", "Metrics", "gym-saas", arm="default"),
        telemetry.role_span("PjM", "pjm", "gym-saas", arm="default"),
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
    with telemetry.role_span("PjM", "pjm", "gym-saas", arm="default") as run:
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
    assert telemetry._tracer_provider is None


def test_endpoint_declared_true_when_the_variable_is_set(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    assert telemetry.endpoint_declared() is True


def test_endpoint_declared_false_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert telemetry.endpoint_declared() is False


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
    with telemetry.role_span("PM", "pm", "gym-saas", arm="default") as run:
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
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas", arm="default") as run:
        run.finish({"cost_usd": 0.2, "change_history": history})
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas", arm="default") as run:
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
    with telemetry.role_span(
        "Product Analyst", "product_analyst", "gym-saas", arm="default"
    ) as run:
        run.finish(patch)
    (span,) = spans.get_finished_spans()
    assert span.attributes["role.outcome"] == "failed"
    assert (
        span.attributes["agentic.role.rework"] is True
        and "agentic.role.model" not in span.attributes
    )


# Every branch `RoleRun.finish` takes, each with its own fixture, asserted over the
# complete set of keys the span carries — not a subset (invariant 10 / ADR-0030).
# `ON_OPEN` is what `role_span` stamps before `finish` ever runs; `ALWAYS_ON_FINISH` is
# what every branch of `finish` adds regardless of the patch it gets.
ON_OPEN = {
    semconv.OPERATION_NAME,
    semconv.AGENT_NAME,
    semconv.AGENT_ID,
    semconv.PROVIDER_NAME,
    semconv.SYSTEM,
    "role",
    "product",
    "langfuse.observation.type",
    "system.baseline",
    "arm",
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
    }


def _maximal_patch() -> dict:
    """One patch that exercises every optional branch `RoleRun.finish` has: the sole
    fixture the README-contract case below reads (no other test in this task may build
    a second one). One run with tokens/tool_calls/cold_fallback, a failure sharing its
    reason, a prompt hash, a return category, two presubmit invocations, an interview
    with both recipients and its own adopted invariant, and one finding."""
    run = _run("Developer", "developer", "Developer: api", 0.5)
    run |= {
        "version": 2,
        "tokens": {"input": 10, "output": 20},
        "tool_calls": {"Read": 2, "Bash": 1},
        "cold_fallback": True,
        "gates": [
            {
                "started_at": "2026-10-26T10:00:00.000+00:00",
                "wait_s": 1.5,
                "run_s": 20.0,
                "share": 6,
                "peers": 1,
                "priority": "subtask",
                "overdue": False,
            }
        ],
    }
    failure = {
        "role": "Developer",
        "attempt": 1,
        "error": "e",
        "cost_usd": 0.02,
        "session_id": "",
        "reason": "schema",
        "slug": "developer",
        "section": "Developer: api",
    }
    return {
        "runs": [run],
        "failures": [failure],
        "cost_usd": 0.62,
        "prompts": {"developer": "deadbeef"},
        "playbooks": {"developer": "cafe0123"},
        "change_history": [
            {"pair": "Tech Lead→Developer:api", "category": "process-suite-red", "reason": "r"}
        ],
        "findings": [{"severity": "important", "category": "seam", "status": "open"}],
        "presubmit_checks": [
            [_check("tests", passed=True), _check("lint", passed=False)],
            [_check("tests", passed=True), _check("format", passed=False)],
        ],
        "interview_stats": {
            "interview": "solution",
            "rounds": 2,
            "questions_to_pm": 1,
            "questions_to_human": 1,
            "invariants_proposed": 2,
        },
        "invariants": ["Every X does Y"],
    }


def _check(name="tests", command="pytest", passed=True, duration_s=1.0):
    return {
        "name": name,
        "command": command,
        "passed": passed,
        "output": "",
        "duration_s": duration_s,
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
            {"agentic.role.failures", "agentic.role.failure_cost_usd"},
            id="no-delivery-with-a-failure",
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
        pytest.param(
            {"runs": [_run("PjM", "pjm", "PjM", 0.1) | {"cold_fallback": True}], "cost_usd": 0.1},
            DELIVERY_KEYS | {"agentic.role.cold_fallback"},
            id="cold-fallback-present",
        ),
        pytest.param(
            {
                "runs": [
                    _run("PjM", "pjm", "PjM", 0.1)
                    | {"tokens": {"input": 1, "output": 2}, "tool_calls": {"Bash": 1}}
                ],
                "cost_usd": 0.1,
            },
            DELIVERY_KEYS | {"agentic.role.tokens", "agentic.role.tool_calls"},
            id="tokens-and-tool-calls-present",
        ),
        pytest.param(
            {
                "cost_usd": 0.2,
                "findings": [{"severity": "important", "category": "seam", "status": "open"}],
            },
            {"agentic.review.findings"},
            id="findings-present",
        ),
        pytest.param(
            {"cost_usd": 0.3, "presubmit_checks": [[_check(passed=True)]]},
            {"agentic.presubmit.checks", "agentic.presubmit.duration"},
            id="presubmit-present",
        ),
        pytest.param(
            {
                "cost_usd": 0.3,
                "interview_stats": {
                    "interview": "solution",
                    "rounds": 1,
                    "questions_to_pm": 0,
                    "questions_to_human": 0,
                    "invariants_proposed": 1,
                },
            },
            {
                "agentic.interview.rounds",
                "agentic.interview.questions",
                "agentic.interview.invariants",
            },
            id="interview-present",
        ),
        # The README's own contract (ADR-0030's two properties, verbatim): `extra` here
        # is not hand-written, it is every role-grain attribute the table documents --
        # a family the table lists but this maximal patch never stamps breaks equality
        # and pytest's own set diff names it; a family this patch stamps but the table
        # never lists does the same in the other direction.
        pytest.param(
            _maximal_patch(),
            _documented("rol")
            | DELIVERY_KEYS
            | {"agentic.role.prompt_hash", "agentic.role.return_categories"},
            id="readme-contract",
        ),
    ],
)
def test_role_finish_attribute_keys_are_exactly_the_declared_set_per_branch(spans, patch, extra):
    with telemetry.role_span("PM", "pm", "gym-saas", arm="default") as run:
        run.finish(patch)
    span = spans.get_finished_spans()[-1]  # the role span, after any run's own child
    assert set(span.attributes) == ON_OPEN | ALWAYS_ON_FINISH | extra


def test_every_role_span_attribute_key_is_gen_ai_agentic_or_an_exception(spans):
    """invariant 3, checked over the keys the richest patch actually emits -- every
    optional branch of `finish` contributes its keys too -- rather than over a
    hand-written list."""
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish(_maximal_patch())
    # The run's own child span carries new keys too (invariant 3 covers it as well).
    for span in spans.get_finished_spans():
        for key in span.attributes:
            assert (
                key.startswith("gen_ai.") or key.startswith("agentic.") or key in semconv.EXCEPTIONS
            ), key


def test_lost_families_are_stamped_nowhere(spans):
    """The twelve families `observability/README.md` marks lost (`grain == "—"`) must
    never resurface as a span attribute under their old dotted name: a lost family that
    quietly comes back as an attribute is a row the table no longer tells the truth
    about. Exercised against the richest patch on both spans (role, task, invocation)."""
    from tests.readme_metrics import _lost

    lost = _lost()
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as task_run:
        task_run.finish({"cost_usd": 0.1, "status": "ready_for_review"})
        task_run.finish_invocation(
            {"id": "T-1", "title": "t", "description": "d"},
            {"id": "T-1", "title": "t", "description": "d", "status": "ready_for_review"},
            "run",
            None,
        )
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as role_run:
        role_run.finish(_maximal_patch())
    for span in spans.get_finished_spans():
        assert not (lost & set(span.attributes))


def test_a_lost_row_with_no_named_reader_is_rejected(tmp_path, monkeypatch):
    """The rejecting input `readme_metrics.Family` exists to catch: a lost row (`—`)
    whose prose names none of the three readers decision 5 allows. Never accepted
    silently as a fourth, undocumented kind of loss."""
    with pytest.raises(AssertionError, match="names no reader"):
        Family("agentic.role.duration", "—", "—", "en ningún lado")


def test_a_kept_row_with_no_single_attribute_key_is_rejected():
    """The other rejecting input: a row that is not lost (a real grain) but whose own
    cell is not exactly one backtick-quoted key -- prose left over from an edit, or two
    keys crammed into one cell."""
    with pytest.raises(AssertionError, match="no attribute key"):
        Family("agentic.role.cost_usd", "dos claves `a` y `b`", "rol", "")


def test_task_finish_stamps_the_span_always(spans):
    """`TaskRun.finish` stamps the span unconditionally, whatever the status: a paused
    or crashed invocation stays readable in Langfuse, unlike the metric gauges ADR-0008's
    2026-09-18 second addendum used to gate on `rules.run_ended`."""
    final = {
        "type": "feature",
        "status": "waiting_on_human",
        "cost_usd": 0.25,
        "changes_requested": {"PM→Product Analyst": 1},
        "change_history": [{"pair": "PM→Product Analyst", "category": "definition", "reason": "r"}],
        "failures": [{"role": "PjM", "attempt": 1, "error": "e", "cost_usd": 0.01}],
        "runs": [
            {
                "role": "PjM",
                "version": 1,
                "session_id": "",
                "model": "m",
                "cost_usd": 0.1,
                "turns": 1,
            }
        ],
    }
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as run:
        run.finish(final)
    (span,) = spans.get_finished_spans()
    assert span.attributes["task.status"] == "waiting_on_human"
    assert span.attributes["task.type"] == "feature"
    assert span.attributes["task.cost_usd"] == 0.25
    # 0.0 rework from the PjM run (`version == 1`, no `failed_cost_usd`) plus the
    # 0.15 the task's own `cost_usd` leaves unaccounted for by any run -- a residual,
    # rework by the same definition (`metrics.rework_cost_usd`).
    assert span.attributes["task.rework_usd"] == pytest.approx(0.15)
    assert span.attributes["task.failures"] == 1
    assert span.attributes["task.runs"] == 1
    assert span.attributes["task.escalated"] is False
    assert span.attributes["task.returns"] == 1
    assert span.attributes["agentic.task.returns"] == ("definition=1",)


# `TASK_ALWAYS`: every key `task_span`'s own open plus one unconditional `TaskRun.finish`
# leaves -- hand-written once here, and cross-checked below (never a second hand list)
# against every family README documents at grain "tarea"/"invocación".
TASK_ALWAYS = {
    "task.id",
    "task.title",
    "product",
    "system.baseline",
    "arm",
    "task.type",
    "task.status",
    "system.version",
    "prompts.fingerprint",
    "task.cost_usd",
    "task.rework_usd",
    "task.returns",
    "task.failures",
    "task.runs",
    "task.escalated",
    "agentic.task.returns",
}
INVOCATION_ALWAYS = {
    "agentic.invocation.kind",
    "agentic.invocation.cost_usd",
    "agentic.invocation.rework_usd",
    "agentic.invocation.duration",
}


def test_task_finish_and_invocation_attribute_keys_are_exactly_the_declared_set(spans):
    """The task-span twin of the role contract above (ADR-0030's two properties,
    verbatim): one `finish` plus one `finish_invocation` that trips every conditional
    (a return, a pause, a human decision) leaves exactly `TASK_ALWAYS |
    INVOCATION_ALWAYS` plus the three conditionals -- and every attribute
    `observability/README.md` documents at grain "tarea" or "invocación" is one of
    them, checked by name so a documented-but-unstamped family fails on its own key."""
    final = {
        "type": "feature",
        "status": "ready_for_review",
        "cost_usd": 0.25,
        "change_history": [{"pair": "PM→Product Analyst", "category": "definition", "reason": "r"}],
    }
    before = {"id": "T-1", "title": "t", "description": "d"}
    after = {
        **before,
        "escalated": "x",
        "escalation_class": "cap",
        "change_history": [{"pair": "PM→Product Analyst", "category": "definition", "reason": "r"}],
    }
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as run:
        run.finish(final)
        run.finish_invocation(before, after, "resume", "Use USD.")
    (span,) = spans.get_finished_spans()
    conditionals = {
        "agentic.invocation.returns",
        "agentic.task.paused",
        "agentic.task.human_decisions",
    }
    assert set(span.attributes) == TASK_ALWAYS | INVOCATION_ALWAYS | conditionals
    documented = _documented("tarea") | _documented("invocación")
    for key in documented:
        assert key in span.attributes, key
    assert documented <= TASK_ALWAYS | INVOCATION_ALWAYS | conditionals


def test_invocation_finish_reports_only_the_delta_this_invocation_spent(spans):
    """The bug the request opens with: every invocation repeating the task's
    accumulated cost and rework. `finish_invocation` diffs the state the invocation
    entered with against the one it left with (`task.delta`), so a resume's own span
    carries only what it added."""
    before = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "cost_usd": 100.0,
        "runs": [
            {
                "role": "PjM",
                "version": 1,
                "session_id": "s1",
                "model": "m",
                "cost_usd": 0.1,
                "turns": 1,
            }
        ],
    }
    after = {
        **before,
        "cost_usd": 140.0,
        "status": "waiting_on_human",
        "runs": [
            *before["runs"],
            {
                "role": "Developer",
                "version": 2,
                "session_id": "s2",
                "model": "m",
                "cost_usd": 40.0,
                "turns": 3,
            },
        ],
        "change_history": [
            {"pair": "Tech Lead→Developer:api", "category": "spec-behaviour", "reason": "r"}
        ],
    }
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as run:
        run.finish_invocation(before, after, "resume", "Use USD.")
    (span,) = spans.get_finished_spans()
    assert span.attributes["agentic.invocation.kind"] == "resume"
    assert span.attributes["agentic.invocation.cost_usd"] == pytest.approx(40.0)
    # The new run is a return's delivery (version 2): its whole cost is rework, and only
    # this invocation's own, never the task's running total.
    assert span.attributes["agentic.invocation.rework_usd"] == pytest.approx(40.0)
    assert span.attributes["agentic.invocation.returns"] == ("spec-behaviour=1",)
    assert span.attributes["agentic.task.human_decisions"] is True
    # No escalation this invocation: nothing paused.
    assert "agentic.task.paused" not in span.attributes


def test_invocation_finish_on_a_crash_reports_zero_not_negative(spans):
    """A crash hands `finish_invocation` the state it entered with, spread with only
    `status` changed: nothing new was recorded, so every delta is zero, never negative
    from a state the graph never actually reached."""
    before = {"id": "T-1", "title": "t", "description": "d", "cost_usd": 12.5}
    crashed = {**before, "status": "crashed"}
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as run:
        run.finish_invocation(before, crashed, "run", None)
    (span,) = spans.get_finished_spans()
    assert span.attributes["agentic.invocation.cost_usd"] == 0.0
    assert span.attributes["agentic.invocation.rework_usd"] == 0.0
    assert "agentic.task.paused" not in span.attributes
    assert "agentic.task.human_decisions" not in span.attributes


def test_task_paused_stamps_the_escalation_class_or_unknown(spans):
    before = {"id": "T-1", "title": "t", "description": "d"}
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as run:
        run.finish_invocation(
            before, {**before, "escalated": "x", "escalation_class": "cap"}, "run", None
        )
    with telemetry.task_span("T-2", "t", "gym-saas", arm="default") as run:
        # A document from before `escalation_class` existed: still stamped, `unknown`.
        run.finish_invocation(before, {**before, "escalated": "y"}, "run", None)
    first, second = spans.get_finished_spans()
    assert first.attributes["agentic.task.paused"] == "cap"
    assert second.attributes["agentic.task.paused"] == "unknown"


def test_a_crash_of_an_already_paused_resume_does_not_repeat_its_pause(spans):
    """A resume enters carrying the prior pause's `escalated`/`escalation_class` (it is
    the state as it paused, `resume.Resumption.paused`); when it crashes before
    reaching a node, `cli._invoke` folds that same state with only `status` changed
    (the case above). `agentic.task.paused` must not stamp again: the earlier invocation
    that first escalated already accounted for this pause (one invocation pauses at
    most once) — only a genuinely new or different escalation should."""
    paused_before = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "escalated": "x",
        "escalation_class": "cap",
    }
    crashed = {**paused_before, "status": "crashed"}
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as run:
        run.finish_invocation(paused_before, crashed, "resume", None)
    (span,) = spans.get_finished_spans()
    assert "agentic.task.paused" not in span.attributes


def test_a_resume_that_escalates_again_with_a_new_class_does_pause(spans):
    paused_before = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "escalated": "x",
        "escalation_class": "cap",
    }
    after = {**paused_before, "escalated": "y", "escalation_class": "questions"}
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as run:
        run.finish_invocation(paused_before, after, "resume", None)
    (span,) = spans.get_finished_spans()
    assert span.attributes["agentic.task.paused"] == "questions"


def test_task_human_decisions_only_with_an_answer(spans):
    before = {"id": "T-1", "title": "t", "description": "d"}
    with telemetry.task_span("T-1", "t", "gym-saas", arm="default") as run:
        run.finish_invocation(before, before, "run", None)
    with telemetry.task_span("T-2", "t", "gym-saas", arm="default") as run:
        run.finish_invocation(before, before, "resume", "Use USD.")
    no_answer, answered = spans.get_finished_spans()
    assert "agentic.task.human_decisions" not in no_answer.attributes
    assert answered.attributes["agentic.task.human_decisions"] is True


def test_role_finish_emits_tokens_and_tool_calls_as_arrays_summed_over_every_run(spans):
    """`agentic.role.tokens`/`.tool_calls` move from one metric sample per run to one
    array attribute per patch, summed across every run record (`_aggregate`) -- a patch
    with several runs (an interview) reports the total, not one array per run."""
    first = _run("PjM", "pjm", "PjM", 0.1) | {
        "tokens": {"input": 10, "output": 20},
        "tool_calls": {"Read": 2, "Bash": 1},
    }
    second = _run("PjM", "pjm", "PjM", 0.05) | {
        "tokens": {"input": 1, "output": 2},
        "tool_calls": {"Bash": 1},
    }
    with telemetry.role_span("PjM", "pjm", "gym-saas", arm="default") as run:
        run.finish({"runs": [first, second], "cost_usd": 0.15})
    span = spans.get_finished_spans()[-1]
    assert sorted(span.attributes["agentic.role.tokens"]) == ["input=11", "output=22"]
    assert sorted(span.attributes["agentic.role.tool_calls"]) == ["Bash=2", "Read=2"]


def test_a_run_record_without_the_new_fields_emits_no_arrays(spans):
    """A run recorded before this task has `turns` (always required) but none of
    `tokens`/`tool_calls`: no array attribute, no crash, `agentic.role.turns` unaffected."""
    run = _run("PjM", "pjm", "PjM", 0.1)
    with telemetry.role_span("PjM", "pjm", "gym-saas", arm="default") as run_span:
        run_span.finish({"runs": [run], "cost_usd": 0.1})
    span = spans.get_finished_spans()[-1]
    assert "agentic.role.tokens" not in span.attributes
    assert "agentic.role.tool_calls" not in span.attributes
    assert span.attributes["agentic.role.turns"] == 1


def test_role_finish_stamps_cold_fallback_when_any_run_or_failure_carries_it(spans):
    """A run record whose `run_with_retry` dropped a stale caller session and started
    cold (ADR-0004, sixth addendum) leaves `agentic.role.cold_fallback=True` on the
    patch's own span, a single boolean over the patch rather than a sample per rol/fase
    (that dimension has no span home any more, `observability/README.md`)."""
    run = _run("Developer", "developer", "Developer: api", 0.1)
    run["cold_fallback"] = True
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run_span:
        run_span.finish({"runs": [run], "cost_usd": 0.1})
    span = spans.get_finished_spans()[-1]
    assert span.attributes["agentic.role.cold_fallback"] is True


def test_role_finish_emits_no_cold_fallback_attribute_when_nothing_fell_back(spans):
    run = _run("PjM", "pjm", "PjM", 0.1)
    with telemetry.role_span("PjM", "pjm", "gym-saas", arm="default") as run_span:
        run_span.finish({"runs": [run], "cost_usd": 0.1})
    span = spans.get_finished_spans()[-1]
    assert "agentic.role.cold_fallback" not in span.attributes


def test_four_run_records_leave_four_child_spans_each_parented_to_the_role_span(spans):
    """The vertical's acceptance, span-only now: one interview-shaped patch, four run
    records across two roles, four child spans under the role span, each carrying its
    own run's `agentic.role.cost_usd` -- the per-run breakdown the role span's own
    aggregate attribute no longer carries (`test_role_finish_stamps_the_run_on_the_span`'s
    neighbour above covers that single scalar)."""
    interview = "Interview: Tech Lead efímero→Tech Lead"
    runs = [
        _run("Tech Lead efímero", "tech_lead_ephemeral", interview, 0.1),
        _run("Tech Lead", "tech_lead_interviewee", interview, 0.2),
        _run("Tech Lead efímero", "tech_lead_ephemeral", interview, 0.15),
        _run("Tech Lead", "tech_lead", "Tech Lead", 0.3),
    ]
    patch = {"runs": runs, "cost_usd": 0.75}
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas", arm="default") as run:
        run.finish(patch)
    finished = spans.get_finished_spans()
    role_span = finished[-1]
    children = [s for s in finished if s is not role_span]
    assert len(children) == 4
    assert all(s.parent.span_id == role_span.get_span_context().span_id for s in children)
    assert sorted(c.attributes["agentic.role.cost_usd"] for c in children) == sorted(
        r["cost_usd"] for r in runs
    )


def test_failures_leave_one_array_entry_per_reason_with_the_count_and_the_cost(spans):
    patch = {
        "failures": [
            {"role": "PjM", "attempt": 1, "error": "a", "cost_usd": 0.01, "reason": "schema"},
            {"role": "PjM", "attempt": 2, "error": "b", "cost_usd": 0.02, "reason": "schema"},
        ],
        "cost_usd": 0.03,
        "escalated": "PjM failed 2 times: b",
        "status": "waiting_on_human",
    }
    with telemetry.role_span("PjM", "pjm", "gym-saas", arm="default") as run:
        run.finish(patch)
    span = spans.get_finished_spans()[-1]
    assert span.attributes["agentic.role.failures"] == ("schema=2",)
    (cell,) = span.attributes["agentic.role.failure_cost_usd"]
    reason, cost = cell.split("=")
    assert reason == "schema" and float(cost) == pytest.approx(0.03)


def test_review_findings_group_by_severity_category_and_status_with_the_count(spans):
    """Two findings landing in the same cell (`important`/`spec-behaviour`/`open`) are
    one array entry with the count `2`."""
    findings = [
        {"severity": "important", "category": "spec-behaviour", "status": "open"},
        {"severity": "important", "category": "spec-behaviour", "status": "open"},
        {"severity": "blocking", "category": "process-suite-red", "status": "dismissed"},
    ]
    with telemetry.role_span("Tech Lead", "tech_lead_review", "gym-saas", arm="default") as run:
        run.finish({"findings": findings, "cost_usd": 0.2})
    span = spans.get_finished_spans()[-1]
    assert sorted(span.attributes["agentic.review.findings"]) == [
        "blocking/process-suite-red/dismissed=1",
        "important/spec-behaviour/open=2",
    ]


def test_a_review_with_no_findings_emits_no_finding_attribute(spans):
    with telemetry.role_span("Tech Lead", "tech_lead_review", "gym-saas", arm="default") as run:
        run.finish({"cost_usd": 0.2})
    span = spans.get_finished_spans()[-1]
    assert "agentic.review.findings" not in span.attributes


def test_presubmit_checks_leave_one_array_entry_per_invocation_only_the_last_final(spans):
    invocations = [
        [_check(passed=False, duration_s=1.0)],
        [_check(passed=False, duration_s=1.2)],
        [_check(passed=True, duration_s=0.9)],
    ]
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish({"presubmit_checks": invocations, "cost_usd": 0.3})
    span = spans.get_finished_spans()[-1]
    checks = span.attributes["agentic.presubmit.checks"]
    assert sorted(checks) == ["tests/green/True=1", "tests/red/False=2"]
    assert sorted(span.attributes["agentic.presubmit.duration"]) == [
        "tests=0.9",
        "tests=1.0",
        "tests=1.2",
    ]


def test_a_check_with_two_rows_counts_its_final_status_and_sums_its_duration(spans):
    """An affected run that was red and the full rerun that was green: one green cell and
    one duration of both runs, never a red and a green for the same check."""
    invocations = [
        [
            {**_check(passed=False, duration_s=2.0), "mode": "affected-only"},
            {**_check(passed=True, duration_s=3.0), "mode": "full"},
            _check(name="lint", command="ruff check .", passed=True, duration_s=0.5),
        ]
    ]
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish({"presubmit_checks": invocations, "cost_usd": 0.3})
    span = spans.get_finished_spans()[-1]
    assert sorted(span.attributes["agentic.presubmit.checks"]) == [
        "lint/green/True=1",
        "tests/green/True=1",
    ]
    assert list(span.attributes["agentic.presubmit.duration"]) == ["tests=5.0", "lint=0.5"]


def test_a_check_red_in_its_final_row_counts_red(spans):
    invocations = [
        [
            {**_check(passed=True, duration_s=1.0), "mode": "affected-only"},
            {**_check(passed=False, duration_s=1.0), "mode": "full"},
        ]
    ]
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish({"presubmit_checks": invocations, "cost_usd": 0.3})
    span = spans.get_finished_spans()[-1]
    assert list(span.attributes["agentic.presubmit.checks"]) == ["tests/red/True=1"]


def test_a_mode_on_a_row_changes_neither_the_cells_nor_the_durations(spans):
    plain = [[_check(duration_s=1.0), _check(name="lint", duration_s=2.0)]]
    moded = [[{**row, "mode": "full"} for row in plain[0]]]
    seen = []
    for invocations in (plain, moded):
        with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
            run.finish({"presubmit_checks": invocations, "cost_usd": 0.3})
        attributes = spans.get_finished_spans()[-1].attributes
        seen.append(
            (
                list(attributes["agentic.presubmit.checks"]),
                list(attributes["agentic.presubmit.duration"]),
            )
        )
    assert seen[0] == seen[1]


def test_a_patch_with_no_presubmit_invocations_emits_no_presubmit_attribute(spans):
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish({"cost_usd": 0.1})
    span = spans.get_finished_spans()[-1]
    assert "agentic.presubmit.checks" not in span.attributes


def test_interview_stats_stamp_rounds_questions_and_proposed_invariants(spans):
    patch = {
        "cost_usd": 0.3,
        "interview_stats": {
            "interview": "solution",
            "rounds": 3,
            "questions_to_pm": 1,
            "questions_to_human": 2,
            "invariants_proposed": 1,
        },
    }
    with telemetry.role_span(
        "Tech Lead efímero", "tech_lead_ephemeral", "gym-saas", arm="default"
    ) as run:
        run.finish(patch)
    span = spans.get_finished_spans()[-1]
    assert span.attributes["agentic.interview.rounds"] == 3
    assert sorted(span.attributes["agentic.interview.questions"]) == [
        "human=2",
        "interviewee=3",
        "pm=1",
    ]
    assert span.attributes["agentic.interview.invariants"] == ("proposed=1",)


def test_interview_adopted_invariants_come_from_its_own_invariants_key(spans):
    patch = {
        "cost_usd": 0.3,
        "interview_stats": {
            "interview": "solution",
            "rounds": 1,
            "questions_to_pm": 0,
            "questions_to_human": 0,
            "invariants_proposed": 2,
        },
        "invariants": ["Every X does Y"],
    }
    with telemetry.role_span("Tech Lead", "tech_lead", "gym-saas", arm="default") as run:
        run.finish(patch)
    span = spans.get_finished_spans()[-1]
    assert sorted(span.attributes["agentic.interview.invariants"]) == ["adopted=1", "proposed=2"]


def test_a_patch_without_an_interview_stamps_no_interview_attribute(spans):
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish({"cost_usd": 0.1})
    span = spans.get_finished_spans()[-1]
    assert "agentic.interview.rounds" not in span.attributes
    assert "agentic.interview.questions" not in span.attributes
    assert "agentic.interview.invariants" not in span.attributes


def test_system_version_prefers_the_env_var(monkeypatch):
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "v-test")
    monkeypatch.setattr(telemetry, "_version", None)
    assert telemetry.system_version() == "v-test"


def test_fresh_system_version_rereads_while_system_version_stays_memoised(monkeypatch):
    monkeypatch.setattr(telemetry, "_version", None)
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "first")
    assert telemetry.fresh_system_version() == "first"
    assert telemetry.system_version() == "first"
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "second")
    assert telemetry.fresh_system_version() == "second"
    assert telemetry.system_version() == "first"


def test_fresh_system_version_does_not_fill_the_memo(monkeypatch):
    monkeypatch.setattr(telemetry, "_version", None)
    monkeypatch.setenv("AGENTIC_SYSTEM_VERSION", "only-fresh")
    telemetry.fresh_system_version()
    assert telemetry._version is None


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


def test_system_baseline_prefers_the_env_var(monkeypatch):
    monkeypatch.setenv("AGENTIC_SYSTEM_BASELINE", "bl-test")
    monkeypatch.setattr(telemetry, "_baseline", None)
    assert telemetry.system_baseline() == "bl-test"


def _commit(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "f.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "x"],
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return path


def test_system_baseline_falls_back_to_untagged_with_no_tag_at_all(tmp_path, monkeypatch):
    repo = _commit(tmp_path / "repo")
    monkeypatch.delenv("AGENTIC_SYSTEM_BASELINE", raising=False)
    monkeypatch.setattr(telemetry, "_baseline", None)
    monkeypatch.setattr(telemetry, "REPO", repo)
    assert telemetry.system_baseline() == "untagged"


def test_system_baseline_rejects_a_tag_outside_the_bl_shape(tmp_path, monkeypatch):
    """The rejecting input: a repository whose only tag is not `bl-*` (a spike tag, a
    `v0.1`) must resolve `untagged`, never that tag -- `--match` is what makes this the
    accident-proof case ADR-0030 exists to rule out."""
    repo = _commit(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "tag", "v0.1"], check=True)
    monkeypatch.delenv("AGENTIC_SYSTEM_BASELINE", raising=False)
    monkeypatch.setattr(telemetry, "_baseline", None)
    monkeypatch.setattr(telemetry, "REPO", repo)
    assert telemetry.system_baseline() == "untagged"


def test_system_baseline_resolves_a_matching_tag(tmp_path, monkeypatch):
    repo = _commit(tmp_path / "repo")
    subprocess.run(["git", "-C", str(repo), "tag", "bl-2026-09-23"], check=True)
    monkeypatch.delenv("AGENTIC_SYSTEM_BASELINE", raising=False)
    monkeypatch.setattr(telemetry, "_baseline", None)
    monkeypatch.setattr(telemetry, "REPO", repo)
    assert telemetry.system_baseline() == "bl-2026-09-23"


def test_system_baseline_git_command_carries_the_match_argument(monkeypatch):
    """`--match` is not cosmetic (ADR-0030): any tag becomes a baseline without it, so
    this pins the argument's presence in the command itself, not only its effect."""
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        calls.append(cmd)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    monkeypatch.delenv("AGENTIC_SYSTEM_BASELINE", raising=False)
    monkeypatch.setattr(telemetry, "_baseline", None)
    telemetry.system_baseline()
    (describe,) = [c for c in calls if "describe" in c]
    assert "--match" in describe
    assert describe[describe.index("--match") + 1] == telemetry.BASELINE_TAG_GLOB == "bl-*"


def test_role_finish_stamps_the_playbook_hashes_only_when_there_are_some(spans):
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish({"cost_usd": 0.1, "prompts": {"developer": "h"}})
    assert "agentic.role.playbook_hash" not in spans.get_finished_spans()[-1].attributes
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish(
            {
                "cost_usd": 0.1,
                "prompts": {"developer": "h"},
                "playbooks": {"developer": "pb1"},
                "playbooks_changed": {"developer": "pb1"},
            }
        )
    attributes = spans.get_finished_spans()[-1].attributes
    assert attributes["agentic.role.playbook_hash"] == ("pb1",)
