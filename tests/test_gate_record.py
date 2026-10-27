"""GH-361: the wait for a share is recorded apart from the run (ADR-0070). Seams: the task
document's round trip of the run, merge and full_checks records, and the span attributes of
the presubmit / full_checks node."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentic.core.measurement.adapters.otel import telemetry
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application.develop import develop
from agentic.core.process.application.full_checks import full_checks
from agentic.core.process.domain import document, presubmit
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import TASK, building
from tests.core.process.application.test_full_checks import state_of
from tests.core.process.application.test_merge_full_checks import _merged_with
from tests.core.process.application.test_presubmit_scheduler import gate_over
from tests.fakes import (
    FakeCheckScheduler,
    MemoryStack,
    ScriptedRunner,
    delivered,
    memory_ports,
)

pytestmark = pytest.mark.anyio

GATE = {
    "started_at": "2026-10-26T10:00:00.000+00:00",
    "wait_s": 12.5,
    "run_s": 80.25,
    "share": 6,
    "peers": 2,
    "priority": "subtask",
    "overdue": False,
}
SPAN_KEYS = (
    "agentic.presubmit.wait_s",
    "agentic.presubmit.share",
    "agentic.presubmit.peers",
    "agentic.presubmit.priority",
    "agentic.presubmit.overdue",
)


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    yield exporter
    telemetry.configure()


def _run(**more):
    return {
        "role": "Developer",
        "section": "Developer",
        "version": 1,
        "session_id": "s",
        "model": "m",
        "cost_usd": 0.1,
        "turns": 1,
        **more,
    }


# -- the document round trip -----------------------------------------------------------


def test_the_gates_of_a_run_and_the_gate_of_a_full_checks_record_round_trip(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path)
    full = {
        "version": 1,
        "head": "abc",
        "passed": True,
        "duration_s": 3.0,
        "declared": {"tests": "pytest"},
        "results": [{"name": "tests", "passed": True}],
        "witness": None,
        "gate": GATE | {"priority": "integration"},
    }
    documents.save(
        {
            "id": "T-1",
            "title": "t",
            "description": "d",
            "runs": [_run(gates=[GATE, GATE | {"wait_s": 0.0}])],
            "full_checks": full,
        }
    )
    loaded = documents.load("T-1")
    assert loaded["runs"][0]["gates"] == [GATE, GATE | {"wait_s": 0.0}]
    assert loaded["full_checks"] == full


def test_a_document_written_before_the_gate_record_still_loads_without_it():
    old = (
        "---\n"
        "id: T-1\n"
        "title: t\n"
        "runs:\n"
        "- {role: Developer, section: Developer, version: 1, session_id: '', model: m,"
        " cost_usd: 0.1, turns: 1}\n"
        "full_checks: {version: 1, head: abc, passed: true, duration_s: 3.0,"
        " declared: {tests: pytest}, results: [{name: tests, passed: true}], witness: null}\n"
        "---\n"
        "\n# t\n\n## Original request\n\nd\n"
    )
    state = document.parse(old)
    assert "gates" not in state["runs"][0]
    assert "gate" not in state["full_checks"]


def test_a_gate_record_with_only_some_keys_loads_as_written(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path)
    documents.save(
        {
            "id": "T-1",
            "title": "t",
            "description": "d",
            "runs": [_run(gates=[{"wait_s": 4.0}])],
        }
    )
    assert documents.load("T-1")["runs"][0]["gates"] == [{"wait_s": 4.0}]


def _merge_checks(**more):
    return presubmit.merge_checks_record(
        head="abc",
        master="def",
        duration_s=2.5,
        declared={"tests": "pytest"},
        results=[
            {"name": "tests", "command": "pytest", "passed": True, "output": "", "duration_s": 1.0}
        ],
        witness=None,
        **more,
    )


def test_the_merge_record_gate_round_trips_through_the_document_on_disk(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path)
    documents.save({"id": "T-1", "title": "t", "description": "d"})
    gate = GATE | {"priority": "land"}
    record = _merge_checks(gate=gate)
    assert record["gate"] == gate
    documents.record_closure("T-1", {"merge_checks": record})
    assert documents.load("T-1")["merge_checks"] == record


def test_a_merge_record_written_without_a_gate_loads_without_one(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path)
    documents.save({"id": "T-1", "title": "t", "description": "d"})
    record = _merge_checks()
    assert "gate" not in record
    documents.record_closure("T-1", {"merge_checks": record})
    loaded = documents.load("T-1")["merge_checks"]
    assert loaded == record and "gate" not in loaded


# -- who writes the record -------------------------------------------------------------


async def test_a_developer_run_keeps_one_gate_record_per_invocation_that_ran_the_checks(cfg):
    scheduler = FakeCheckScheduler(share=6, wait_s=7.5)
    ports = memory_ports(
        ScriptedRunner(developer=[delivered()]),
        stack=MemoryStack(checks={"tests": "pytest"}),
        check_scheduler=scheduler,
    )
    state, _ = building(ports)
    patch = await develop({**TASK, **state}, cfg, ports)
    (gate,) = patch["runs"][-1]["gates"]
    assert (gate["share"], gate["wait_s"], gate["priority"], gate["overdue"]) == (
        6,
        7.5,
        "subtask",
        False,
    )
    assert gate["run_s"] >= 0.0 and gate["started_at"].endswith("+00:00")
    assert set(gate) == set(GATE)


@pytest.mark.parametrize(("moved", "clean"), [(False, True), (True, False)])
def test_a_call_refused_before_the_checks_ran_leaves_no_gate_record(moved, clean):
    scheduler = FakeCheckScheduler()
    gate = gate_over(scheduler, moved=moved, clean=clean)
    assert gate() is not None
    assert gate.gate is None and gate.gates == []


def test_every_invocation_that_ran_the_checks_leaves_its_own_record():
    gate = gate_over(FakeCheckScheduler(share=3, wait_s=2.0))
    gate()
    gate()
    assert len(gate.gates) == len(gate.invocations) == 2
    assert gate.gate == gate.gates[-1] and gate.gate["share"] == 3 and gate.gate["wait_s"] == 2.0


async def test_the_full_checks_record_carries_its_gate(cfg):
    ports = memory_ports(
        stack=MemoryStack(checks={"tests": "pytest"}),
        check_scheduler=FakeCheckScheduler(share=9, overdue=True, wait_s=3.0),
    )
    patch = await full_checks(state_of(), cfg, ports)
    gate = patch["full_checks"]["gate"]
    assert (gate["share"], gate["wait_s"], gate["priority"], gate["overdue"]) == (
        9,
        3.0,
        "integration",
        True,
    )


def test_the_merge_record_carries_its_gate():
    ports = _merged_with(MemoryStack(checks={"tests": "pytest"}))
    gate = ports.documents.load("GH-7")["merge_checks"]["gate"]
    assert gate["priority"] == "land" and gate["share"] == 4
    assert gate["wait_s"] == 0.0 and gate["overdue"] is False


# -- the span --------------------------------------------------------------------------


def _finished(spans, patch):
    with telemetry.role_span("Developer", "developer", "gym-saas", arm="default") as run:
        run.finish({"cost_usd": 0.3, **patch})
    return spans.get_finished_spans()[-1].attributes


def test_the_span_carries_the_five_attributes_of_the_last_gate_of_the_run(spans):
    first = GATE | {"wait_s": 99.0, "share": 2}
    attributes = _finished(spans, {"runs": [_run(gates=[first, GATE])]})
    assert attributes["agentic.presubmit.wait_s"] == 12.5
    assert attributes["agentic.presubmit.share"] == 6
    assert attributes["agentic.presubmit.peers"] == 2
    assert attributes["agentic.presubmit.priority"] == "subtask"
    assert attributes["agentic.presubmit.overdue"] is False


def test_the_span_of_a_full_checks_run_carries_them_from_its_record(spans):
    gate = GATE | {"priority": "integration", "overdue": True}
    attributes = _finished(spans, {"full_checks": {"gate": gate}})
    assert attributes["agentic.presubmit.priority"] == "integration"
    assert attributes["agentic.presubmit.overdue"] is True


@pytest.mark.parametrize(
    "patch",
    [
        {"runs": [_run()]},
        {"runs": [_run(gates=[])]},
        {"full_checks": {"version": 1}},
        {},
    ],
)
def test_the_span_has_none_of_them_when_there_was_no_grant(spans, patch):
    attributes = _finished(spans, patch)
    assert not [key for key in SPAN_KEYS if key in attributes]
