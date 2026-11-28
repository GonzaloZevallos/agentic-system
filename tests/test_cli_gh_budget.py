"""The GraphQL budget export at the CLI's four sites (GH-991): the poller's line, the
`gh` launches of an invocation (`_invoke`) and of a `merge`/`land` child, and the bounded
flush. An in-memory exporter stands in for the collector; every counting test has its own
log, never the process one."""

import threading
import time
from collections.abc import Mapping
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentic import cli
from agentic.core.measurement.adapters.otel import telemetry
from agentic.core.process.adapters.github.gh_call_log import InProcessGhCallLog
from agentic.core.process.application.landing import LandResult
from agentic.core.process.application.ports import LockHeld
from agentic.core.process.domain.gh_calls import GH_CALL_KINDS, GhCallKind
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryRunRegistry, memory_ports
from tests.test_cli import FakeLock, lock_record


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


class CountingLog:
    """A `GhCallLog` that says how many times it was drained and hands back `counts`."""

    def __init__(self, counts: Mapping[GhCallKind, int] | None = None):
        self.counts = counts or dict.fromkeys(GH_CALL_KINDS, 0)
        self.drains = 0

    def record(self, kind: GhCallKind) -> None:
        raise AssertionError("nothing records into this log")

    def drain(self) -> Mapping[GhCallKind, int]:
        self.drains += 1
        return self.counts


def with_calls(counts: Mapping[GhCallKind, int]) -> Mapping[GhCallKind, int]:
    zeros: dict[GhCallKind, int] = dict.fromkeys(GH_CALL_KINDS, 0)
    return {**zeros, **counts}


# -- the poller ------------------------------------------------------------------------


def test_the_poller_is_wired_to_the_otel_module(tmp_path):
    assert cli._poll_budget_wiring(tmp_path).sink is telemetry


def test_a_poller_round_exports_its_span_without_round_points_when_unmeasured(spans, tmp_path):
    log = InProcessGhCallLog()
    log.record("api")
    log.record("api")
    wiring = cli._poll_budget_wiring(tmp_path)
    step = cli._budget_step(lambda: None, log, wiring.sink)
    assert step is not None
    step()
    (span,) = spans.get_finished_spans()
    assert span.name == "agentic.gh.budget"
    assert span.attributes["agentic.gh.consumer"] == "poller"
    assert "api=2" in span.attributes["agentic.gh.calls"]
    assert "agentic.gh.round_points" not in span.attributes


# -- run / resume ----------------------------------------------------------------------


class FakeApp:
    def __init__(self, final=None, raises=None):
        self.final = final or {"id": "GH-9", "title": "t", "status": "ready_for_review"}
        self.raises = raises

    async def ainvoke(self, graph_input, config=None):
        if self.raises is not None:
            raise self.raises
        return self.final


async def invoke(tmp_path, app, log):
    entering = {"id": "GH-9", "title": "t", "description": "d"}
    cfg = Configuration(product_dir=tmp_path)
    scorer = SimpleNamespace(write=lambda *args: None)  # the document is absent: never called
    return await cli._invoke(
        app, entering, {}, entering, memory_ports(), cfg, scorer=scorer, gh_calls=log
    )


@pytest.mark.anyio
async def test_an_invocation_stamps_its_calls_on_the_task_span_and_drains_once(spans, tmp_path):
    log = CountingLog(with_calls({"api": 4, "pr_view": 1}))
    await invoke(tmp_path, FakeApp(), log)
    (span,) = spans.get_finished_spans()
    assert span.attributes["agentic.gh.consumer"] == "run"
    assert span.attributes["agentic.gh.calls"] == (
        "api=4",
        "issue_list=0",
        "issue_view=0",
        "other=0",
        "pr_list=0",
        "pr_view=1",
    )
    assert log.drains == 1


@pytest.mark.anyio
async def test_a_crashed_invocation_stamps_its_calls_drains_once_and_still_raises(spans, tmp_path):
    log = CountingLog(with_calls({"issue_list": 2}))
    with pytest.raises(RuntimeError):
        await invoke(tmp_path, FakeApp(raises=RuntimeError("boom")), log)
    (span,) = spans.get_finished_spans()
    assert span.attributes["task.status"] == "crashed"
    assert span.attributes["agentic.gh.consumer"] == "run"
    assert "issue_list=2" in span.attributes["agentic.gh.calls"]
    assert log.drains == 1


# -- merge / land ----------------------------------------------------------------------


@pytest.fixture
def children(monkeypatch):
    """`merge_task` and `land` faked, the lock and the registry in memory."""
    registry = MemoryRunRegistry()
    lock = FakeLock()
    monkeypatch.setattr(cli, "_registry_for", lambda cfg: registry)
    monkeypatch.setattr(cli, "_lock_for", lambda cfg: lock)
    monkeypatch.setattr(
        cli, "merge_task", lambda *args, **kwargs: SimpleNamespace(kind="skipped", reason="")
    )
    monkeypatch.setattr(cli, "land", lambda *args, **kwargs: LandResult("skipped", "no_head"))
    return SimpleNamespace(registry=registry, lock=lock)


def merge_ports():
    ports = memory_ports()
    ports.documents.save({"id": "GH-9", "pull_request": {"number": 1}})
    return ports


async def run_child(command, tmp_path, log):
    cfg = Configuration(product_dir=tmp_path)
    if command == "merge":
        await cli._merge("GH-9", 9, cfg, merge_ports(), gh_calls=log)
    else:
        await cli._land("GH-9", cfg, memory_ports(), gh_calls=log)


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["merge", "land"])
async def test_a_child_that_launched_no_gh_exports_nothing(spans, children, tmp_path, command):
    log = CountingLog()
    await run_child(command, tmp_path, log)
    assert spans.get_finished_spans() == ()
    assert log.drains == 1


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["merge", "land"])
async def test_a_child_exports_one_run_span_with_its_counts(spans, children, tmp_path, command):
    log = CountingLog(with_calls({"pr_list": 3}))
    await run_child(command, tmp_path, log)
    (span,) = spans.get_finished_spans()
    assert span.name == "agentic.gh.budget"
    assert span.attributes["agentic.gh.consumer"] == "run"
    assert "pr_list=3" in span.attributes["agentic.gh.calls"]
    assert "agentic.gh.round_points" not in span.attributes
    assert "agentic.gh.graphql.remaining" not in span.attributes
    assert log.drains == 1


@pytest.mark.anyio
async def test_a_merge_under_a_held_lock_still_drains_once_and_refuses(
    spans, children, tmp_path, monkeypatch
):
    children.lock.held = lock_record()
    log = CountingLog(with_calls({"api": 1}))
    with pytest.raises(cli.CannotStart):
        await run_child("merge", tmp_path, log)
    assert log.drains == 1
    assert len(spans.get_finished_spans()) == 1


@pytest.mark.anyio
async def test_a_land_under_a_held_lock_still_drains_once_and_refuses(
    spans, children, tmp_path, monkeypatch
):
    def held(*args, **kwargs):
        raise LockHeld(lock_record(), "GH-9 is already running")

    monkeypatch.setattr(cli, "land", held)
    log = CountingLog(with_calls({"api": 1}))
    with pytest.raises(cli.CannotStart):
        await run_child("land", tmp_path, log)
    assert log.drains == 1
    assert len(spans.get_finished_spans()) == 1
    assert children.registry.unregistered == 1


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["merge", "land"])
async def test_a_gh_budget_that_raises_changes_nothing_but_its_own_line(
    spans, children, tmp_path, command, monkeypatch, capsys
):
    def boom(*args):
        raise ValueError("no")

    monkeypatch.setattr(telemetry, "gh_budget", boom)
    await run_child(command, tmp_path, CountingLog(with_calls({"api": 1})))
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"agentic: {command}: GH-9 -> skipped" + (": no_head" if command == "land" else ""),
        f"agentic: {command}: gh budget not exported: ValueError",
    ]


@pytest.mark.anyio
async def test_a_failing_gh_budget_does_not_hide_the_childs_own_exception(
    spans, children, tmp_path, monkeypatch
):
    def boom(*args):
        raise ValueError("no")

    monkeypatch.setattr(telemetry, "gh_budget", boom)
    children.lock.held = lock_record()
    with pytest.raises(cli.CannotStart):
        await run_child("merge", tmp_path, CountingLog(with_calls({"api": 1})))


# -- the bounded flush -----------------------------------------------------------------


def test_a_flush_that_hangs_costs_the_cap_and_says_so(capsys):
    release = threading.Event()
    started = time.monotonic()
    try:
        cli._bounded_flush("poll", flush=release.wait, cap_s=0.05)
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert elapsed < 2.0
    assert capsys.readouterr().out == "agentic: poll: telemetry flush timed out\n"


def test_a_flush_that_raises_says_one_line_and_does_not_raise(capsys):
    def broken():
        raise ConnectionError("collector says no")

    cli._bounded_flush("merge", flush=broken, cap_s=1.0)
    assert capsys.readouterr().out == "agentic: merge: telemetry flush failed: ConnectionError\n"


def test_a_flush_that_returns_says_nothing(capsys):
    cli._bounded_flush("land", flush=lambda: None, cap_s=1.0)
    assert capsys.readouterr().out == ""
