import contextlib
import json
import os
import signal
import socket
import subprocess
import weakref
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import cli, composition
from agentic.core.measurement.domain import evaluation
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.github import GhInbox
from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application.ports import LockHeld, LockRecord
from agentic.core.process.domain import legacy_names
from agentic.core.process.domain.task import PullRequest, PullRequestCommit
from agentic.shared.configuration import ENV_PREFIX, Configuration
from tests.fakes import MemoryKnowledge, MemoryStack, MemoryTracker, memory_ports

# `_run`/`_resume` now open their checkpointer twice per invocation (ADR-0003, addendum
# 2026-10-04: a second open after `adelete_thread` is how a task migrates). This double
# must hand back the SAME saver both times for the same `cfg`, as the real file would,
# never a fresh `InMemorySaver` that would look like an empty store to the second open.
# Keyed on `id(cfg)` rather than `cfg` itself (`Configuration` is an unhashable pydantic
# model); the entry is dropped the moment `cfg` is garbage-collected, so two unrelated
# tests can never collide on a reused id.
_IN_MEMORY_SAVERS: dict[int, InMemorySaver] = {}


@asynccontextmanager
async def in_memory(cfg, task_id):
    key = id(cfg)
    if key not in _IN_MEMORY_SAVERS:
        _IN_MEMORY_SAVERS[key] = InMemorySaver()
        weakref.finalize(cfg, _IN_MEMORY_SAVERS.pop, key, None)
    yield _IN_MEMORY_SAVERS[key]


STACK = "product: p\nchecks:\n  tests: pytest\nsetup:\n  deps: uv sync\n"


def product(tmp_path, name, stack=STACK):
    """A product as the system needs one: a repo whose HEAD declares a stack (#36)."""
    root = tmp_path / name
    (root / ".agentic").mkdir(parents=True)
    if stack is not None:
        (root / ".agentic/stack.yaml").write_text(stack)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q", "-b", "master", str(root)], check=True)
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "v0", "--allow-empty"], cwd=root, check=True)
    return root


def capture_run(monkeypatch):
    """Replace the graph run with a recorder of what the CLI would have run. `main` now
    calls `anyio.run(cli._signal_guarded, work)` (subtask `signals`), so this unwraps
    one level to the same `work` partial every assertion here always read."""
    seen = {}

    def fake_run(func, *args):
        assert func is cli._signal_guarded
        work, *_stop = args  # `run`/`resume` also pass their `StopCause`
        task, cfg, ports, *options = work.args
        seen.update(fn=work.func, task=task, cfg=cfg, ports=ports, options=tuple(options))

    monkeypatch.setattr(cli.anyio, "run", fake_run)
    return seen


def test_flag_beats_env(monkeypatch, tmp_path):
    from_env, from_flag = product(tmp_path, "env"), product(tmp_path, "flag")
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(from_env))
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(from_flag)])
    cli.main()
    assert seen["cfg"].product_dir == from_flag
    assert seen["task"]["id"].startswith("T-")
    assert seen["task"]["title"] == "t" and seen["task"]["description"] == "d"
    assert seen["cfg"].verbose is False
    assert seen["ports"].runner.verbose is False
    assert seen["ports"].documents.product_dir == from_flag


def test_verbose_reaches_the_runner(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    seen = capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "run", "t", "d", "--verbose", "--product-dir", str(root)]
    )
    cli.main()
    assert seen["cfg"].verbose is True
    assert seen["ports"].runner.verbose is True


def test_preflight_rejects_a_dir_without_agentic(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_preflight_rejects_a_bad_flags_overlay(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    (root / ".agentic/flags.yaml").write_text("- touches_ui\n")
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert "non-empty mapping" in capsys.readouterr().err


def test_preflight_rejects_a_prompt_overlay_with_an_unknown_placeholder(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    (root / ".agentic/prompts").mkdir()
    (root / ".agentic/prompts/pjm.md").write_text("Left: {{budget}}")
    capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def fake_gh(monkeypatch, *, stdout="", returncode=0, stderr=""):
    """Only `gh`, and only the inbox's: the composition root builds its `GhInbox` with this
    `run`, so anything else (preflight asking git about the product's repo) goes through
    for real."""
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if returncode:
            raise subprocess.CalledProcessError(returncode, cmd, stderr=stderr)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(composition, "GhInbox", lambda product_dir: GhInbox(product_dir, run=run))
    return calls


def test_issue_feeds_the_task(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    calls = fake_gh(
        monkeypatch, stdout=json.dumps({"title": " Metrics ", "body": "How is my gym\n"})
    )
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "--issue", "12", "--product-dir", str(root)])
    cli.main()
    assert seen["task"] == {"id": "GH-12", "title": "Metrics", "description": "How is my gym"}
    assert calls[0][1]["cwd"] == root


def test_gh_failure_exits_2(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    fake_gh(monkeypatch, returncode=1, stderr="GraphQL: Could not resolve to an Issue")
    capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "run", "--issue", "999", "--product-dir", str(root)]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert "Could not resolve" in capsys.readouterr().err


def test_issue_and_title_are_exclusive(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "run", "t", "d", "--issue", "1", "--product-dir", str(root)]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_title_without_description_is_rejected(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "only-title", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


@pytest.mark.anyio
async def test_a_crashed_run_still_finishes_the_task_span(monkeypatch, tmp_path):
    """The graph raising outside any use case leaves a task span with a status, so
    the crash is counted; `crashed` exists only in telemetry, never in the state."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from agentic.core.measurement.adapters.otel import telemetry
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    class Broken:
        def compile(self, checkpointer):
            return self

        async def aget_state(self, config):
            return SimpleNamespace(values={}, next=(), interrupts=())

        async def ainvoke(self, task, config):
            raise RuntimeError("checkpointer down")

    monkeypatch.setattr(cli, "graph", Broken())
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    try:
        task = {"id": "T-9", "title": "t", "description": "d"}
        with pytest.raises(RuntimeError, match="checkpointer"):
            await cli._run(
                task, Configuration(product_dir=tmp_path), memory_ports(), checkpointer=in_memory
            )
    finally:
        telemetry.configure()
    (span,) = exporter.get_finished_spans()
    assert span.name == "T-9" and span.attributes["task.status"] == "crashed"
    assert span.attributes["task.cost_usd"] == 0.0


def test_resume_takes_its_flags_and_the_cap_the_human_sets(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    seen = capture_run(monkeypatch)
    argv = ["agentic", "resume", "--issue", "12", "--task-budget", "140", "--dry-run"]
    monkeypatch.setattr("sys.argv", [*argv, "--product-dir", str(root)])
    cli.main()
    assert seen["fn"] is cli._resume and seen["task"] == "GH-12"
    assert seen["options"] == (False, True, False)  # from the document, dry run, yes
    assert seen["cfg"].task_budget_usd == 140.0


class Saver:
    """One in-memory checkpointer for every command of a test, as the file would be."""

    def __init__(self):
        self.saver = InMemorySaver()

    @asynccontextmanager
    async def __call__(self, cfg, task_id):
        yield self.saver


@pytest.mark.anyio
async def test_a_paused_task_resumes_through_the_cli_and_run_refuses_to_redo_it(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.process.adapters.langgraph.graph import build_graph
    from agentic.shared.configuration import Configuration
    from tests.fakes import (
        MemoryWorkspace,
        ScriptedRunner,
        answers,
        classified,
        defined,
        delivered,
        memory_ports,
        planned,
        questions,
        reviewed,
        verdict,
    )

    script = ScriptedRunner(
        pjm=[classified()],
        product_analyst_interviewer=[questions(1), questions(done=True)],
        pm_interviewee=[answers(1)],
        product_analyst=[defined()],
        pm=[verdict("approved")],
        tech_lead_ephemeral=[questions(done=True)],
        tech_lead=[planned()],
        developer=[delivered()],
        tech_lead_review=[reviewed("approved")],
    )
    ports = memory_ports(script)
    ports.workspace.moved.add(MemoryWorkspace.ROOT / "GH-1")
    monkeypatch.setattr(cli, "graph", build_graph(lambda cfg: ports, arm_for=composition._arm_for))
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIC_TASK_MAX_BUDGET_USD", "0.7")
    cfg = Configuration.from_sources({}, os.environ)
    saver, task = Saver(), {"id": "GH-1", "title": "Export", "description": "CSV."}

    with pytest.raises(SystemExit) as info:
        await cli._run(task, cfg, ports, checkpointer=saver)
    assert info.value.code == 1
    assert "resume:    agentic resume --issue 1" in capsys.readouterr().out
    with pytest.raises(cli.CannotStart, match="is paused: Developer stopped by the task cap"):
        await cli._run(task, cfg, ports, checkpointer=saver)

    # The cap in force is already spent: refused before anything is spent.
    with pytest.raises(cli.CannotStart, match="--task-budget above 0.86"):
        await cli._resume("GH-1", cfg, ports, yes=True, checkpointer=saver)
    monkeypatch.setenv("AGENTIC_TASK_BUDGET_USD", "50")
    raised = Configuration.from_sources({}, os.environ)
    await cli._resume("GH-1", raised, ports, dry_run=True, checkpointer=saver)
    shown = capsys.readouterr().out
    assert "from:      checkpoint" in shown and "resumes:   integrate (api)" in shown
    assert script.slugs.count("developer") == 0  # a dry run spends nothing

    await cli._resume("GH-1", raised, ports, yes=True, checkpointer=saver)
    assert "status:    ready_for_review" in capsys.readouterr().out
    assert ports.tracker.unlabeled == [("GH-1", "needs-human")]
    assert [text.split(" ")[0] for _, text in ports.tracker.comments] == [
        "Escalated:",
        "Resumed:",
        "Ready",
    ]
    with pytest.raises(cli.CannotStart, match=r"already ran \(ready_for_review\)"):
        await cli._run(task, cfg, ports, checkpointer=saver)
    with pytest.raises(cli.CannotStart, match="is not paused"):
        await cli._resume("GH-1", raised, ports, yes=True, checkpointer=saver)


@pytest.mark.anyio
async def test_a_checkpointed_decided_task_is_still_decided_after_a_resume(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.process.adapters.langgraph.graph import build_graph
    from agentic.shared.configuration import Configuration
    from tests.fakes import (
        MemoryWorkspace,
        ScriptedRunner,
        answers,
        classified,
        defined,
        delivered,
        memory_ports,
        planned,
        questions,
        reviewed,
        verdict,
    )

    script = ScriptedRunner(
        pjm=[classified()],
        product_analyst_interviewer=[questions(1), questions(done=True)],
        pm_interviewee=[answers(1)],
        product_analyst=[defined()],
        pm=[verdict("approved")],
        tech_lead_ephemeral=[questions(done=True)],
        tech_lead=[planned()],
        developer=[delivered()],
        tech_lead_review=[reviewed("approved")],
    )
    ports = memory_ports(script)
    ports.workspace.moved.add(MemoryWorkspace.ROOT / "GH-1")
    app = build_graph(lambda cfg: ports, arm_for=composition._arm_for)
    monkeypatch.setattr(cli, "graph", app)
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIC_TASK_MAX_BUDGET_USD", "0.7")
    cfg = Configuration.from_sources({}, os.environ)
    saver = Saver()
    task = {"id": "GH-1", "title": "Export", "description": "CSV.\n\n## Decided\n- csv\n"}

    with pytest.raises(SystemExit):
        await cli._run(task, cfg, ports, checkpointer=saver)
    thread = {"configurable": {"thread_id": "GH-1"}}
    paused = await app.compile(checkpointer=saver.saver).aget_state(thread)
    assert paused.values["decided"] is True

    monkeypatch.setenv("AGENTIC_TASK_BUDGET_USD", "50")
    raised = Configuration.from_sources({}, os.environ)
    await cli._resume("GH-1", raised, ports, yes=True, checkpointer=saver)
    resumed = await app.compile(checkpointer=saver.saver).aget_state(thread)
    assert resumed.values["decided"] is True
    # The definition interview ran its one round: the interviewer asked once, then closed.
    assert script.slugs.count("product_analyst_interviewer") == 2


@pytest.mark.anyio
async def test_the_log_file_travels_in_configurable_to_every_node(monkeypatch, tmp_path):
    """`_run` puts the string next to `thread_id` in the config it hands `ainvoke`
    (invariant 12: a `str`, never a `Path`); each node reads it back off its own
    `config["configurable"]` (`configuration_from`, `agentic/core/process/adapters/
    langgraph/graph.py`) and `default_ports` opens the file from it. A real compiled
    graph over a fake saver is the seam: `aget_state` itself does not echo custom
    `configurable` keys back once a checkpoint exists (only `thread_id`,
    `checkpoint_ns` and `checkpoint_id` are its own business), so the node itself,
    not a state snapshot, is where this round trip is provable."""
    from agentic.core.process.adapters.langgraph.graph import build_graph
    from agentic.shared.configuration import Configuration
    from tests.fakes import ScriptedRunner, classified, memory_ports

    seen: list[Configuration] = []

    def ports_for(cfg: Configuration):
        seen.append(cfg)
        return memory_ports(ScriptedRunner(pjm=[classified()]))

    graph = build_graph(ports_for, arm_for=composition._arm_for)
    monkeypatch.setattr(cli, "graph", graph)
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(tmp_path))
    cfg = Configuration.from_sources({}, os.environ)
    saver, task = Saver(), {"id": "GH-1", "title": "Export", "description": "CSV."}
    log_file = str(tmp_path / ".agentic/logs/GH-1-20260921T000000Z.log")

    with pytest.raises(AssertionError, match="no scripted result"):
        # Only `pjm` is scripted: the next node crashes, which is fine -- `pjm`'s own
        # node already ran and recorded what `configurable` handed it.
        await cli._run(task, cfg, memory_ports(), checkpointer=saver, log_file=log_file)

    assert seen and seen[0].log_file == Path(log_file)


def unavailable_inhibitor(monkeypatch, seen: list[str]):
    from contextlib import contextmanager

    @contextmanager
    def hold(why):
        seen.append(why)
        yield "systemd-inhibit not found"

    monkeypatch.setattr(cli, "inhibitor", SimpleNamespace(hold=hold))


@pytest.mark.anyio
async def test_run_says_once_that_the_inhibitor_is_unavailable_and_goes_on(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.process.adapters.langgraph.graph import build_graph
    from agentic.shared.configuration import Configuration
    from tests.fakes import ScriptedRunner, classified, memory_ports

    graph = build_graph(
        lambda cfg: memory_ports(ScriptedRunner(pjm=[classified()])), arm_for=composition._arm_for
    )
    monkeypatch.setattr(cli, "graph", graph)
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(tmp_path))
    held: list[str] = []
    unavailable_inhibitor(monkeypatch, held)
    cfg = Configuration.from_sources({}, os.environ)
    task = {"id": "GH-1", "title": "Export", "description": "CSV."}

    with pytest.raises(AssertionError, match="no scripted result"):
        # The run proceeds into the graph past the warning: it was not refused.
        await cli._run(task, cfg, memory_ports(), checkpointer=Saver())

    err = capsys.readouterr().err
    assert (
        err.count("agentic: sleep/shutdown inhibitor unavailable: systemd-inhibit not found") == 1
    )
    assert held == ["GH-1: run"]


@pytest.mark.anyio
async def test_resume_says_once_that_the_inhibitor_is_unavailable_and_goes_on(
    monkeypatch, tmp_path, capsys
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(tmp_path))
    held: list[str] = []
    unavailable_inhibitor(monkeypatch, held)
    ports = memory_ports()
    ports.documents.save(
        {"id": "GH-1", "title": "t", "description": "d", "escalated": "Developer stopped"}
    )
    cfg = Configuration.from_sources({}, os.environ)

    with pytest.raises(Exception):  # noqa: B017 -- what follows the warning is not the point
        await cli._resume("GH-1", cfg, ports, yes=True, checkpointer=Saver())

    err = capsys.readouterr().err
    assert (
        err.count("agentic: sleep/shutdown inhibitor unavailable: systemd-inhibit not found") == 1
    )
    assert held == ["GH-1: resume"]


@pytest.mark.anyio
async def test_a_resume_emits_only_what_it_spent_never_the_tasks_whole_cost(
    monkeypatch, tmp_path, capsys
):
    """`_invoke`'s two invocations each open their own task span (ADR-0037: no meter,
    no sample -- the whole fact now lives on the two spans themselves): a resume
    stamping the task's whole accumulated cost again, instead of only what it spent
    itself, is exactly the bug the request opens with (README.md, 14)."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from agentic.core.measurement.adapters.otel import telemetry
    from agentic.core.process.adapters.langgraph.graph import build_graph
    from agentic.shared.configuration import Configuration
    from tests.fakes import (
        MemoryWorkspace,
        ScriptedRunner,
        answers,
        classified,
        defined,
        delivered,
        memory_ports,
        planned,
        questions,
        reviewed,
        verdict,
    )

    script = ScriptedRunner(
        pjm=[classified()],
        product_analyst_interviewer=[questions(1), questions(done=True)],
        pm_interviewee=[answers(1)],
        product_analyst=[defined()],
        pm=[verdict("approved")],
        tech_lead_ephemeral=[questions(done=True)],
        tech_lead=[planned()],
        developer=[delivered()],
        tech_lead_review=[reviewed("approved")],
    )
    ports = memory_ports(script)
    ports.workspace.moved.add(MemoryWorkspace.ROOT / "GH-1")
    monkeypatch.setattr(cli, "graph", build_graph(lambda cfg: ports, arm_for=composition._arm_for))
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIC_TASK_MAX_BUDGET_USD", "0.7")
    cfg = Configuration.from_sources({}, os.environ)
    saver, task = Saver(), {"id": "GH-1", "title": "Export", "description": "CSV."}

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    try:
        with pytest.raises(SystemExit) as info:
            await cli._run(task, cfg, ports, checkpointer=saver)
        assert info.value.code == 1
        monkeypatch.setenv("AGENTIC_TASK_BUDGET_USD", "50")
        raised = Configuration.from_sources({}, os.environ)
        await cli._resume("GH-1", raised, ports, yes=True, checkpointer=saver)
        assert "status:    ready_for_review" in capsys.readouterr().out
    finally:
        telemetry.configure()

    task_spans = [s for s in exporter.get_finished_spans() if s.name == "GH-1"]
    run_span, resume_span = task_spans
    assert run_span.attributes["agentic.invocation.kind"] == "run"
    assert run_span.attributes["task.status"] == "waiting_on_human"
    assert resume_span.attributes["agentic.invocation.kind"] == "resume"
    assert resume_span.attributes["task.status"] == "ready_for_review"

    # One pause, by the task cap's class (`agentic.task.human_decisions` is covered at
    # its own seam, `test_telemetry.py`, where a decision is easy to script directly).
    assert run_span.attributes["agentic.task.paused"] == "cap"
    assert "agentic.task.paused" not in resume_span.attributes

    run_cost = run_span.attributes["agentic.invocation.cost_usd"]
    resume_cost = resume_span.attributes["agentic.invocation.cost_usd"]
    total_cost = resume_span.attributes["task.cost_usd"]
    # The resume's own delta, never the task's running total repeated a second time.
    assert 0 < resume_cost < total_cost
    assert run_cost + resume_cost == pytest.approx(total_cost)


@pytest.mark.anyio
async def test_run_refuses_a_task_whose_document_is_paused_unless_fresh(tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports = memory_ports()
    task = {"id": "GH-12", "title": "t", "description": "d"}
    ports.documents.save({**task, "escalated": "Developer stopped by the task cap"})
    cfg = Configuration(product_dir=tmp_path)
    with pytest.raises(cli.CannotStart) as info:
        await cli._run(task, cfg, ports, checkpointer=Saver())
    assert "per its document" in str(info.value) and "agentic resume --issue 12" in str(info.value)
    assert info.value.code == 2


def test_a_dirty_stage0_is_said_out_loud_and_the_run_goes_on(monkeypatch, tmp_path, capsys):
    """A warning, not a refusal: the run is worth less, it is not wrong (issue #44)."""
    root = product(tmp_path, "p")
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: "stage0 has uncommitted changes")
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    cli.main()
    assert "stage0 has uncommitted changes" in capsys.readouterr().err
    assert seen["task"]["title"] == "t"


def test_a_stage0_fixed_to_a_sha_says_nothing(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    # The product declares no Brief either (ADR-0025): written here so this test stays
    # about stage0 alone, not the separate warning `no_brief` owns.
    (root / "BRIEF.md").write_text(
        "## Problem and who has it\n\nx.\n\n## Jobs\n\nx.\n\n## Constraints\n\nx.\n\n"
        "## Out of scope\n\nx.\n"
    )
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    # `STACK` declares no `forbidden` either (issue #38): stubbed the same way, so this
    # test stays about stage0 alone, not the separate warning `no_forbidden` owns.
    monkeypatch.setattr(cli, "no_forbidden", lambda ports, revision: None)
    capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    # A real terminal, so the no-TTY warning (subtask `--detach`) does not fire here
    # either: this test is about stage0 alone, same as pytest's own stdin is not one.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    cli.main()
    assert capsys.readouterr().err == ""


def test_a_product_that_declares_no_stack_does_not_run(monkeypatch, tmp_path, capsys):
    """Mandatory since #36: the system has no default a run can reach, so the refusal
    carries the template the human copies."""
    root = product(tmp_path, "p", stack=None)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    error = capsys.readouterr().err
    assert "declares no stack" in error and "checks:" in error


def test_a_stack_the_working_tree_fixed_but_no_commit_holds_does_not_run(
    monkeypatch, tmp_path, capsys
):
    """The commit decides, here as everywhere (ADR-0016, addendum): a file written and
    not committed is not a declaration."""
    root = product(tmp_path, "p", stack=None)
    (root / ".agentic/stack.yaml").write_text("product: p\nchecks:\n  tests: pytest\n")
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert "declares no stack" in capsys.readouterr().err


def test_a_product_without_setup_is_warned_and_the_run_goes_on(monkeypatch, tmp_path, capsys):
    """A warning, not a refusal: a product with no dependencies prepares nothing, and
    one that does may still choose to live with a shared environment (issue #37)."""
    root = product(tmp_path, "p", stack="product: p\nchecks:\n  tests: pytest\n")
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    cli.main()
    assert "declares no `setup`" in capsys.readouterr().err
    assert seen["task"]["title"] == "t"


def test_a_resume_checks_the_stack_at_the_tasks_base_not_only_at_head(
    monkeypatch, tmp_path, capsys
):
    """A task whose branch started before the product declared a stack would pass the
    check at HEAD and then die inside a node, where nothing escalates."""
    root = product(tmp_path, "p", stack=None)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "branch", "agentic/GH-9"], cwd=root, check=True)
    base = subprocess.run(
        [*git, "rev-parse", "--short", "agentic/GH-9"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run([*git, "config", "branch.agentic/GH-9.agenticBase", base], cwd=root, check=True)
    (root / ".agentic/stack.yaml").write_text("product: p\nchecks:\n  tests: pytest\n")
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "stack"], cwd=root, check=True)

    monkeypatch.setattr(
        "sys.argv", ["agentic", "resume", "--issue", "9", "--product-dir", str(root)]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert "declares no stack" in capsys.readouterr().err


VALID_BRIEF = (
    "# Brief\n\n"
    "## Problem and who has it\n\nGym owners cannot tell who is about to churn.\n\n"
    "## Jobs\n\n- When a member skips, I want a flag, so I can call them.\n\n"
    "## Constraints\n\n- Scale: up to 200k members.\n\n"
    "## Out of scope\n\nPayment processing.\n"
)
MALFORMED_BRIEF = "# Brief\n\n## Problem and who has it\n\nOnly one section.\n"


def test_a_product_without_a_brief_is_warned_on_run_and_the_run_goes_on(
    monkeypatch, tmp_path, capsys
):
    """ADR-0025: absent is a fact, never a refusal -- gym-saas keeps running until its
    human writes one."""
    root = product(tmp_path, "p")
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    cli.main()
    error = capsys.readouterr().err
    assert "the product has no BRIEF.md" in error
    assert "## Problem and who has it" in error  # the template, printed verbatim
    assert seen["task"]["title"] == "t"


def test_a_product_without_a_brief_is_warned_on_resume_too(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    seen = capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "resume", "--issue", "9", "--yes", "--product-dir", str(root)]
    )
    cli.main()
    assert "the product has no BRIEF.md" in capsys.readouterr().err
    assert seen["fn"] is cli._resume


def test_a_valid_brief_says_nothing(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    (root / "BRIEF.md").write_text(VALID_BRIEF)
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    monkeypatch.setattr(cli, "no_forbidden", lambda ports, revision: None)
    capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    cli.main()
    assert capsys.readouterr().err == ""


def test_a_malformed_brief_refuses_a_run(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    (root / "BRIEF.md").write_text(MALFORMED_BRIEF)
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    error = capsys.readouterr().err
    assert "BRIEF.md is missing or empty" in error and "## Jobs" in error


def test_a_malformed_brief_is_printed_on_resume_and_it_proceeds(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    (root / "BRIEF.md").write_text(MALFORMED_BRIEF)
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    seen = capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "resume", "--issue", "9", "--yes", "--product-dir", str(root)]
    )
    cli.main()
    assert "BRIEF.md is missing or empty" in capsys.readouterr().err
    assert seen["fn"] is cli._resume  # a warning, not a refusal: the resume proceeds


def test_a_long_brief_and_an_over_cap_criteria_file_do_not_refuse_a_run(
    monkeypatch, tmp_path, capsys
):
    """GH-334, the apostrophe case: a 562-word valid BRIEF.md and a 611-line
    DESIGN_STANDARDS.md at HEAD. The Brief has no cap; the criteria file is said."""
    long_brief = VALID_BRIEF.replace(
        "Gym owners cannot tell who is about to churn.",
        " ".join(f"word{i}" for i in range(524)),
    )
    assert len(long_brief.split()) == 562
    root = product(tmp_path, "p")
    (root / "BRIEF.md").write_text(long_brief)
    _commit_file(root, "DESIGN_STANDARDS.md", 611)
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])

    cli.main()  # raises on a refusal

    error = capsys.readouterr().err
    assert "agentic: `DESIGN_STANDARDS.md`: 611 lines (cap 300)" in error
    assert "words" not in error
    assert seen["task"]["title"] == "t"


class FakeLock:
    """A `RunLock` double: no real process, no real file. `held` is what a live process
    already wrote, if any; `taken_over` is what `acquire` hands back for a dead one."""

    def __init__(self, held: LockRecord | None = None, taken_over: LockRecord | None = None):
        self.held = held
        self.taken_over = taken_over
        self.acquired: list[tuple[str, str]] = []
        self.released: list[str] = []
        self.kept: list[str] = []
        self.kept_crashed: list[tuple[str, str]] = []

    def read(self, task_id):
        return self.held

    def keep_at_shutdown(self, task_id):
        self.kept.append(task_id)

    def keep_crashed(self, task_id, system_version):
        self.kept_crashed.append((task_id, system_version))

    def acquire(self, task_id, command):
        self.acquired.append((task_id, command))
        message = self.refusal(task_id)
        if message is not None:
            raise LockHeld(self.held, message)
        self.held = LockRecord(os.getpid(), "h", "2026-09-21T00:00:00Z", command)
        return self.taken_over

    def refusal(self, task_id):
        if self.held is None:
            return None
        return f"{task_id} is already running: pid {self.held.pid}"

    def release(self, task_id):
        self.released.append(task_id)
        self.held = None


def lock_record(
    pid=4321, host="elsewhere", started_at="2026-09-15T00:00:00Z", command="agentic run"
):
    return LockRecord(pid, host, started_at, command)


class Delivers:
    """A graph double whose `ainvoke` returns normally, to exercise the lock around a
    successful invocation without the full scripted graph."""

    def __init__(self, final=None):
        self.final = final or {"id": "T-9", "title": "t", "status": "ready_for_review"}

    def compile(self, checkpointer):
        return self

    async def aget_state(self, config):
        return SimpleNamespace(values={}, next=(), interrupts=())

    async def aupdate_state(self, config, values, as_node=None):
        pass

    async def ainvoke(self, task, config):
        return self.final


@pytest.mark.anyio
async def test_run_refuses_when_the_lock_is_held_and_touches_nothing(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers())
    lock = FakeLock(held=lock_record())
    task = {"id": "T-9", "title": "t", "description": "d"}
    with pytest.raises(cli.CannotStart, match="T-9 is already running"):
        await cli._run(
            task,
            Configuration(product_dir=tmp_path),
            memory_ports(),
            checkpointer=in_memory,
            run_lock=lock,
        )
    assert lock.released == []  # never acquired: nothing to release


@pytest.mark.anyio
async def test_a_run_whose_graph_breaks_keeps_the_lock_marked_as_crashed(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    class Broken(Delivers):
        async def ainvoke(self, task, config):
            raise RuntimeError("boom")

    monkeypatch.setattr(cli, "graph", Broken())
    monkeypatch.setattr(cli.telemetry, "system_version", lambda: "abc1234")
    lock = FakeLock()
    task = {"id": "T-9", "title": "t", "description": "d"}
    with pytest.raises(RuntimeError, match="boom"):
        await cli._run(
            task,
            Configuration(product_dir=tmp_path),
            memory_ports(),
            checkpointer=in_memory,
            run_lock=lock,
            stop=cli.StopCause(),
        )
    assert lock.acquired == [("T-9", " ".join(__import__("sys").argv))]
    assert lock.kept_crashed == [("T-9", "abc1234")]
    assert lock.released == []
    assert lock.kept == []


@pytest.mark.anyio
async def test_a_run_with_no_lock_injected_uses_the_products_own_runs_dir(monkeypatch, tmp_path):
    from agentic.core.process.adapters.filesystem import FilesystemRunLock
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "T-9", "title": "t", "status": "open"}))
    cfg = Configuration(product_dir=tmp_path)
    task = {"id": "T-9", "title": "t", "description": "d"}
    await cli._run(task, cfg, memory_ports(), checkpointer=in_memory)
    assert FilesystemRunLock(cfg.agentic_dir / "runs").read("T-9") is None


@pytest.mark.anyio
async def test_a_run_with_no_registry_injected_leaves_nothing_in_the_watched_directory(
    monkeypatch, tmp_path
):
    """The `no_real_run_registry` fixture's own contract, proved rather than assumed: a
    `run` with no `registry=` of its own goes through the real `FilesystemRunRegistry`
    (`cli._registry_for`), and `Configuration.run_registry_dir`'s default reads
    `XDG_STATE_HOME` straight off the environment (`_default_run_registry_dir`), not
    through `from_sources`. If the fixture ever stopped redirecting that variable, this
    `run` would register a live pid under the real path of whatever machine runs the
    suite instead of this test's own `watched` directory -- which it leaves empty,
    `register`/`unregister` having run and cleared inside `_locked`."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    watched = tmp_path / "watched-xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(watched))
    monkeypatch.setattr(cli, "graph", Delivers({"id": "T-9", "title": "t", "status": "open"}))
    cfg = Configuration(product_dir=tmp_path / "product")
    task = {"id": "T-9", "title": "t", "description": "d"}
    await cli._run(task, cfg, memory_ports(), checkpointer=in_memory)
    assert cfg.run_registry_dir == watched / "agentic" / "runs"
    assert list(cfg.run_registry_dir.glob("*.json")) == []


@pytest.mark.anyio
async def test_a_run_registers_itself_and_unregisters_once_it_finishes(monkeypatch, tmp_path):
    """GH-137: `run` registers under `Configuration.run_registry_dir` inside its own
    lock, so the poller's concurrency cap has something to count, and clears it once
    the invocation is done -- the product's name comes off `ports.stack.product()`,
    `MemoryStack`'s own default."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import MemoryRunRegistry, memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "T-9", "title": "t", "status": "open"}))
    registry = MemoryRunRegistry()
    task = {"id": "T-9", "title": "t", "description": "d"}
    await cli._run(
        task,
        Configuration(product_dir=tmp_path),
        memory_ports(),
        checkpointer=in_memory,
        registry=registry,
    )
    assert registry.registered == [("product", "T-9")]
    assert registry.unregistered == 1


@pytest.mark.anyio
async def test_a_run_unregisters_even_if_the_graph_breaks(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import MemoryRunRegistry, memory_ports

    class Broken(Delivers):
        async def ainvoke(self, task, config):
            raise RuntimeError("boom")

    monkeypatch.setattr(cli, "graph", Broken())
    registry = MemoryRunRegistry()
    task = {"id": "T-9", "title": "t", "description": "d"}
    with pytest.raises(RuntimeError, match="boom"):
        await cli._run(
            task,
            Configuration(product_dir=tmp_path),
            memory_ports(),
            checkpointer=in_memory,
            registry=registry,
        )
    assert registry.registered == [("product", "T-9")]
    assert registry.unregistered == 1


@pytest.mark.anyio
async def test_a_run_still_happens_when_the_registry_cannot_be_written(
    monkeypatch, tmp_path, capsys
):
    """Best effort only (GH-137): a registry that cannot write must not turn into a
    `CannotStart` -- nothing about the task itself failed."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    class BrokenRegistry:
        def register(self, product, task_id, kind="role"):
            raise OSError("disk full")

        def unregister(self):
            raise OSError("disk full")

    monkeypatch.setattr(cli, "graph", Delivers({"id": "T-9", "title": "t", "status": "open"}))
    task = {"id": "T-9", "title": "t", "description": "d"}
    await cli._run(
        task,
        Configuration(product_dir=tmp_path),
        memory_ports(),
        checkpointer=in_memory,
        registry=BrokenRegistry(),
    )
    assert "could not register the run" in capsys.readouterr().err


@pytest.mark.anyio
async def test_resume_refuses_when_the_lock_is_held(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers())
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    lock = FakeLock(held=lock_record())
    with pytest.raises(cli.CannotStart, match="GH-1 is already running"):
        await cli._resume(
            "GH-1",
            Configuration(product_dir=tmp_path),
            ports,
            from_document=True,
            yes=True,
            checkpointer=in_memory,
            run_lock=lock,
        )
    assert ports.tracker.comments == []  # refused before announcing anything


@pytest.mark.anyio
async def test_a_dry_run_never_acquires_the_lock_and_prints_it_instead(
    monkeypatch, tmp_path, capsys
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers())
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    held = lock_record(host="h2", started_at="2026-09-15T00:00:00Z")
    lock = FakeLock(held=held)
    await cli._resume(
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        from_document=True,
        dry_run=True,
        checkpointer=in_memory,
        run_lock=lock,
    )
    assert lock.acquired == []
    out = capsys.readouterr().out
    assert str(held.pid) in out and held.host in out


@pytest.mark.anyio
async def test_a_resumed_run_acquires_and_releases_the_lock_and_a_takeover_is_printed(
    monkeypatch, tmp_path, capsys
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "GH-1", "title": "t", "status": "open"}))
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    dead = lock_record(pid=999_999_999, host="h", started_at="2026-09-01T00:00:00Z")
    lock = FakeLock(taken_over=dead)
    await cli._resume(
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        from_document=True,
        yes=True,
        checkpointer=in_memory,
        run_lock=lock,
    )
    assert lock.acquired[0][0] == "GH-1" and lock.released == ["GH-1"]
    out = capsys.readouterr().out
    assert "999999999" in out
    assert ports.tracker.comments  # announced normally: the lock did not stop it


@pytest.mark.anyio
async def test_a_resumed_run_whose_graph_breaks_keeps_the_lock_marked_as_crashed(
    monkeypatch, tmp_path
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    class Broken(Delivers):
        async def ainvoke(self, task, config):
            raise RuntimeError("boom")

    monkeypatch.setattr(cli, "graph", Broken())
    monkeypatch.setattr(cli.telemetry, "system_version", lambda: "abc1234")
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    lock = FakeLock()
    with pytest.raises(RuntimeError, match="boom"):
        await cli._resume(
            "GH-1",
            Configuration(product_dir=tmp_path),
            ports,
            from_document=True,
            yes=True,
            checkpointer=in_memory,
            run_lock=lock,
            stop=cli.StopCause(),
        )
    assert lock.kept_crashed == [("GH-1", "abc1234")]
    assert lock.released == []


def _paused_gh1(ports):
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]


@pytest.mark.anyio
@pytest.mark.parametrize("dead", [True, False])
async def test_a_takeover_reaps_the_dead_runs_own_scope_and_no_takeover_reaps_nothing(
    monkeypatch, tmp_path, capsys, dead
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeReaper, memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "GH-1", "title": "t", "status": "open"}))
    left = ({"pid": 5, "command": "uv run sample_app.py", "outcome": "kill"},)
    reaper = FakeReaper(left, reaped_runs=left)
    ports = memory_ports(reaper=reaper)
    _paused_gh1(ports)
    record = lock_record(pid=999_999_999, host="h", started_at="2026-09-01T00:00:00Z")
    await cli._resume(
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        from_document=True,
        yes=True,
        checkpointer=in_memory,
        run_lock=FakeLock(taken_over=record if dead else None),
    )
    err = capsys.readouterr().err
    if dead:
        assert reaper.calls == [
            ("run", "GH-1", 999_999_999, None),  # never the whole task
            ("run", "check", 999_999_999, None),
            ("run", "perf", 999_999_999, None),
        ]
        assert "reaped 3 processes: `uv run sample_app.py` (kill), " in err
    else:
        assert reaper.calls == [] and "reaped" not in err


@pytest.mark.anyio
async def test_a_live_lock_refuses_the_run_before_anything_is_reaped(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeReaper, memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "GH-1", "title": "t", "status": "open"}))
    reaper = FakeReaper()
    ports = memory_ports(reaper=reaper)
    _paused_gh1(ports)
    with pytest.raises(cli.CannotStart):
        await cli._resume(
            "GH-1",
            Configuration(product_dir=tmp_path),
            ports,
            from_document=True,
            yes=True,
            checkpointer=in_memory,
            run_lock=FakeLock(held=lock_record(pid=os.getpid(), host="h")),
        )
    assert reaper.calls == []


@pytest.mark.anyio
async def test_a_run_takeover_reaps_the_dead_runs_own_scope(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeReaper, memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "T-9", "title": "t", "status": "open"}))
    reaper = FakeReaper()
    ports = memory_ports(reaper=reaper)
    task = {"id": "T-9", "title": "t", "description": "d"}
    dead = lock_record(pid=4444, host="h")
    await cli._run(
        task,
        Configuration(product_dir=tmp_path),
        ports,
        checkpointer=in_memory,
        run_lock=FakeLock(taken_over=dead),
    )
    assert reaper.calls == [
        ("run", "T-9", 4444, None),
        ("run", "check", 4444, None),
        ("run", "perf", 4444, None),
    ]


@pytest.mark.anyio
async def test_a_resume_registers_itself_and_unregisters_once_it_finishes(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import MemoryRunRegistry, memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "GH-1", "title": "t", "status": "open"}))
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    registry = MemoryRunRegistry()
    await cli._resume(
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        from_document=True,
        yes=True,
        checkpointer=in_memory,
        registry=registry,
    )
    assert registry.registered == [("product", "GH-1")]
    assert registry.unregistered == 1


def test_a_product_that_declares_no_name_does_not_run(monkeypatch, tmp_path, capsys):
    """Without it every span and every metric of the run lands unlabelled, and the two
    products add up in every panel (issue #47)."""
    root = product(tmp_path, "p", stack="checks:\n  tests: pytest\n")
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert "expected `product`" in capsys.readouterr().err


class NeverFires:
    """A stand-in for `anyio.open_signal_receiver(...)`: never yields a signal, but
    stays cancellable like the real one -- no OS signal, no real event loop handler."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await anyio.sleep_forever()


class FiresOnce:
    """A stand-in for `anyio.open_signal_receiver(...)` that yields one signal as soon
    as it is awaited, with no real OS signal and no real event loop handler involved."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return signal.SIGTERM


class Hangs:
    """A graph double whose `ainvoke` never returns on its own: what a SIGTERM has to
    cancel out of, mid-role, the way a real Claude Code stream would hang until killed."""

    def compile(self, checkpointer):
        return self

    async def aget_state(self, config):
        return SimpleNamespace(values={}, next=(), interrupts=())

    async def ainvoke(self, task, config):
        await anyio.sleep_forever()


@pytest.mark.anyio
async def test_a_refusal_from_work_is_not_wrapped_by_the_signal_guard(monkeypatch):
    """`CannotStart` (or any other exception `work` raises) must reach `main`'s bare
    `except CannotStart` unchanged: anyio wraps whatever escapes a task group in an
    `ExceptionGroup`, even a lone exception, so `_signal_guarded` has to catch it inside
    the group and re-raise it after -- verified here, not assumed."""
    monkeypatch.setattr(cli.anyio, "open_signal_receiver", lambda *sigs: NeverFires())

    async def work():
        raise cli.CannotStart("nope", code=2)

    with pytest.raises(cli.CannotStart, match="nope") as info:
        await cli._signal_guarded(work)
    assert info.value.code == 2


@pytest.mark.anyio
async def test_a_signal_cancels_the_invocation_releases_the_lock_and_exits_nonzero(
    monkeypatch, tmp_path
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Hangs())
    monkeypatch.setattr(cli.anyio, "open_signal_receiver", lambda *sigs: FiresOnce())
    lock = FakeLock()
    task = {"id": "T-9", "title": "t", "description": "d"}
    work = partial(
        cli._run,
        task,
        Configuration(product_dir=tmp_path),
        memory_ports(),
        checkpointer=in_memory,
        run_lock=lock,
    )
    with pytest.raises(SystemExit) as info:
        await cli._signal_guarded(work)
    assert info.value.code != 0
    assert lock.released == ["T-9"]  # `_locked`'s own `finally`, surviving cancellation


async def _signalled_run(monkeypatch, tmp_path, lock, probe):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Hangs())
    monkeypatch.setattr(cli.anyio, "open_signal_receiver", lambda *sigs: FiresOnce())
    stop = cli.StopCause()
    work = partial(
        cli._run,
        {"id": "T-9", "title": "t", "description": "d"},
        Configuration(product_dir=tmp_path),
        memory_ports(),
        checkpointer=in_memory,
        run_lock=lock,
        stop=stop,
    )
    with pytest.raises(SystemExit):
        await cli._signal_guarded(work, stop, shutdown_probe=probe)
    return stop


@pytest.mark.anyio
async def test_a_signal_at_system_shutdown_keeps_the_lock_record_marked(monkeypatch, tmp_path):
    from agentic.core.process.adapters.filesystem import FilesystemRunLock

    lock = FilesystemRunLock(tmp_path / "locks", boot_id=lambda: "boot-1")
    stop = await _signalled_run(monkeypatch, tmp_path, lock, lambda: True)
    assert stop.at_shutdown is True
    record = lock.read("T-9")
    assert record is not None
    assert record.stopped_at_shutdown is True
    assert record.boot_id == "boot-1"


@pytest.mark.anyio
async def test_a_signal_outside_a_shutdown_releases_the_lock(monkeypatch, tmp_path):
    from agentic.core.process.adapters.filesystem import FilesystemRunLock

    lock = FilesystemRunLock(tmp_path / "locks", boot_id=lambda: "boot-1")
    stop = await _signalled_run(monkeypatch, tmp_path, lock, lambda: False)
    assert stop.at_shutdown is False
    assert lock.read("T-9") is None


def test_each_invocation_has_its_own_stop_cause():
    first, second = cli.StopCause(), cli.StopCause()
    first.at_shutdown = True
    first.crashed = True
    first.signalled = True
    assert (second.at_shutdown, second.crashed, second.signalled) == (False, False, False)


class RaisesWhenCancelled(Hangs):
    """A graph whose `ainvoke` turns the signal's cancellation into a `RuntimeError`, as a
    node that fails while it is being torn down would: the graph raised after a signal."""

    async def ainvoke(self, task, config):
        try:
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
            raise RuntimeError("boom while stopping") from None


async def _signalled_then_raising_run(monkeypatch, tmp_path, lock, probe):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", RaisesWhenCancelled())
    monkeypatch.setattr(cli.anyio, "open_signal_receiver", lambda *sigs: FiresOnce())
    stop = cli.StopCause()
    work = partial(
        cli._run,
        {"id": "T-9", "title": "t", "description": "d"},
        Configuration(product_dir=tmp_path),
        memory_ports(),
        checkpointer=in_memory,
        run_lock=lock,
        stop=stop,
    )
    with pytest.raises(RuntimeError, match="boom while stopping"):
        await cli._signal_guarded(work, stop, shutdown_probe=probe)
    return stop


@pytest.mark.anyio
async def test_a_signal_followed_by_a_graph_exception_releases_the_lock_unmarked(
    monkeypatch, tmp_path
):
    lock = FakeLock()
    stop = await _signalled_then_raising_run(monkeypatch, tmp_path, lock, lambda: False)
    assert (stop.signalled, stop.crashed, stop.at_shutdown) == (True, True, False)
    assert lock.released == ["T-9"]
    assert lock.kept_crashed == []
    assert lock.kept == []


@pytest.mark.anyio
async def test_a_shutdown_and_a_crash_keep_the_lock_for_the_shutdown_only(monkeypatch, tmp_path):
    lock = FakeLock()
    stop = await _signalled_then_raising_run(monkeypatch, tmp_path, lock, lambda: True)
    assert (stop.signalled, stop.crashed, stop.at_shutdown) == (True, True, True)
    assert lock.kept == ["T-9"]
    assert lock.kept_crashed == []
    assert lock.released == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", [OSError("disk full"), cli.CannotStart("cannot archive", code=2)]
)
async def test_an_exception_outside_the_graph_releases_the_lock_unmarked(
    monkeypatch, tmp_path, failure
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    def archive_fails(task_id, ports):
        raise failure

    monkeypatch.setattr(cli, "graph", Delivers())
    monkeypatch.setattr(cli, "_archive_previous_run", archive_fails)
    lock = FakeLock()
    stop = cli.StopCause()
    with pytest.raises(type(failure)):
        await cli._run(
            {"id": "T-9", "title": "t", "description": "d"},
            Configuration(product_dir=tmp_path),
            memory_ports(),
            checkpointer=in_memory,
            run_lock=lock,
            stop=stop,
        )
    assert stop.crashed is False
    assert lock.released == ["T-9"]
    assert lock.kept_crashed == []


def test_system_is_stopping_only_on_exactly_stopping(monkeypatch):
    def answering(text):
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=text, stderr="")

    for output, expected in (
        ("stopping\n", True),
        ("running\n", False),
        ("degraded\n", False),
        ("", False),
    ):
        monkeypatch.setattr(cli.subprocess, "run", answering(output))
        assert cli._system_is_stopping() is expected


def test_system_is_stopping_is_false_when_systemctl_is_missing_or_slow(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("systemctl", 2)

    for failing in (missing, slow):
        monkeypatch.setattr(cli.subprocess, "run", failing)
        assert cli._system_is_stopping() is False


@pytest.mark.anyio
async def test_a_signal_still_flushes_telemetry_and_closes_the_task_span(monkeypatch, tmp_path):
    """`_invoke`'s `finally: telemetry.flush()` and `task_span`'s own context manager
    exit are existing code, not new; this asserts they both survive a cancellation
    instead of assuming it in prose (Tech Lead plan, subtask `signals`)."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from agentic.core.measurement.adapters.otel import telemetry
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Hangs())
    monkeypatch.setattr(cli.anyio, "open_signal_receiver", lambda *sigs: FiresOnce())
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    flushes = []
    original_flush = telemetry.flush
    monkeypatch.setattr(telemetry, "flush", lambda: (flushes.append(1), original_flush())[0])
    task = {"id": "T-9", "title": "t", "description": "d"}
    work = partial(
        cli._run, task, Configuration(product_dir=tmp_path), memory_ports(), checkpointer=in_memory
    )
    try:
        with pytest.raises(SystemExit):
            await cli._signal_guarded(work)
    finally:
        telemetry.configure()
    assert flushes == [1]
    (span,) = exporter.get_finished_spans()
    assert span.name == "T-9"


@pytest.mark.anyio
async def test_an_uninterrupted_run_over_the_guard_comments_exactly_as_before(
    monkeypatch, tmp_path
):
    """Wiring `_signal_guarded` around the invocation changes nothing for a run that
    was never signalled: the issue comments are byte-for-byte what `_resume` alone
    would have written."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "GH-1", "title": "t", "status": "open"}))
    monkeypatch.setattr(cli.anyio, "open_signal_receiver", lambda *sigs: NeverFires())
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    work = partial(
        cli._resume,
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        from_document=True,
        yes=True,
        checkpointer=in_memory,
    )
    await cli._signal_guarded(work)
    assert ports.tracker.comments == [
        ("GH-1", "Resumed: diagnose, under a task cap of 250.00 USD.")
    ]


# --detach (subtask `--detach`): argparse refusals, seam `agentic.cli.main`.


def test_detach_on_resume_requires_yes(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "resume", "--issue", "1", "--detach"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_detach_and_dry_run_are_exclusive(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["agentic", "resume", "--issue", "1", "--detach", "--yes", "--dry-run"]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_no_tty_and_no_detach_warns_once(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    capture_run(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    cli.main()
    assert "dies if its session closes" in capsys.readouterr().err


def test_detach_silences_the_no_tty_warning(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    capture_run(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root), "--detach"]
    )
    cli.main()
    assert "dies if its session closes" not in capsys.readouterr().err


def test_child_argv_strips_detach_but_keeps_everything_else(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["/venv/bin/agentic", "resume", "--issue", "1", "--detach", "--yes"]
    )
    assert cli._child_argv() == ["/venv/bin/agentic", "resume", "--issue", "1", "--yes"]


def test_child_argv_resolves_a_relative_argv0_to_an_absolute_path(monkeypatch):
    """Acceptance (i): `_child_argv()[0]` is absolute even when `sys.argv[0]` is not --
    GH-140's own fix, through `_self_executable`, never a bare `sys.argv[0]` read."""
    monkeypatch.setattr("sys.argv", ["bin/agentic", "run", "t", "d"])
    argv0 = cli._child_argv()[0]
    assert os.path.isabs(argv0)
    assert argv0 == os.path.abspath("bin/agentic")


def test_spawn_detached_redirects_stdout_and_stderr_onto_the_log_before_any_role_runs(
    monkeypatch, tmp_path
):
    """Seam: the default `Spawner`. `start_new_session=True` and stdin closed, exactly
    as the plan requires; a fake `Popen` proves nothing real is spawned by the test."""
    log_path = tmp_path / "t.log"
    calls = {}

    class FakeProcess:
        pid = 424242

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    pid = cli._spawn_detached(["agentic", "resume", "--issue", "1", "--yes"], str(log_path))
    assert pid == 424242
    assert calls["argv"] == ["agentic", "resume", "--issue", "1", "--yes"]
    assert calls["kwargs"]["start_new_session"] is True
    assert calls["kwargs"]["stdin"] is cli.subprocess.DEVNULL
    assert calls["kwargs"]["stdout"] is calls["kwargs"]["stderr"]
    assert calls["kwargs"]["stdout"].name == str(log_path)
    assert calls["kwargs"]["env"][cli.ENV_PREFIX + "LOG_FILE"] == str(log_path)
    assert log_path.is_file()  # opened before the (faked) exec, never after


def test_spawn_detached_removes_the_log_file_it_created_when_popen_fails(tmp_path):
    """Acceptance (g): a `Popen` that raises `FileNotFoundError` (a poller's own
    `argv0` not found) must not leave an empty log file behind every round -- the file
    this very call created is removed before the exception propagates."""
    log_path = tmp_path / "logs" / "poll-run-5-20261004T000000Z.log"
    log_path.parent.mkdir(parents=True)
    missing = str(tmp_path / "does-not-exist")
    with pytest.raises(FileNotFoundError):
        cli._spawn_detached([missing, "run"], str(log_path))
    assert list(tmp_path.glob("logs/*")) == []


def test_spawn_detached_keeps_a_pre_existing_log_file_when_popen_fails(tmp_path):
    """Acceptance (h): a log path a previous invocation already wrote into is never
    this call's own file to delete, even when this call's own `Popen` then fails."""
    log_path = tmp_path / "logs" / "poll-run-5-20261004T000000Z.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("already here\n")
    missing = str(tmp_path / "does-not-exist")
    with pytest.raises(FileNotFoundError):
        cli._spawn_detached([missing, "run"], str(log_path))
    assert log_path.read_text() == "already here\n"


class FakeSpawn:
    """A `Spawner` double: no real process, just what argv and log path it was asked
    to spawn with, and the pid to hand back as if it were the child's."""

    def __init__(self, pid=54321):
        self.pid = pid
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv, log_file):
        self.calls.append((argv, log_file))
        return self.pid


@pytest.mark.anyio
async def test_run_detach_spawns_the_child_and_returns_without_touching_the_graph(
    monkeypatch, tmp_path, capsys
):
    """Seam: the injected spawner on `_run`. The parent never reaches `_locked` or
    `ainvoke`: `Broken.ainvoke` would fail the test if it somehow did."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    class Broken(Delivers):
        async def ainvoke(self, task, config):
            raise AssertionError("the graph must never run in the parent under --detach")

    monkeypatch.setattr(cli, "graph", Broken())
    spawn = FakeSpawn()
    lock = FakeLock()
    task = {"id": "T-9", "title": "t", "description": "d"}
    await cli._run(
        task,
        Configuration(product_dir=tmp_path),
        memory_ports(),
        checkpointer=in_memory,
        run_lock=lock,
        log_file="/tmp/x.log",
        detach=True,
        child_argv=["agentic", "run", "--issue", "1", "--detach"],
        spawn=spawn,
    )
    assert spawn.calls == [(["agentic", "run", "--issue", "1", "--detach"], "/tmp/x.log")]
    assert lock.acquired == []  # the parent never holds it -- the child does, after it re-execs
    assert "54321" in capsys.readouterr().out


@pytest.mark.anyio
async def test_resume_detach_spawns_after_the_summary_and_returns_without_invoking(
    monkeypatch, tmp_path, capsys
):
    """Seam: the injected spawner on `_resume`. The summary and (skipped, via --yes)
    confirmation already ran; `announce` and `ainvoke`, which only the child should do,
    must not have run in the parent."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    class Broken(Delivers):
        async def ainvoke(self, task, config):
            raise AssertionError("the graph must never run in the parent under --detach")

    monkeypatch.setattr(cli, "graph", Broken())
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    spawn = FakeSpawn()
    lock = FakeLock()
    child_argv = ["agentic", "resume", "--issue", "1", "--yes", "--detach"]
    await cli._resume(
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        from_document=True,
        yes=True,
        checkpointer=in_memory,
        run_lock=lock,
        log_file="/tmp/x.log",
        detach=True,
        child_argv=child_argv,
        spawn=spawn,
    )
    assert spawn.calls == [(child_argv, "/tmp/x.log")]
    assert lock.acquired == []
    assert ports.tracker.comments == []  # the child announces; the parent never did
    assert "54321" in capsys.readouterr().out


@pytest.mark.anyio
async def test_detach_refuses_when_the_lock_is_held_and_never_spawns(monkeypatch, tmp_path):
    """The parent's read-only liveness check: a refusal on this terminal, no lock
    write, and nothing spawned -- the same shape `_acquire` refuses with, but never
    written from a process about to exit (invariant: no subtask writes a lock from a
    process that then forks or exits)."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers())
    lock = FakeLock(held=lock_record(pid=os.getpid(), host=socket.gethostname()))
    spawn = FakeSpawn()
    task = {"id": "T-9", "title": "t", "description": "d"}
    with pytest.raises(cli.CannotStart, match="T-9 is already running"):
        await cli._run(
            task,
            Configuration(product_dir=tmp_path),
            memory_ports(),
            checkpointer=in_memory,
            run_lock=lock,
            log_file="/tmp/x.log",
            detach=True,
            child_argv=["agentic", "run", "--issue", "1", "--detach"],
            spawn=spawn,
        )
    assert spawn.calls == []
    assert lock.acquired == []


@pytest.mark.anyio
async def test_a_run_with_no_lock_injected_detaches_without_writing_the_products_lock(
    monkeypatch, tmp_path
):
    """No `run_lock` injected (the production shape, `_lock_for` builds a real
    `FilesystemRunLock`): the parent's liveness check reads it and finds nothing to
    refuse, and, since it never acquires, leaves no lock file behind for the child to
    trip over when it re-execs and acquires for real."""
    from agentic.core.process.adapters.filesystem import FilesystemRunLock
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers())
    cfg = Configuration(product_dir=tmp_path)
    spawn = FakeSpawn()
    task = {"id": "T-9", "title": "t", "description": "d"}
    await cli._run(
        task,
        cfg,
        memory_ports(),
        checkpointer=in_memory,
        log_file="/tmp/x.log",
        detach=True,
        child_argv=["agentic", "run", "--issue", "1", "--detach"],
        spawn=spawn,
    )
    assert spawn.calls
    assert FilesystemRunLock(cfg.agentic_dir / "runs").read("T-9") is None


# `--fresh` archives a previous run before dropping the thread (ADR-0011, sixth
# addendum, subtask `archive.py`, the `--fresh` call site and the resume prefix).


@pytest.mark.anyio
async def test_fresh_with_no_branches_and_no_document_makes_no_git_or_tracker_call(
    monkeypatch, tmp_path, capsys
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "T-9", "title": "t", "status": "open"}))
    ports = memory_ports()
    task = {"id": "T-9", "title": "t", "description": "d"}
    await cli._run(
        task, Configuration(product_dir=tmp_path), ports, fresh=True, checkpointer=in_memory
    )
    assert "archived" not in capsys.readouterr().out
    assert ports.workspace.renamed == []
    assert ports.workspace.commits == []
    assert ports.workspace.cleaned == []
    assert ports.workspace.pruned == 0
    assert ports.workspace.ignored_checks == []
    assert ports.documents.written_into == []
    assert ports.tracker.comments == []
    assert ports.tracker.labels == []
    assert ports.tracker.unlabeled == []


@pytest.mark.anyio
async def test_fresh_archives_the_previous_run_and_prints_the_line_before_invoking(
    monkeypatch, tmp_path, capsys
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "GH-1", "title": "t", "status": "open"}))
    ports = memory_ports()
    task = {"id": "GH-1", "title": "t", "description": "d"}
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234", "escalated": "the task cap"})
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")
    await cli._run(
        task, Configuration(product_dir=tmp_path), ports, fresh=True, checkpointer=in_memory
    )
    assert "agentic: archived previous run as archive/run1-abc1234" in capsys.readouterr().out
    assert ports.workspace.renamed == [("agentic/GH-1", "archive/run1-abc1234/agentic/GH-1")]
    assert ports.tracker.comments == [
        ("GH-1", "Started over: previous run archived as archive/run1-abc1234/")
    ]
    # The document said the task was escalated: restarting it takes it out of the queue.
    assert ports.tracker.unlabeled == [("GH-1", "needs-human")]


@pytest.mark.anyio
async def test_fresh_names_what_the_archive_reaped_in_the_line_and_the_comment(
    monkeypatch, tmp_path, capsys
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeReaper, memory_ports

    monkeypatch.setattr(cli, "graph", Delivers({"id": "GH-1", "title": "t", "status": "open"}))
    reaped = ({"pid": 7, "command": "uv run sample_app.py", "outcome": "kill"},)
    ports = memory_ports(reaper=FakeReaper(reaped))
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")
    task = {"id": "GH-1", "title": "t", "description": "d"}
    await cli._run(
        task, Configuration(product_dir=tmp_path), ports, fresh=True, checkpointer=in_memory
    )
    assert "reaped 1 process: `uv run sample_app.py` (kill)" in capsys.readouterr().out
    ((_, comment),) = ports.tracker.comments
    assert comment == (
        "Started over: previous run archived as archive/run1-abc1234/; "
        "reaped 1 process: `uv run sample_app.py` (kill)"
    )


@pytest.mark.anyio
async def test_detach_fresh_makes_no_archive_call_in_the_parent(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    class Broken(Delivers):
        async def ainvoke(self, task, config):
            raise AssertionError("the graph must never run in the parent under --detach")

    monkeypatch.setattr(cli, "graph", Broken())
    ports = memory_ports()
    ports.workspace.existing_branches["agentic/T-9"] = ("h1", "b0")
    spawn = FakeSpawn()
    lock = FakeLock()
    task = {"id": "T-9", "title": "t", "description": "d"}
    await cli._run(
        task,
        Configuration(product_dir=tmp_path),
        ports,
        fresh=True,
        checkpointer=in_memory,
        run_lock=lock,
        log_file="/tmp/x.log",
        detach=True,
        child_argv=["agentic", "run", "--issue", "1", "--fresh", "--detach"],
        spawn=spawn,
    )
    assert spawn.calls
    assert ports.workspace.renamed == []
    assert ports.tracker.comments == []


@pytest.mark.anyio
async def test_run_without_fresh_still_refuses_with_the_same_text_and_never_archives(tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports = memory_ports()
    task = {"id": "GH-12", "title": "t", "description": "d"}
    ports.documents.save({**task, "escalated": "Developer stopped by the task cap"})
    ports.workspace.existing_branches["agentic/GH-12"] = ("h1", "b0")
    with pytest.raises(cli.CannotStart) as info:
        await cli._run(task, Configuration(product_dir=tmp_path), ports, checkpointer=Saver())
    assert str(info.value) == (
        "GH-12 is paused, per its document: Developer stopped by the task cap. "
        "Resume it with `agentic resume --issue 12`, or start it over with --fresh."
    )
    assert info.value.code == 2
    # `_refuse_a_started_task` runs before `_locked`: archiving never even asked.
    assert ports.workspace.renamed == []


@pytest.mark.anyio
async def test_resume_from_document_still_reads_the_frontmatter_unchanged(tmp_path, capsys):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports = memory_ports()
    task_id = "GH-1"
    ports.documents.save({"id": task_id, "title": "t", "description": "d", "cost_usd": 0.2})
    await cli._resume(
        task_id,
        Configuration(product_dir=tmp_path),
        ports,
        from_document=True,
        dry_run=True,
        checkpointer=in_memory,
    )
    assert "from:      document" in capsys.readouterr().out


def _trivial_graph():
    """A real, tiny `StateGraph` -- no interrupt -- compiled fresh against whatever
    saver `_run`/`_resume` hand it: real enough to write real checkpoint rows through a
    real `composition.checkpointer`, unlike the `Delivers`/`in_memory` doubles the rest
    of this module uses (invariant: a migration claim is proven over real SQLite, never
    a double). An `escalate` node, named like the real graph's `ESCALATE`, is what lets
    `_resume`'s `aupdate_state(..., as_node=ESCALATE)` target an existing node."""
    from langgraph.graph import END, START, StateGraph

    from agentic.core.process.adapters.langgraph.graph import ESCALATE

    def escalate(state):
        return {}

    def node(state):
        return {"status": "ready_for_review"}

    g = StateGraph(dict)
    g.add_node(ESCALATE, escalate)
    g.add_node("n", node)
    g.add_edge(START, ESCALATE)
    g.add_edge(ESCALATE, "n")
    g.add_edge("n", END)
    return g


async def _seed_legacy_checkpoint(product_dir, thread_id: str) -> None:
    """A real checkpoint for `thread_id` in the product's legacy shared store, written
    with a real `AsyncSqliteSaver` (the same real seam `tests/test_composition.py`
    uses): what makes GH-1 resolve to the shared file on the first open, the state a
    migration test starts from."""
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    legacy = product_dir / ".agentic/state.sqlite"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(legacy)) as saver:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        metadata = {"source": "update", "step": 1, "parents": {}}
        await saver.aput(config, empty_checkpoint(), metadata, {})


def _checkpoint_count(path, thread_id: str) -> int:
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT count(*) FROM checkpoints WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.anyio
async def test_run_fresh_migrates_a_legacy_task_to_its_own_file(monkeypatch, tmp_path):
    """`_run(..., fresh=True)` over a task already in the legacy shared store: the
    reopen after `adelete_thread` is how it migrates off it (ADR-0003, addendum
    2026-10-04)."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", _trivial_graph())
    await _seed_legacy_checkpoint(tmp_path, "GH-1")
    ports = memory_ports()
    task = {"id": "GH-1", "title": "t", "description": "d"}
    cfg = Configuration(product_dir=tmp_path)

    await cli._run(task, cfg, ports, fresh=True, checkpointer=composition.checkpointer)

    legacy = tmp_path / ".agentic/state.sqlite"
    own = tmp_path / ".agentic/state/GH-1.sqlite"
    assert _checkpoint_count(legacy, "GH-1") == 0
    assert _checkpoint_count(own, "GH-1") >= 1
    async with composition.checkpointer(cfg, "GH-1") as saver:
        app = _trivial_graph().compile(checkpointer=saver)
        snapshot = await app.aget_state({"configurable": {"thread_id": "GH-1"}})
    assert snapshot.values.get("status") == "ready_for_review"


@pytest.mark.anyio
async def test_resume_from_document_migrates_a_legacy_task_to_its_own_file(monkeypatch, tmp_path):
    """`_resume(..., from_document=True)` over a task already in the legacy shared
    store: the second saver opened after `adelete_thread` is where `aupdate_state` and
    the resumed invocation both land (ADR-0003, addendum 2026-10-04)."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    monkeypatch.setattr(cli, "graph", _trivial_graph())
    await _seed_legacy_checkpoint(tmp_path, "GH-1")
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "type": "bug",
            "status": "waiting_on_human",
            "escalated": "Developer stopped by the task cap",
            "cost_usd": 1.0,
        }
    )
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    cfg = Configuration(product_dir=tmp_path)

    await cli._resume(
        "GH-1", cfg, ports, from_document=True, yes=True, checkpointer=composition.checkpointer
    )

    legacy = tmp_path / ".agentic/state.sqlite"
    own = tmp_path / ".agentic/state/GH-1.sqlite"
    assert _checkpoint_count(legacy, "GH-1") == 0
    assert _checkpoint_count(own, "GH-1") >= 1
    async with composition.checkpointer(cfg, "GH-1") as saver:
        app = _trivial_graph().compile(checkpointer=saver)
        snapshot = await app.aget_state({"configurable": {"thread_id": "GH-1"}})
    assert snapshot.values.get("status") == "ready_for_review"


# `cli._resume` over a checkpoint seeded with the pre-rename role names (2026-10-05,
# Tech Lead plan subtask `rename`): a fake checkpointer/graph double is the declared
# seam, since `aget_state`'s `values`/`interrupts` are what the new branching reads,
# never a real paused run reproduced end to end. The old name itself is never typed
# here: it comes from `legacy_names`'s own constants, the one place it lives.


class _LegacyFakeSaver:
    """What `_resume` calls on the checkpointer directly: `adelete_thread` alone."""

    def __init__(self):
        self.deleted: list[str] = []

    async def adelete_thread(self, task_id):
        self.deleted.append(task_id)


class LegacyFakeCheckpointer:
    """The same saver on both opens (`_resume` opens it twice), like `Saver` above."""

    def __init__(self):
        self.saver = _LegacyFakeSaver()

    @asynccontextmanager
    async def __call__(self, cfg, task_id):
        yield self.saver


class LegacyGraph:
    """A graph double whose `aget_state` hands back a checkpoint snapshot seeded with
    the old names, so `_resume`'s own `legacy_names.has_legacy_roles` branch is what
    routes it -- never a real interrupt reproduced through the real graph."""

    def __init__(self, values, interrupts=(), next_=("some_node",)):
        self.values = values
        self.interrupts = interrupts
        self.next = next_
        self.update_calls: list[tuple[dict, str | None]] = []

    def compile(self, checkpointer):
        return self

    async def aget_state(self, config):
        from types import SimpleNamespace

        return SimpleNamespace(values=self.values, next=self.next, interrupts=self.interrupts)

    async def aupdate_state(self, config, values, as_node=None):
        self.update_calls.append((values, as_node))

    async def ainvoke(self, command, config):
        return {**self.values, "status": "ready_for_review", "escalated": None}


def _legacy_checkpoint_values():
    """A checkpoint snapshot as a pre-rename run would have left it: `current_role`,
    `runs[].section/.slug` and `changes_requested` all still naming the old role."""
    return {
        "id": "GH-1",
        "title": "t",
        "status": "waiting_on_human",
        "current_role": legacy_names.OLD_NAME,
        "escalated": f"{legacy_names.OLD_NAME} stopped by the task cap",
        "cost_usd": 0.3,
        "runs": [
            {
                "role": legacy_names.OLD_NAME,
                "section": legacy_names.OLD_NAME,
                "slug": legacy_names.OLD_SLUG,
                "version": 1,
                "session_id": "s1",
                "model": "m",
                "cost_usd": 0.3,
                "turns": 1,
            }
        ],
        "changes_requested": {f"{legacy_names.OLD_NAME}→UI Designer": 1},
    }


def _new_named_document(**extra):
    return {
        "id": "GH-1",
        "title": "t",
        "description": "d",
        "current_role": legacy_names.NEW_NAME,
        "cost_usd": 0.1,
        "changes_requested": {f"{legacy_names.NEW_NAME}→UI Designer": 1},
        "runs": [
            {
                "role": legacy_names.NEW_NAME,
                "section": legacy_names.NEW_NAME,
                "slug": legacy_names.NEW_SLUG,
                "version": 1,
                "session_id": "s0",
                "model": "m",
                "cost_usd": 0.1,
                "turns": 1,
            }
        ],
        **extra,
    }


@pytest.mark.anyio
async def test_resume_takes_the_document_branch_for_a_paused_legacy_checkpoint(
    monkeypatch, tmp_path
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports = memory_ports()
    ports.documents.save(_new_named_document())
    graph_double = LegacyGraph(_legacy_checkpoint_values(), interrupts=("paused",))
    monkeypatch.setattr(cli, "graph", graph_double)
    checkpointer = LegacyFakeCheckpointer()

    await cli._resume(
        "GH-1", Configuration(product_dir=tmp_path), ports, yes=True, checkpointer=checkpointer
    )

    assert checkpointer.saver.deleted == ["GH-1"]
    assert len(graph_double.update_calls) == 1
    reseeded, as_node = graph_double.update_calls[0]
    assert as_node == cli.ESCALATE
    # Reseeded from the document alone -- not merged with the checkpoint's own old-named
    # run, which `operator.add` would have duplicated.
    assert reseeded["runs"] == _new_named_document()["runs"]
    assert reseeded["changes_requested"] == {f"{legacy_names.NEW_NAME}→UI Designer": 1}
    assert legacy_names.OLD_NAME not in str(reseeded)


@pytest.mark.anyio
async def test_resume_prefixes_the_rename_cause_when_the_forced_document_branch_disagrees_with_git(
    monkeypatch, tmp_path
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports = memory_ports()
    ports.documents.save(_new_named_document(branch="agentic/GH-1"))  # no such branch in git
    monkeypatch.setattr(
        cli, "graph", LegacyGraph(_legacy_checkpoint_values(), interrupts=("paused",))
    )

    with pytest.raises(cli.CannotStart) as info:
        await cli._resume(
            "GH-1",
            Configuration(product_dir=tmp_path),
            ports,
            yes=True,
            checkpointer=LegacyFakeCheckpointer(),
        )
    message = str(info.value)
    assert message.startswith(
        f"this task's checkpoint {legacy_names.RENAME_CAUSE}; it resumes from its "
        "document, which was checked against git:"
    )
    assert "agentic/GH-1" in message


@pytest.mark.anyio
async def test_resume_refuses_a_crashed_legacy_checkpoint(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports = memory_ports()
    ports.documents.save(_new_named_document())
    monkeypatch.setattr(
        cli,
        "graph",
        LegacyGraph(_legacy_checkpoint_values(), interrupts=()),  # crashed
    )

    with pytest.raises(cli.CannotStart) as info:
        await cli._resume(
            "GH-1",
            Configuration(product_dir=tmp_path),
            ports,
            yes=True,
            checkpointer=LegacyFakeCheckpointer(),
        )
    message = str(info.value)
    assert legacy_names.OLD_NAME in message and legacy_names.NEW_NAME in message
    assert "--from-document" in message
    assert "--issue 1" in message


# `agentic close --issue N` (Tech Lead plan, subtask `close-command`). Its declared
# seam is `agentic.cli.main` itself: argv in, exit code, stdout, samples and document
# bytes out -- not `cli._close` directly, unlike `_run`/`_resume` above.


def close_root(tmp_path: Path) -> Path:
    """A product directory good enough for `close`: no git repo needed, since `close`
    never calls `preflight` and touches no git command of its own -- just the
    `.agentic/` directory `main` itself checks for before building any ports."""
    root = tmp_path / "p"
    (root / ".agentic").mkdir(parents=True)
    return root


def close_task(**overrides):
    task = {
        "id": "GH-9",
        "title": "t",
        "description": "d",
        "type": "bug",
        "status": "ready_for_review",
        "pull_request": "https://github.com/o/r/pull/7",
        "cost_usd": 1.0,
        "prompts": {"developer": "abc"},
    }
    task.update(overrides)
    return task


def close_pull_request(
    *,
    state="MERGED",
    merged=True,
    human_commit=False,
    human_merge_parent_count=None,
    head_oid=None,
):
    commits = [
        PullRequestCommit(
            author_emails=("agentic-system@users.noreply.github.com",),
            message_headline="feat: x",
            additions=10,
            deletions=2,
            parent_count=1,
        )
    ]
    if human_commit:
        commits.append(
            PullRequestCommit(
                author_emails=("human@x.com",),
                message_headline="fix typo",
                additions=3,
                deletions=1,
                parent_count=1,
            )
        )
    if human_merge_parent_count is not None:
        commits.append(
            PullRequestCommit(
                author_emails=("human@x.com",),
                message_headline="Merge master into GH-9: resolve conflicts",
                additions=500,
                deletions=20,
                parent_count=human_merge_parent_count,
            )
        )
    return PullRequest(
        state=state,
        created_at="2026-09-01T00:00:00+00:00",
        merged_at="2026-09-01T01:00:00+00:00" if merged else None,
        commits=tuple(commits),
        merge_commit="c0ffee" if merged else None,
        head_oid=head_oid,
    )


class FakePullRequestTracker(MemoryTracker):
    """`MemoryTracker`'s own shape, with `pull_request` answered from a canned map (or
    raised) instead of `NotImplementedError` -- the one verb `close` needs that
    `MemoryTracker` does not otherwise carry."""

    def __init__(self, prs: dict[str, PullRequest | None] | None = None, raises=None):
        super().__init__()
        self.prs = prs or {}
        self.raises = raises
        self.pull_request_refs: list[str] = []

    def pull_request(self, reference: str) -> PullRequest | None:
        pull_request_number(reference)  # the real adapter's parse, before anything else
        self.pull_request_refs.append(reference)
        if self.raises is not None:
            raise self.raises
        return self.prs.get(reference)


def close_ports(root: Path, tracker: MemoryTracker, product: str | None = "p"):
    """A real `FilesystemTaskDocuments` over `root` (so a refusal's claim of a
    byte-identical document file is provable on disk), everything else an in-memory
    fake `close` never touches."""
    return replace(
        memory_ports(),
        documents=FilesystemTaskDocuments(root),
        tracker=tracker,
        stack=MemoryStack(product=product),
    )


def test_close_dispatch_builds_the_close_work(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    seen = capture_run(monkeypatch)
    argv = ["agentic", "close", "--issue", "9", "--product-dir", str(root)]
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    assert seen["fn"] is cli._close
    assert seen["task"] == "GH-9"


def test_close_does_not_run_preflight(monkeypatch, tmp_path, capsys):
    """Unlike `run`/`resume`, a product that declares no stack does not stop `close`:
    it runs no role and spends nothing, so it never calls `preflight`."""
    root = product(tmp_path, "p", stack=None)
    seen = capture_run(monkeypatch)
    argv = ["agentic", "close", "--issue", "9", "--product-dir", str(root)]
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    assert seen["fn"] is cli._close
    assert capsys.readouterr().err == ""


def test_close_detach_is_a_parse_error(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "close", "--issue", "9", "--detach"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_close_dry_run_is_a_parse_error(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "close", "--issue", "9", "--dry-run"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def _closed_run(monkeypatch, root: Path, ports, *, issue: int = 9):
    """Runs `agentic close --issue <issue>` for real, through `main`, with `ports`
    substituted for the ones `default_ports` would have built -- the lever every test
    below uses to keep `gh` and git out of it while still exercising the CLI seam."""
    monkeypatch.setattr(cli, "default_ports", lambda cfg: ports)
    argv = ["agentic", "close", "--issue", str(issue), "--product-dir", str(root)]
    monkeypatch.setattr("sys.argv", argv)


class _PortsAsked(BaseException):
    """Raised by the fake `default_ports` so `main` stops right after the ports are asked."""


@pytest.mark.parametrize(
    ("argv", "wanted"),
    [
        (["run", "--issue", "9"], "run"),
        (["resume", "--issue", "9"], "run"),
        (["merge", "--issue", "9"], "exempt"),
        (["land", "--issue", "9"], "exempt"),
        (["close", "--issue", "9"], None),
        (["poll"], None),
    ],
)
def test_the_cli_asks_for_a_gh_retry_policy_only_from_the_verbs_that_own_a_task(
    monkeypatch, tmp_path, argv, wanted
):
    """GH-389: run/resume wait up to the run cap, merge/land the exempt one, and every other
    verb (the poller, close) builds its ports with no policy, so ADR-0050's fast failure
    holds there. Swapping or dropping a verb in `main` must break this."""
    (tmp_path / ".agentic").mkdir()
    asked: list[object] = []

    def fake_default_ports(cfg, stop_flag=None, *, gh_retry=None):
        asked.append(gh_retry)
        raise _PortsAsked

    monkeypatch.setattr(cli, "default_ports", fake_default_ports)
    monkeypatch.setattr("sys.argv", ["agentic", *argv, "--product-dir", str(tmp_path)])
    with pytest.raises(_PortsAsked):
        cli.main()
    assert asked == [wanted]


def test_close_refuses_with_no_document(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "GH-9 has no document" in capsys.readouterr().err
    assert not ports.documents.path_for("GH-9").exists()


def test_close_refuses_with_no_pull_request_value(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(close_task(pull_request=None))
    before = ports.documents.path_for("GH-9").read_bytes()
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "GH-9 has no pull request" in capsys.readouterr().err
    assert ports.documents.path_for("GH-9").read_bytes() == before


def test_close_refuses_when_already_closed(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(close_task())
    ports.documents.record_closure(
        "GH-9",
        {
            "closed_at": "2026-09-20T00:00:00.000Z",
            "delivery_outcome": "merged",
            "merge_commit": "c0ffee",
            "human_commits": "0",
            "human_lines_changed": "0",
        },
    )
    before = ports.documents.path_for("GH-9").read_bytes()
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "already closed on 2026-09-20T00:00:00.000Z" in capsys.readouterr().err
    assert ports.documents.path_for("GH-9").read_bytes() == before
    assert ports.tracker.pull_request_refs == []  # refused before `gh` was ever asked


def test_close_refuses_when_the_pull_request_is_still_open(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request(state="OPEN", merged=False)})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    before = ports.documents.path_for("GH-9").read_bytes()
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    error = capsys.readouterr().err
    assert "still open" in error and "nothing to close" in error
    assert ports.documents.path_for("GH-9").read_bytes() == before


def test_close_closes_with_no_metrics_endpoint_configured(monkeypatch, tmp_path, capsys):
    """The endpoint gate (`telemetry.metrics_configured`) is gone: no sample backs the
    verdict any more, so a predicate asking whether one could be sent has no subject
    left to guard (Tech Lead plan, subtask `close-verdict`). A machine with no
    `OTEL_EXPORTER_OTLP_ENDPOINT` now closes like any other -- this is the regression
    evidence for the guard's removal, the counterpart of the accepting case this test
    used to be the refusing one of."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request()})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    telemetry.configure()  # no OTEL_EXPORTER_OTLP_ENDPOINT: `configure()` builds nothing
    try:
        _closed_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    assert "agentic: document written, not committed" in capsys.readouterr().out
    assert ports.documents.load("GH-9")["closed_at"]


def test_close_refuses_when_the_product_has_no_name(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker(), product=None)
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "expected `product`" in capsys.readouterr().err


def test_close_happy_path_writes_the_five_keys_with_no_sample_emitted(
    monkeypatch, tmp_path, capsys
):
    """`close` writes its five frontmatter keys straight off the pull request, with no
    `telemetry.record_delivery` call in between (Tech Lead plan, subtask
    `close-verdict`, item 2): `points.points == {}` after a successful close is the
    proof the closure path never touches telemetry at all any more."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request(human_commit=True)})
    ports = close_ports(root, tracker)
    FilesystemTaskDocuments(root, system_version=lambda: "v123").save(
        close_task(pull_request=reference)
    )
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()

    out = capsys.readouterr().out
    assert "outcome:             merged_with_changes" in out
    assert "human_commits:       1" in out
    assert "human_lines_changed: 4" in out
    assert "doc:" in out and "not committed" in out

    doc = ports.documents.load("GH-9")
    assert doc["delivery_outcome"] == "merged_with_changes"
    assert doc["human_commits"] == "1"
    assert doc["human_lines_changed"] == "4"
    assert doc["closed_at"]
    assert doc["status"] == "ready_for_review"  # no new `Status` value is added


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"auto_merge_head": "abc123"}, "auto_merged"),
        ({"auto_merge_head": ""}, "merged"),
        ({"auto_merge_head": "older-head"}, "merged"),
        ({}, "merged"),
    ],
)
def test_close_classifies_auto_merged_by_the_intent_head(monkeypatch, tmp_path, extra, expected):
    """The intent the merge verb wrote (`auto_merge_head`) names the head it merged ->
    `auto_merged`; an empty, stale or missing one leaves the human's verdict as before."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request(head_oid="abc123")})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    if extra:  # `render` only writes the state's own keys; the verb edits the line in
        ports.documents.record_closure("GH-9", extra)
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    assert ports.documents.load("GH-9")["delivery_outcome"] == expected


def test_close_treats_a_human_merge_of_master_as_no_human_change(monkeypatch, tmp_path, capsys):
    """GH-136: the human's only commit on the branch is a two-parent merge of master
    with a hand-written title (resolving a conflict, the apostrophe #16/#21 case) --
    `parent_count` alone excludes it, so the close is `merged` with 0 lines, never
    `merged_with_changes` carrying the whole diff of master."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request(human_merge_parent_count=2)})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    doc = ports.documents.load("GH-9")
    assert doc["delivery_outcome"] == "merged"
    assert doc["human_commits"] == "0"
    assert doc["human_lines_changed"] == "0"


def test_close_persists_time_to_merge_seconds_when_the_pull_request_merged(monkeypatch, tmp_path):
    """The one new closure key (Tech Lead plan, subtask `close-all`): seconds from
    `created_at` to `merged_at`, written as a quoted scalar string -- `record_closure`
    round-trips every closure value through `document.parse`, which hands every
    frontmatter scalar back as a `str`."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request()})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    doc = ports.documents.load("GH-9")
    assert doc["time_to_merge_seconds"] == "3600"  # 2026-09-01T00:00 -> T01:00


def test_close_omits_time_to_merge_seconds_when_the_pull_request_never_merged(
    monkeypatch, tmp_path
):
    """A pull request `delivery_outcome` reads as `closed` (never merged) leaves the
    key entirely absent -- never written empty or as a zero (Tech Lead plan, subtask
    `close-all`)."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request(state="CLOSED", merged=False)})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    doc = ports.documents.load("GH-9")
    assert doc["delivery_outcome"] == "closed"
    assert "time_to_merge_seconds" not in doc


def _closed_with(monkeypatch, tmp_path, tracker_setup, pull_request=None, **task):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: pull_request or close_pull_request()})
    tracker_setup(tracker)
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference, **task))
    telemetry.configure()
    try:
        _closed_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    return ports.documents.load("GH-9"), tracker


def test_close_records_the_files_the_pull_request_changed(monkeypatch, tmp_path):
    """ADR-0054: `actual_files` comes from the tracker's `changed_files` through
    `rules.delivered_files`, for a merged and for a closed-unmerged pull request."""

    def three(tracker):
        tracker.changed_files_count = 3

    doc, tracker = _closed_with(monkeypatch, tmp_path, three)
    assert doc["actual_files"] == 3
    assert tracker.changed_files_asked == ["https://github.com/o/r/pull/7"]


def test_close_records_the_files_of_a_pull_request_closed_without_merging(monkeypatch, tmp_path):
    def three(tracker):
        tracker.changed_files_count = 3

    doc, _ = _closed_with(
        monkeypatch, tmp_path, three, close_pull_request(state="CLOSED", merged=False)
    )
    assert doc["delivery_outcome"] == "closed" and doc["actual_files"] == 3


def test_close_still_closes_when_the_file_count_is_unreadable(monkeypatch, tmp_path):
    def broken(tracker):
        tracker.changed_files_error = "gh printed garbage"

    doc, _ = _closed_with(monkeypatch, tmp_path, broken)
    assert doc["closed_at"] and "actual_files" not in doc


# `agentic close --all` (Tech Lead plan, subtask `close-all`): the sweep over every
# task document with a `pull_request` and no `closed_at`, through the CLI seam like
# every close test above -- argv in, exit code, stdout and document bytes out.


def _all_run(monkeypatch, root: Path, ports) -> None:
    monkeypatch.setattr(cli, "default_ports", lambda cfg: ports)
    monkeypatch.setattr("sys.argv", ["agentic", "close", "--all", "--product-dir", str(root)])


def test_close_all_is_a_parse_error_with_neither_issue_nor_all(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "close"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_close_all_is_a_parse_error_together_with_issue(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "close", "--issue", "9", "--all"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_close_all_sweeps_unconditionally_with_no_metrics_endpoint_configured(
    monkeypatch, tmp_path, capsys
):
    """The capability predicate (`telemetry.metrics_configured`) is gone: no sample
    backs the verdict any more, so the sweep no longer refuses before touching a
    document -- it runs unconditionally (Tech Lead plan, subtask `close-verdict`,
    item 1). The endpoint-gate test's accepting counterpart: a machine with no
    `OTEL_EXPORTER_OTLP_ENDPOINT` now closes every selected document."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request()})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    telemetry.configure()  # no OTEL_EXPORTER_OTLP_ENDPOINT: nothing configured
    try:
        _all_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    out = capsys.readouterr().out
    assert "agentic: closed 1:" in out
    assert ports.documents.load("GH-9")["closed_at"]
    assert tracker.pull_request_refs == [reference]


def test_close_all_does_not_raise_with_the_endpoint_declared_and_telemetry_untouched(
    monkeypatch, tmp_path, capsys
):
    """GH-119: the accepting case for the declared-but-unconfigured input -- the
    endpoint is set in the shell (`main`'s `load_dotenv()` already ran) and nothing in
    this process has called `telemetry.configure()` yet, exactly the shell the bug
    report describes. `monkeypatch` only: no `try`/`finally` and no call to
    `telemetry.configure()` anywhere in this test, because `configure()` with the
    variable still set is the branch that builds a real `BatchSpanProcessor` over OTLP
    -- an exporter started inside `uv run pytest -m "not llm"` would be invisible in a
    green suite (`tests/conftest.py`'s own `isolated_env` deletes this variable autouse
    for exactly this reason). `close` no longer gates on any endpoint at all (ADR-0037:
    no metrics sample left to back a verdict on), so this only proves the sweep still
    does not raise with the variable declared and the tracer left unconfigured."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    tracker = FakePullRequestTracker()
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=None))
    monkeypatch.setattr(telemetry, "_configured", False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    _all_run(monkeypatch, root, ports)
    cli.main()  # would raise CannotStart / SystemExit(2) before the fix
    out = capsys.readouterr().out
    assert "GH-9 (issue 9): no pull request" in out
    assert tracker.pull_request_refs == []
    assert telemetry._configured is False


def test_close_all_closes_every_selected_document_in_sorted_id_order(monkeypatch, tmp_path, capsys):
    """Two documents with a pull request and no `closed_at`, closed in `ids()`'s own
    sorted order -- proof `ids()` is sorted and the sweep is idempotent-per-task
    without an explicit ordering argument (Tech Lead plan, subtask `close-all`)."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ref1, ref9 = "https://github.com/o/r/pull/1", "https://github.com/o/r/pull/9"
    tracker = FakePullRequestTracker({ref1: close_pull_request(), ref9: close_pull_request()})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(id="GH-9", pull_request=ref9))
    ports.documents.save(close_task(id="GH-1", pull_request=ref1))
    telemetry.configure()
    try:
        _all_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    out = capsys.readouterr().out
    assert out.index("GH-1") < out.index("GH-9")  # closed in sorted `ids()` order
    assert ports.documents.load("GH-1")["closed_at"]
    assert ports.documents.load("GH-9")["closed_at"]
    assert "agentic: closed 2:" in out
    assert "agentic: remaining 0:" in out


def test_close_all_prints_a_line_for_a_document_that_stays_open_and_leaves_it_untouched(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request(state="OPEN", merged=False)})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    before = ports.documents.path_for("GH-9").read_bytes()
    telemetry.configure()
    try:
        _all_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    out = capsys.readouterr().out
    assert "GH-9 (issue 9): still open" in out
    assert "agentic: closed 0:" in out
    assert "agentic: remaining 1:" in out
    assert ports.documents.path_for("GH-9").read_bytes() == before


def test_close_all_is_idempotent_over_an_already_closed_document(monkeypatch, tmp_path, capsys):
    """A document `agentic close --issue N` (or a previous sweep) already closed is
    never selected: rerunning `--all` over it finds nothing left to do."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(close_task())
    ports.documents.record_closure(
        "GH-9",
        {
            "closed_at": "2026-09-20T00:00:00.000Z",
            "delivery_outcome": "merged",
            "merge_commit": "c0ffee",
            "human_commits": "0",
            "human_lines_changed": "0",
        },
    )
    telemetry.configure()
    try:
        _all_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    out = capsys.readouterr().out
    assert "GH-9 (issue 9): already closed" in out  # one of the plan's five line kinds
    assert "agentic: closed 0:" in out
    assert "agentic: remaining 0:" in out


def test_close_all_prints_a_line_for_a_document_with_no_pull_request_and_touches_nothing(
    monkeypatch, tmp_path, capsys
):
    """A task that never opened a pull request is nothing for the sweep to do -- read
    off the document alone, never `gh` (`_refuse_open_pull_request`'s own reading) --
    but it still gets its own line, one of the plan's five kinds, and stays out of both
    summary counts since it was never selected."""
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(close_task(pull_request=None))
    telemetry.configure()
    try:
        _all_run(monkeypatch, root, ports)
        cli.main()
    finally:
        telemetry.configure()
    out = capsys.readouterr().out
    assert "GH-9 (issue 9): no pull request" in out
    assert "agentic: closed 0:" in out
    assert "agentic: remaining 0:" in out


class _FlakyPullRequestTracker(FakePullRequestTracker):
    """Answers `prs` normally until its `fails_at`-th call, then raises like a `gh pr
    view` that just broke -- the sweep's stop-on-first-failure trigger after Tech Lead
    plan subtask `close-verdict`: a `gh` failure (`_close_document`'s own
    `"tracker_error"` status), not a false flush."""

    def __init__(self, prs, fails_at: int):
        super().__init__(prs)
        self.fails_at = fails_at

    def pull_request(self, reference: str) -> PullRequest | None:
        pull_request_number(reference)  # the real adapter's parse, before anything else
        self.pull_request_refs.append(reference)
        if len(self.pull_request_refs) == self.fails_at:
            raise ValueError("gh: pull request could not be read")
        return self.prs.get(reference)


def test_a_bare_issue_number_fails_through_fake_pull_request_tracker_before_anything_is_recorded():
    tracker = FakePullRequestTracker({"9": close_pull_request()})
    for reference in ("9", "GH-9"):
        with pytest.raises(ValueError, match="not a pull request URL"):
            tracker.pull_request(reference)
    assert tracker.pull_request_refs == []


def test_a_bare_issue_number_fails_through_the_flaky_tracker_with_the_parse_error_not_its_own():
    tracker = _FlakyPullRequestTracker({"9": close_pull_request()}, fails_at=1)
    for reference in ("9", "GH-9"):
        with pytest.raises(ValueError, match="not a pull request URL"):
            tracker.pull_request(reference)
    assert tracker.pull_request_refs == []


def test_close_all_stops_the_sweep_on_the_first_gh_failure_printing_both_lists(
    monkeypatch, tmp_path, capsys
):
    """A `gh` failure on GH-9 must stop the sweep there and print what closed before it
    and what remains (Tech Lead plan, subtask `close-verdict`, item 3: the stop trigger
    moves from a false flush to a `gh` failure) -- exits zero, since every document was
    still readable, so this is not the "no document could be read at all" case. The
    rejecting input for the changed stop-on-first-failure guard."""
    root = close_root(tmp_path)
    ref1, ref9 = "https://github.com/o/r/pull/1", "https://github.com/o/r/pull/9"
    tracker = _FlakyPullRequestTracker(
        {ref1: close_pull_request(), ref9: close_pull_request()}, fails_at=2
    )
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(id="GH-1", pull_request=ref1))
    ports.documents.save(close_task(id="GH-9", pull_request=ref9))
    _all_run(monkeypatch, root, ports)
    cli.main()  # exits zero: every document was readable
    out = capsys.readouterr().out
    assert ports.documents.load("GH-1")["closed_at"]  # closed before GH-9's `gh` call failed
    assert not ports.documents.load("GH-9").get("closed_at")
    assert "agentic: closed 1: GH-1 (issue 1)" in out
    assert "agentic: remaining 1: GH-9 (issue 9)" in out
    assert tracker.pull_request_refs == [ref1, ref9]


def test_close_all_reports_a_task_after_the_stopped_one_as_not_attempted_not_still_open(
    monkeypatch, tmp_path, capsys
):
    """The task selected *after* the one whose `gh` call failed is never asked `gh`
    either (Tech Lead plan, subtask `close-verdict`): it must not print `still open`, a
    claim about a pull request state this sweep stopped reading -- only that it was not
    attempted, and the printed line no longer names a flush."""
    root = close_root(tmp_path)
    ref1 = "https://github.com/o/r/pull/1"
    ref5 = "https://github.com/o/r/pull/5"
    ref9 = "https://github.com/o/r/pull/9"
    tracker = _FlakyPullRequestTracker(
        {ref1: close_pull_request(), ref5: close_pull_request(), ref9: close_pull_request()},
        fails_at=2,
    )
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(id="GH-1", pull_request=ref1))
    ports.documents.save(close_task(id="GH-5", pull_request=ref5))
    ports.documents.save(close_task(id="GH-9", pull_request=ref9))
    _all_run(monkeypatch, root, ports)
    cli.main()
    out = capsys.readouterr().out
    assert "GH-9 (issue 9): not attempted (sweep stopped on an earlier gh failure)" in out
    assert "GH-9 (issue 9): still open" not in out
    assert ref9 not in tracker.pull_request_refs  # never asked `gh` about GH-9 at all
    assert not ports.documents.load("GH-9").get("closed_at")
    assert "agentic: closed 1: GH-1 (issue 1)" in out
    assert "agentic: remaining 2: GH-5 (issue 5), GH-9 (issue 9)" in out


# `agentic eval run` / `agentic eval score` (Tech Lead plan, subtask `eval-cli`):
# argparse refusals at `agentic.cli.main`, everything else at `_eval_run`/`_eval_score`
# directly, injecting a fake `EvalLauncher` so no test here spawns a real `agentic run`
# or patches `subprocess` (Tech Lead plan: "the cheap test to write is one that spends
# $200").

EVAL_SET_YAML = "issues:\n  - 1\n  - 2\nsmoke: 1\n"


def eval_root(tmp_path: Path, eval_set: str | None = EVAL_SET_YAML) -> Path:
    root = tmp_path / "p"
    (root / ".agentic").mkdir(parents=True)
    if eval_set is not None:
        (root / ".agentic" / "eval-set.yaml").write_text(eval_set)
    return root


class FakeKeyedLock:
    """A `RunLock` double keyed by `task_id`, for `eval run`'s mix of its own
    fixed-string lock (`"eval"`) and each set task's own: `held` maps `task_id` to
    whatever `LockRecord` a live process would have written."""

    def __init__(self, held: dict[str, LockRecord] | None = None):
        self.held = dict(held or {})
        self.acquired: list[tuple[str, str]] = []
        self.released: list[str] = []
        self.kept_crashed: list[tuple[str, str]] = []

    def read(self, task_id):
        return self.held.get(task_id)

    def acquire(self, task_id, command):
        self.acquired.append((task_id, command))
        message = self.refusal(task_id)
        if message is not None:
            raise LockHeld(self.held[task_id], message)
        self.held[task_id] = LockRecord(os.getpid(), "h", "2026-09-21T00:00:00Z", command)
        return None

    def refusal(self, task_id):
        record = self.held.get(task_id)
        if record is None:
            return None
        return f"{task_id} is already running: pid {record.pid}"

    def keep_at_shutdown(self, task_id):
        pass

    def keep_crashed(self, task_id, system_version):
        self.kept_crashed.append((task_id, system_version))

    def release(self, task_id):
        self.released.append(task_id)
        self.held.pop(task_id, None)


class FakeLaunch:
    """An `EvalLauncher` double: no real process, just what argv and environment
    overlay each call got, in order."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv, env_overlay):
        self.calls.append((argv, dict(env_overlay)))


def test_eval_run_requires_budget(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "eval", "run", "--yes"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_eval_score_requires_out(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "eval", "score"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_eval_run_detach_and_dry_run_are_exclusive(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["agentic", "eval", "run", "--budget", "200", "--yes", "--detach", "--dry-run"],
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_eval_run_detach_requires_yes(monkeypatch):
    monkeypatch.setattr("sys.argv", ["agentic", "eval", "run", "--budget", "200", "--detach"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def capture_eval(monkeypatch):
    seen = {}

    def fake_run(func, *args):
        assert func is cli._signal_guarded
        (work,) = args
        seen.update(fn=work.func, args=work.args, kwargs=work.keywords)

    monkeypatch.setattr(cli.anyio, "run", fake_run)
    return seen


def test_eval_run_dispatch_builds_the_eval_run_work(monkeypatch, tmp_path):
    root = eval_root(tmp_path)
    seen = capture_eval(monkeypatch)
    argv = ["agentic", "eval", "run", "--budget", "200", "--yes", "--product-dir", str(root)]
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    assert seen["fn"] is cli._eval_run
    assert seen["kwargs"]["detach"] is False


def test_eval_score_dispatch_builds_the_eval_score_work(monkeypatch, tmp_path):
    root = eval_root(tmp_path)
    seen = capture_eval(monkeypatch)
    out = str(tmp_path / "out.jsonl")
    argv = ["agentic", "eval", "score", "--out", out, "--product-dir", str(root)]
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    assert seen["fn"] is cli._eval_score
    cfg, _ports, set_path, out_path = seen["args"]
    assert cfg.product_dir == root
    assert set_path is None
    assert out_path == out


@pytest.mark.anyio
async def test_eval_run_refuses_when_the_declaration_file_is_missing(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set=None)
    with pytest.raises(cli.CannotStart, match="eval-set.yaml"):
        await cli._eval_run(
            Configuration(product_dir=root), memory_ports(), None, 200.0, True, False
        )


@pytest.mark.anyio
async def test_eval_run_dry_run_prefers_the_set_override_over_the_products_own(tmp_path, capsys):
    """`--set` overrides the product's own `.agentic/eval-set.yaml` for a spike, and
    the printed line says which of the two it read (Tech Lead plan)."""
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set="issues:\n  - 1\nsmoke: 1\n")  # never read
    override = tmp_path / "spike.yaml"
    override.write_text("issues:\n  - 9\nsmoke: 9\n")
    await cli._eval_run(
        Configuration(product_dir=root), memory_ports(), str(override), 200.0, False, True
    )
    out = capsys.readouterr().out
    assert str(override) in out
    assert "GH-9 (issue 9)" in out
    assert "GH-1" not in out


@pytest.mark.anyio
async def test_eval_score_uses_the_set_override_and_reports_it_as_the_source(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set="issues:\n  - 1\nsmoke: 1\n")  # never read
    override = tmp_path / "spike.yaml"
    override.write_text("issues:\n  - 9\nsmoke: 9\n")
    ports = memory_ports()
    ports.documents.save({"id": "GH-9", "status": "open", "prompts": {"developer": "abc"}})
    out = tmp_path / "out.jsonl"
    await cli._eval_score(Configuration(product_dir=root), ports, str(override), str(out))
    (line,) = out.read_text().splitlines()
    record = json.loads(line)
    assert record["issue"] == 9
    assert record["declaration_source"] == str(override)
    assert record["baseline_id"]


@pytest.mark.anyio
async def test_eval_run_refuses_when_the_declaration_names_an_unknown_key(tmp_path):
    """The pure verdict `evaluation.eval_set_refusal` on an input it must reject,
    exercised through the CLI seam: the offending key names itself in the message."""
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set="issues:\n  - 1\nsmoke: 1\nextra: nope\n")
    with pytest.raises(cli.CannotStart, match="extra"):
        await cli._eval_run(
            Configuration(product_dir=root), memory_ports(), None, 200.0, True, False
        )


@pytest.mark.anyio
async def test_eval_run_refuses_when_the_product_has_no_name(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    ports = memory_ports(stack=MemoryStack(product=None))
    with pytest.raises(cli.CannotStart, match="expected `product`"):
        await cli._eval_run(Configuration(product_dir=root), ports, None, 200.0, True, False)


@pytest.mark.anyio
async def test_eval_run_refuses_when_a_set_tasks_pull_request_has_no_recorded_closure(tmp_path):
    """Read off the document alone, never `gh`: `memory_ports()`'s own `MemoryTracker`
    carries no `pull_request` method at all, so a call to it would raise `AttributeError`
    -- a different exception than `CannotStart` -- and fail this test outright, proving
    the refusal never reaches for `gh` (the plan's invariant: neither `eval` phase calls
    it)."""
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "pull_request": reference})
    launch = FakeLaunch()
    with pytest.raises(cli.CannotStart, match="no recorded closure"):
        await cli._eval_run(
            Configuration(product_dir=root), ports, None, 200.0, True, False, launch=launch
        )
    assert launch.calls == []


@pytest.mark.anyio
async def test_eval_run_does_not_refuse_a_set_tasks_already_closed_pull_request(tmp_path):
    """The accept side of the same rule: `closed_at` recorded is `agentic close`'s own
    word that the pull request is no longer open, so `--fresh` archiving its branch is
    no longer a hazard and the task launches."""
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "pull_request": "https://github.com/o/r/pull/7",
            "closed_at": "2026-09-20T00:00:00.000Z",
        }
    )

    def launch(argv, env_overlay):
        issue = argv[-1]
        ports.documents.save({"id": f"GH-{issue}", "status": "ready_for_review", "cost_usd": 40.0})

    await cli._eval_run(
        Configuration(product_dir=root), ports, None, 200.0, True, False, launch=launch
    )


@pytest.mark.anyio
async def test_eval_run_refuses_when_a_set_tasks_own_run_lock_is_held(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    lock = FakeKeyedLock(held={"GH-2": lock_record()})
    launch = FakeLaunch()
    with pytest.raises(cli.CannotStart, match="GH-2 is already running"):
        await cli._eval_run(
            Configuration(product_dir=root),
            memory_ports(),
            None,
            200.0,
            True,
            False,
            run_lock=lock,
            launch=launch,
        )
    assert launch.calls == []


@pytest.mark.anyio
async def test_eval_run_refuses_when_the_eval_lock_itself_is_held(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    lock = FakeKeyedLock(held={"eval": lock_record()})
    launch = FakeLaunch()
    with pytest.raises(cli.CannotStart, match="eval is already running"):
        await cli._eval_run(
            Configuration(product_dir=root),
            memory_ports(),
            None,
            200.0,
            True,
            False,
            run_lock=lock,
            launch=launch,
        )
    assert launch.calls == []
    assert lock.released == []  # never acquired: nothing to release


@pytest.mark.anyio
async def test_eval_run_requires_yes_before_spending_and_prints_the_estimate_first(
    tmp_path, capsys
):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    launch = FakeLaunch()
    with pytest.raises(cli.CannotStart, match=r"estimated spend \$90.00"):
        await cli._eval_run(
            Configuration(product_dir=root),
            memory_ports(),
            None,
            200.0,
            False,
            False,
            launch=launch,
        )
    assert "estimated $90.00" in capsys.readouterr().out
    assert launch.calls == []


@pytest.mark.anyio
async def test_eval_run_dry_run_reports_and_spends_nothing(tmp_path, capsys):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "status": "ready_for_review"})
    launch = FakeLaunch()
    lock = FakeKeyedLock()
    await cli._eval_run(
        Configuration(product_dir=root),
        ports,
        None,
        200.0,
        False,
        True,
        run_lock=lock,
        launch=launch,
    )
    out = capsys.readouterr().out
    assert "GH-1 (issue 1): ready_for_review" in out
    assert "GH-2 (issue 2): no document yet" in out
    assert launch.calls == []
    assert lock.acquired == []


@pytest.mark.anyio
async def test_eval_run_launches_each_task_in_order_waited(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    ports = memory_ports()

    def launch(argv, env_overlay):
        launch.calls.append((argv, env_overlay))
        issue = argv[-1]
        ports.documents.save({"id": f"GH-{issue}", "status": "ready_for_review", "cost_usd": 40.0})

    launch.calls = []
    await cli._eval_run(
        Configuration(product_dir=root), ports, None, 200.0, True, False, launch=launch
    )
    assert [argv for argv, _ in launch.calls] == [
        [cli.sys.argv[0], "run", "--fresh", "--issue", "1"],
        [cli.sys.argv[0], "run", "--fresh", "--issue", "2"],
    ]


@pytest.mark.anyio
async def test_eval_run_hands_each_child_the_products_dir_and_never_a_log_file_key(tmp_path):
    """`_eval_run` builds its overlay from `cfg.product_dir` alone (Tech Lead plan): the
    `AGENTIC_LOG_FILE` drop is the default `_run_eval_child`'s own job on `os.environ`
    (not exercised here, since no test in this diff patches `subprocess`), but the
    overlay the loop hands to *any* launcher -- fake or real -- must never itself carry
    that key, or the default would have nothing to drop it from."""
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set="issues:\n  - 1\nsmoke: 1\n")
    ports = memory_ports()
    launch = FakeLaunch()
    await cli._eval_run(
        Configuration(product_dir=root), ports, None, 200.0, True, False, launch=launch
    )
    (call,) = launch.calls
    _argv, overlay = call
    assert overlay == {cli.ENV_PREFIX + "PRODUCT_DIR": str(root)}
    assert cli.ENV_PREFIX + "LOG_FILE" not in overlay


@pytest.mark.anyio
async def test_eval_run_charges_the_full_estimate_when_a_childs_document_never_moved(
    tmp_path, capsys
):
    """A child that dies before writing anything cannot loop for free: the budget
    check that follows sees the full per-task estimate, not zero."""
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set="issues:\n  - 1\n  - 2\nsmoke: 1\n")
    ports = memory_ports()
    launch = FakeLaunch()  # never writes a document
    await cli._eval_run(
        Configuration(product_dir=root), ports, None, 45.0, True, False, launch=launch
    )
    # Budget of one task's estimate: the second task never launches.
    assert len(launch.calls) == 1
    assert f"${evaluation.PER_TASK_ESTIMATE_USD:.2f} spent so far" in capsys.readouterr().out


@pytest.mark.anyio
async def test_eval_score_refuses_when_the_destination_already_exists(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set=None)  # even an unreadable declaration never matters
    out = tmp_path / "out.jsonl"
    out.write_text("{}\n")
    with pytest.raises(cli.CannotStart, match="already exists"):
        await cli._eval_score(Configuration(product_dir=root), memory_ports(), None, str(out))
    assert out.read_text() == "{}\n"  # untouched: never truncated, never appended to


@pytest.mark.anyio
async def test_eval_score_refuses_on_an_invalid_declaration(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set=None)
    out = tmp_path / "out.jsonl"
    with pytest.raises(cli.CannotStart, match="eval-set.yaml"):
        await cli._eval_score(Configuration(product_dir=root), memory_ports(), None, str(out))
    assert not out.exists()


@pytest.mark.anyio
async def test_eval_score_writes_one_line_per_task_and_prints_pending_close_commands(
    tmp_path, capsys
):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "status": "ready_for_review",
            "type": "feature",
            "closed_at": "2026-09-20T00:00:00.000Z",
            "delivery_outcome": "merged",
            "cost_usd": 40.0,
            "prompts": {"developer": "abc"},
        }
    )
    ports.documents.save({"id": "GH-2", "status": "open", "prompts": {"developer": "abc"}})
    out = tmp_path / "out.jsonl"
    await cli._eval_score(Configuration(product_dir=root), ports, None, str(out))
    lines = out.read_text().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    by_issue = {r["issue"]: r for r in records}
    assert by_issue[1]["score"] is True
    assert by_issue[2]["score"] is None
    assert by_issue[2]["score_pending_reason"]
    out_text = capsys.readouterr().out
    assert "agentic close --issue 2" in out_text
    assert "agentic close --issue 1" not in out_text


@pytest.mark.anyio
async def test_eval_score_reports_fingerprint_divergence_as_a_fact_not_an_error(tmp_path, capsys):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "status": "open", "prompts": {"developer": "a"}})
    ports.documents.save({"id": "GH-2", "status": "open", "prompts": {"developer": "b"}})
    out = tmp_path / "out.jsonl"
    await cli._eval_score(Configuration(product_dir=root), ports, None, str(out))  # never raises
    assert "diverges" in capsys.readouterr().out


# `agentic stack review` (ADR-0034): the Tech Lead in proactive mode, with no task
# behind it. Seam: `agentic/cli.py`, `agentic stack review` through `main()`, the same
# seam the rest of this file already uses.


def _greenfield(tmp_path, name, *, git=True, brief="# Brief\n\nGym members book classes.\n"):
    """A product with only `BRIEF.md`: no `.agentic/` at all, so every other command
    refuses it outright (`test_preflight_rejects_a_dir_without_agentic`) and only
    `agentic stack review` may run over it (ADR-0034, item 4)."""
    root = tmp_path / name
    root.mkdir(parents=True)
    if brief is not None:
        (root / "BRIEF.md").write_text(brief)
    if git:
        gitcmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*gitcmd, "init", "-q", "-b", "master", str(root)], check=True)
        subprocess.run([*gitcmd, "add", "-A"], cwd=root, check=True)
        subprocess.run(
            [*gitcmd, "commit", "-q", "-m", "brief", "--allow-empty"], cwd=root, check=True
        )
    return root


def test_stack_review_refuses_a_directory_that_is_not_a_git_repository(monkeypatch, tmp_path):
    root = _greenfield(tmp_path, "g", git=False)
    monkeypatch.setattr("sys.argv", ["agentic", "stack", "review", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


def test_stack_review_names_both_halves_of_the_repository_refusal(monkeypatch, tmp_path, capsys):
    root = _greenfield(tmp_path, "g", git=False)
    monkeypatch.setattr("sys.argv", ["agentic", "stack", "review", "--product-dir", str(root)])
    with pytest.raises(SystemExit):
        cli.main()
    err = capsys.readouterr().err
    assert "not a git repository" in err and "git is not installed" in err


def test_stack_review_proceeds_once_git_init_makes_it_a_repository(monkeypatch, tmp_path, capsys):
    """The same path `git init` turns into a repository proceeds past the verb's own
    refusal into `--dry-run`'s report -- no `gh` call, no lock, no document."""
    root = _greenfield(tmp_path, "g")
    calls = fake_gh(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "stack", "review", "--product-dir", str(root), "--dry-run"]
    )
    cli.main()
    out = capsys.readouterr().out
    assert "anchor: (none, greenfield)" in out
    assert calls == []


def test_stack_review_dry_run_takes_no_lock_writes_no_file_and_calls_no_gh(
    monkeypatch, tmp_path, capsys
):
    root = _greenfield(tmp_path, "g")
    calls = fake_gh(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "stack", "review", "--product-dir", str(root), "--dry-run"]
    )
    cli.main()
    assert calls == []
    assert not (root / ".agentic" / "runs").exists()
    assert not (root / ".agentic" / "stack-reviews").exists()
    assert not (root / ".agentic" / "tasks").exists()


@pytest.mark.anyio
async def test_stack_review_refuses_on_an_already_open_proposal_spending_nothing(tmp_path):
    """`GhInbox` binds its `bounded_run` default at import time, so a real `main()`
    round trip cannot fake `gh` for `open_issues` the way `fake_gh` fakes `task()` -- this
    calls `cli._stack_review` directly with `MemoryInbox`, the same seam
    `test_eval_run_dry_run_prefers_the_set_override...` already uses for a full round
    trip over in-memory ports (ADR-0034)."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import MemoryInbox

    root = _greenfield(tmp_path, "g")
    marker = "<!-- agentic:stack-review anchor=none -->"
    url = "https://github.com/o/r/issues/9"
    ports = memory_ports(inbox=MemoryInbox(open_issues=[(url, f"{marker}\n\nolder review")]))
    ports.workspace.repositories[root] = root
    cfg = Configuration(product_dir=root)

    with pytest.raises(cli.CannotStart) as info:
        await cli._stack_review(cfg, ports, None, False)
    assert url in str(info.value)
    assert ports.tracker.opened_issues == []  # no write of any kind
    assert not (root / ".agentic" / "runs").exists()
    assert not (root / ".agentic" / "stack-reviews").exists()


def test_stack_review_every_other_subcommand_still_refuses_no_agentic_dir(monkeypatch, tmp_path):
    """`main()`'s `.agentic/` refusal stays global for every command but `stack review`
    (invariant): `close` is the cheapest other command to prove it on."""
    monkeypatch.setattr(
        "sys.argv", ["agentic", "close", "--issue", "1", "--product-dir", str(tmp_path)]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


@pytest.mark.anyio
async def test_stack_review_writes_its_document_under_stack_reviews_never_under_tasks(tmp_path):
    """A completed review (Brief present, the greenfield anchor firing vacuously) runs
    the interview and the author through `ScriptedRunner`, and its document lands
    under `.agentic/stack-reviews/`, never `.agentic/tasks/` (invariant)."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import ScriptedRunner, questions, stack_proposal

    root = _greenfield(tmp_path, "g")
    ask, author = "tech_lead_ephemeral_stack", "tech_lead_stack"
    runner = ScriptedRunner(
        **{ask: [questions(done=True, session="e1")]},
        **{author: [stack_proposal(session="t1")]},
    )
    ports = memory_ports(runner, knowledge=MemoryKnowledge(brief="# Brief\n\nfits."))
    ports.workspace.repositories[root] = root
    cfg = Configuration(product_dir=root)

    await cli._stack_review(cfg, ports, None, False)

    reviews_dir = root / ".agentic" / "stack-reviews"
    assert [p.name for p in reviews_dir.glob("*.md")]
    assert not (root / ".agentic" / "tasks").exists()
    [(title, body)] = ports.tracker.opened_issues
    assert "needs-info" not in title
    assert "keep the stack" in body


@pytest.mark.anyio
async def test_stack_review_with_no_brief_opens_needs_info_and_runs_no_role(tmp_path):
    """Spec item 1: sin `BRIEF.md` no decide -- `[needs-info]`, y ningún rol corre."""
    from agentic.shared.configuration import Configuration

    root = _greenfield(tmp_path, "g", brief=None)
    ports = memory_ports()  # MemoryKnowledge()'s own brief_text defaults to None
    ports.workspace.repositories[root] = root
    cfg = Configuration(product_dir=root)

    await cli._stack_review(cfg, ports, None, False)

    assert ports.runner.calls == []  # no role ran
    [(title, body)] = ports.tracker.opened_issues
    assert title.startswith("[needs-info]")
    assert "BRIEF.md" in body
    assert not (root / ".agentic" / "stack-reviews").exists()


@pytest.mark.anyio
async def test_stack_review_prints_and_opens_nothing_when_no_signal_fired(tmp_path, capsys):
    """A declared stack, a Brief no newer than it, and no `--reason`: no signal fires,
    so the real run prints and opens nothing, and runs no role either."""
    from agentic.shared.configuration import Configuration

    root = tmp_path / "g"
    root.mkdir()
    (root / ".agentic").mkdir()
    (root / ".agentic/stack.yaml").write_text(STACK)
    (root / "BRIEF.md").write_text("# Brief\n\nfits.\n")
    gitcmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*gitcmd, "init", "-q", "-b", "master", str(root)], check=True)
    subprocess.run([*gitcmd, "add", "-A"], cwd=root, check=True)
    subprocess.run([*gitcmd, "commit", "-q", "-m", "v0"], cwd=root, check=True)

    ports = memory_ports(knowledge=MemoryKnowledge(brief="# Brief\n\nfits.\n"))
    ports.workspace.repositories[root] = root
    cfg = Configuration(product_dir=root)

    await cli._stack_review(cfg, ports, None, False)

    out = capsys.readouterr().out
    assert "no signal fired" in out
    assert ports.tracker.opened_issues == []
    assert ports.runner.calls == []


@pytest.mark.anyio
async def test_stack_review_reason_is_a_trigger_reproduced_verbatim_in_the_issue_body(tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import ScriptedRunner, questions, stack_proposal

    root = _greenfield(tmp_path, "g")
    ask, author = "tech_lead_ephemeral_stack", "tech_lead_stack"
    runner = ScriptedRunner(
        **{ask: [questions(done=True, session="e1")]},
        **{author: [stack_proposal(session="t1")]},
    )
    ports = memory_ports(runner, knowledge=MemoryKnowledge(brief="# Brief\n\nfits."))
    ports.workspace.repositories[root] = root
    cfg = Configuration(product_dir=root)

    await cli._stack_review(cfg, ports, "a vendor sunset forces this", False)

    [(_, body)] = ports.tracker.opened_issues
    assert "a vendor sunset forces this" in body


# `merge_commit` (ADR-0038): written by `close`, backfilled onto an already-closed
# merged document, never touching what that document already recorded.


def _closed_without_merge_commit(ports, *, outcome="merged"):
    ports.documents.save(close_task())
    ports.documents.record_closure(
        "GH-9",
        {
            "closed_at": "2026-09-20T00:00:00.000Z",
            "delivery_outcome": outcome,
            "human_commits": "2",
            "human_lines_changed": "7",
            "time_to_merge_seconds": "99",
        },
    )


def test_close_writes_merge_commit_for_a_merged_pull_request_and_omits_it_otherwise(
    monkeypatch, tmp_path
):
    root = close_root(tmp_path)
    merged, unmerged = "https://github.com/o/r/pull/7", "https://github.com/o/r/pull/8"
    tracker = FakePullRequestTracker(
        {merged: close_pull_request(), unmerged: close_pull_request(state="CLOSED", merged=False)}
    )
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=merged))
    ports.documents.save(close_task(id="GH-10", pull_request=unmerged))
    _closed_run(monkeypatch, root, ports, issue=9)
    cli.main()
    _closed_run(monkeypatch, root, ports, issue=10)
    cli.main()
    assert ports.documents.load("GH-9")["merge_commit"] == "c0ffee"
    assert "merge_commit" not in ports.documents.load("GH-10")


def test_close_issue_backfills_only_merge_commit_and_exits_zero(monkeypatch, tmp_path, capsys):
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({"https://github.com/o/r/pull/7": close_pull_request()})
    ports = close_ports(root, tracker)
    _closed_without_merge_commit(ports)
    before = ports.documents.load("GH-9")
    _closed_run(monkeypatch, root, ports)
    cli.main()  # no SystemExit: exit 0
    after = ports.documents.load("GH-9")
    assert after["merge_commit"] == "c0ffee"
    assert {k: v for k, v in after.items() if k != "merge_commit"} == before
    assert "merge_commit backfilled" in capsys.readouterr().out
    # Now it is complete: a second run reaches no `gh` call and refuses as already closed.
    tracker.pull_request_refs.clear()
    _closed_run(monkeypatch, root, ports)
    with pytest.raises(SystemExit):
        cli.main()
    assert tracker.pull_request_refs == []


def test_close_all_backfills_and_exits_zero(monkeypatch, tmp_path, capsys):
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({"https://github.com/o/r/pull/7": close_pull_request()})
    ports = close_ports(root, tracker)
    _closed_without_merge_commit(ports)
    _all_run(monkeypatch, root, ports)
    cli.main()  # no SystemExit: exit 0
    out = capsys.readouterr().out
    assert "GH-9 (issue 9): merge_commit backfilled" in out
    assert "agentic: backfilled 1:" in out
    assert ports.documents.load("GH-9")["merge_commit"] == "c0ffee"
    assert ports.documents.load("GH-9")["human_commits"] == "2"


def test_a_closed_document_that_needs_no_merge_commit_reaches_no_gh_call(
    monkeypatch, tmp_path, capsys
):
    """The rejecting side of `needs_merge_commit`: a closed-unmerged document has no merge
    commit to backfill, on either path."""
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({"https://github.com/o/r/pull/7": close_pull_request()})
    ports = close_ports(root, tracker)
    _closed_without_merge_commit(ports, outcome="closed")
    before = ports.documents.path_for("GH-9").read_bytes()
    _all_run(monkeypatch, root, ports)
    cli.main()
    assert "GH-9 (issue 9): already closed" in capsys.readouterr().out
    _closed_run(monkeypatch, root, ports)
    with pytest.raises(SystemExit):
        cli.main()
    assert tracker.pull_request_refs == []
    assert ports.documents.path_for("GH-9").read_bytes() == before


# `agentic stack review` orders by the main line's first-parent chain, never by date
# (ADR-0038). The issue's table, in a real repo: a Brief written before the anchor and
# merged after it.

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def _git(root, *args, date=None):
    env = {**os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {})}
    out = subprocess.run(
        [*_GIT, *args], cwd=root, check=True, capture_output=True, text=True, env=env
    )
    return out.stdout.strip()


def _landing_repo(tmp_path, *, brief_first):
    """`master` with a stack-less root commit, then two `--no-ff` merges: a `brief` branch
    whose only commit is dated 18:26 and a `stack` branch whose only commit is dated 18:32.
    The merge order is the argument; the dates are the same either way."""
    root = tmp_path / "g"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    (root / "README").write_text("x")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "root")
    branches = {
        "brief": ("BRIEF.md", "# Brief\n\nfits.\n", "2026-09-24T18:26:11+00:00"),
        "stack": (".agentic/stack.yaml", STACK, "2026-09-24T18:32:51+00:00"),
    }
    for name, (path, text, date) in branches.items():
        _git(root, "checkout", "-q", "-b", name, "master")
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", name, date=date)
    _git(root, "checkout", "-q", "master")
    order = ["brief", "stack"] if brief_first else ["stack", "brief"]
    for name in order:
        _git(root, "merge", "-q", "--no-ff", "-m", f"merge {name}", name)
    return root


async def _dry_run(root, ports=None):
    from agentic.shared.configuration import Configuration

    ports = ports or memory_ports()
    ports.workspace.repositories[root] = root
    await cli._stack_review(Configuration(product_dir=root), ports, None, True)
    return ports


@pytest.mark.anyio
async def test_a_brief_written_before_the_anchor_but_merged_after_it_fires(tmp_path, capsys):
    root = _landing_repo(tmp_path, brief_first=False)
    await _dry_run(root)
    out = capsys.readouterr().out
    assert "the Brief changed since the last stack decision" in out
    # The recorded anchor is the landing merge, not the branch's own commit.
    landing = _git(
        root, "log", "--first-parent", "-1", "--format=%H", "HEAD", "--", ".agentic/stack.yaml"
    )
    assert f"anchor: {landing}" in out
    assert _git(root, "log", "-1", "--format=%H", "stack") != landing


@pytest.mark.anyio
async def test_a_brief_merged_before_the_anchor_does_not_fire_even_if_dated_before_it(
    tmp_path, capsys
):
    root = _landing_repo(tmp_path, brief_first=True)
    await _dry_run(root)
    out = capsys.readouterr().out
    assert "Signals fired: none" in out
    assert "no signal fired" in out


@pytest.mark.anyio
async def test_merged_since_counts_by_the_merge_commit_position_not_by_closed_at(tmp_path, capsys):
    root = _landing_repo(tmp_path, brief_first=True)
    chain = _git(root, "rev-list", "--first-parent", "--reverse", "HEAD").split("\n")
    with_brief, with_stack = chain[1], chain[2]
    # A third landing after the anchor.
    (root / "later.txt").write_text("x")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "later")
    later = _git(root, "rev-parse", "HEAD")
    ports = memory_ports()
    # `closed_at` is far in the past for all of them: the clock decides nothing.
    for task_id, merge_commit in (
        ("GH-1", with_brief),  # landed before the anchor
        ("GH-2", with_stack),  # the anchor itself
        ("GH-3", later),  # after it
        ("GH-4", None),  # never backfilled
    ):
        doc = {"id": task_id, "closed_at": "2001-01-01T00:00:00Z", "delivery_outcome": "merged"}
        if merge_commit:
            doc["merge_commit"] = merge_commit
        ports.documents.save(doc)
    await _dry_run(root, ports)
    assert "Merged since anchor: 1" in capsys.readouterr().out


# -- `agentic poll` (GH-137): the long-lived, per-product queue-mover -------------


def test_poll_child_label_names_the_command_and_the_issue_when_present():
    assert (
        cli._poll_child_label(["agentic", "resume", "--issue", "19", "--yes"]) == "poll-resume-19"
    )
    assert cli._poll_child_label(["agentic", "close", "--issue", "3"]) == "poll-close-3"
    assert cli._poll_child_label(["agentic", "run", "--detach"]) == "poll-run"
    assert cli._poll_child_label(["agentic"]) == "poll-run"  # no command at all: the default


def test_spawn_launcher_wraps_spawn_detached_with_a_freshly_minted_log_path(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration

    calls = []

    def fake_spawn_detached(argv, log_path):
        calls.append((argv, log_path))
        return 777

    monkeypatch.setattr(cli, "_spawn_detached", fake_spawn_detached)
    launcher = cli.SpawnLauncher(Configuration(product_dir=tmp_path))
    argv = ["agentic", "resume", "--issue", "19", "--yes", "--detach"]
    assert launcher.spawn(argv) == 777
    assert len(calls) == 1
    spawned_argv, log_path = calls[0]
    assert spawned_argv == argv
    assert "poll-resume-19" in log_path
    assert log_path.startswith(str(tmp_path))


@pytest.mark.anyio
async def test_poll_detach_spawns_the_child_and_returns_without_running_a_round(
    monkeypatch, tmp_path, capsys
):
    """Seam: the injected spawner on `_poll`. The parent never calls `poll.run_once`:
    a monkeypatch that raises would fail the test if it somehow did."""
    from agentic.core.process.application import poll as pollmod
    from agentic.shared.configuration import Configuration

    def broken_run_once(*args, **kwargs):
        raise AssertionError("a round must never run in the parent under --detach")

    monkeypatch.setattr(pollmod, "run_once", broken_run_once)
    spawn = FakeSpawn()
    lock = FakeLock()
    child_argv = ["agentic", "poll", "--detach"]
    await cli._poll(
        Configuration(product_dir=tmp_path),
        memory_ports(),
        60.0,
        run_lock=lock,
        log_file="/tmp/x.log",
        detach=True,
        child_argv=child_argv,
        spawn=spawn,
    )
    assert spawn.calls == [(child_argv, "/tmp/x.log")]
    assert lock.acquired == []  # the parent never holds it -- the child does, after it re-execs
    assert "54321" in capsys.readouterr().out


@pytest.mark.anyio
async def test_poll_flushes_every_line_of_a_round_before_awaiting_the_wait(monkeypatch, tmp_path):
    """Acceptance (f): GH-140's own fix for the first poller's 32 minutes of lines
    stuck in a block-buffered file -- every line a round printed is already flushed by
    the time `_poll` awaits the wait to the next one. Checked through the `sleep`
    `_poll` now takes as a keyword, never a monkeypatch on `anyio` itself: that wait is
    `_poll`'s own seam, not `anyio.sleep`'s."""
    from agentic.core.process.domain.task import PullRequest
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeLauncher, MemoryRunRegistry

    merged = PullRequest(
        state="MERGED",
        created_at="2026-10-01T00:00:00Z",
        merged_at="2026-10-02T00:00:00Z",
        commits=(),
        merge_commit="c0ffee",
    )
    tracker = FakePullRequestTracker(
        {
            "https://github.com/o/r/pull/7": merged,
            "https://github.com/o/r/pull/8": merged,
        }
    )
    ports = memory_ports(tracker=tracker)
    ports.documents.save({"id": "GH-3", "pull_request": "https://github.com/o/r/pull/7"})
    ports.documents.save({"id": "GH-4", "pull_request": "https://github.com/o/r/pull/8"})

    class RecordingStream:
        def __init__(self):
            self.flushes = 0

        def write(self, s):
            return len(s)

        def flush(self):
            self.flushes += 1

    stream = RecordingStream()
    monkeypatch.setattr(cli.sys, "stdout", stream)

    class Stop(Exception):
        pass

    flushes_before_the_wait = []

    async def sleep(seconds: float) -> None:
        flushes_before_the_wait.append(stream.flushes)
        raise Stop()

    with pytest.raises(Stop):
        await cli._poll(
            Configuration(product_dir=tmp_path),
            ports,
            60.0,
            run_lock=FakeLock(),
            registry=MemoryRunRegistry(),
            launcher=FakeLauncher(),
            argv0="agentic",
            sleep=sleep,
        )
    # Round 1 closed both GH-3 and GH-4: both lines were flushed before the wait.
    assert flushes_before_the_wait == [2]


@pytest.mark.anyio
async def test_eval_score_names_the_role_whose_playbook_differs(tmp_path, capsys):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "status": "open",
            "prompts": {"developer": "a", "pm": "p"},
            "playbooks": {"developer": "pb1"},
        }
    )
    ports.documents.save(
        {
            "id": "GH-2",
            "status": "open",
            "prompts": {"developer": "b", "pm": "p"},
            "playbooks": {"developer": "pb2"},
        }
    )
    await cli._eval_score(Configuration(product_dir=root), ports, None, str(tmp_path / "out.jsonl"))
    out = capsys.readouterr().out
    assert "playbook differs for: developer" in out
    assert "template differs for" not in out


@pytest.mark.anyio
async def test_eval_score_names_the_role_whose_template_differs(tmp_path, capsys):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "status": "open", "prompts": {"developer": "a", "pm": "p"}})
    ports.documents.save({"id": "GH-2", "status": "open", "prompts": {"developer": "a", "pm": "q"}})
    await cli._eval_score(Configuration(product_dir=root), ports, None, str(tmp_path / "out.jsonl"))
    out = capsys.readouterr().out
    assert "template differs for: pm" in out
    assert "playbook differs" not in out


# --- GH-176: the poller retakes a run a reboot cut, and `_resume` takes it up --------


def _two_step_graph(ran: list[str], seen_lock: list, lock, fail_b: bool):
    """A real, tiny `StateGraph`: `a` then `b`. Run with `fail_b`, it leaves the real
    per-task checkpoint a killed run leaves -- `a` done, `b` still pending, no interrupt."""
    from langgraph.graph import END, START, StateGraph

    from agentic.core.process.adapters.langgraph.graph import ESCALATE

    def a(state):
        ran.append("a")
        return {**state, "status": "developing"}

    def b(state):
        if fail_b:
            raise RuntimeError("the machine went down")
        ran.append("b")
        seen_lock.append(lock.read(state["id"]))
        return {**state, "status": "ready_for_review"}

    g = StateGraph(dict)
    g.add_node(ESCALATE, lambda state: {})
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g


@pytest.mark.anyio
async def test_the_poller_retakes_a_run_a_reboot_cut_and_resume_takes_over_its_lock(
    monkeypatch, tmp_path, capsys
):
    """The join: a real per-task checkpoint left mid-run, a real lock record written in
    a previous boot (by a pid that is alive now, as a reused pid would be), `run_once`
    deciding the retake from that unmodified record, and `_resume` driven from the argv
    it spawned."""
    from datetime import UTC, datetime

    from agentic.core.process.adapters.filesystem import FilesystemRunLock
    from agentic.core.process.application import poll
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports

    cfg = Configuration(product_dir=tmp_path)
    ports = memory_ports()
    ports.documents.save({"id": "GH-30", "title": "t", "status": "developing"})
    old_boot = FilesystemRunLock(tmp_path / "locks", boot_id=lambda: "old")
    old_boot.acquire("GH-30", "agentic run")  # this very pid: `os.kill` alone says alive
    lock = FilesystemRunLock(tmp_path / "locks", boot_id=lambda: "new")

    ran: list[str] = []
    seen: list = []
    crashed = _two_step_graph(ran, seen, lock, fail_b=True)
    async with composition.checkpointer(cfg, "GH-30") as saver:
        app = crashed.compile(checkpointer=saver)
        with pytest.raises(RuntimeError):
            await app.ainvoke(
                {"id": "GH-30", "title": "t", "status": "open"},
                config={"configurable": {"thread_id": "GH-30"}},
            )
    assert ran == ["a"]  # the checkpoint: `b` is pending, nothing interrupted

    launcher = FakeLauncher()
    lines = poll.run_once(
        cfg,
        ports,
        MemoryRunRegistry(),
        launcher,
        datetime.now(UTC),
        "agentic",
        register_timeout_s=0.0,
        sleep=lambda s: None,
        lock=lock,
        current_boot="new",
    )
    assert lines[0] == "agentic: poll: retake #30 -> pid 4242"
    (argv,) = launcher.spawned
    assert argv[:2] == ["agentic", "resume"] and {"--yes", "--detach"} <= set(argv)
    assert ports.tracker.comments == [("GH-30", "Retaken after reboot: previous boot")]

    ran.clear()
    monkeypatch.setattr(cli, "graph", _two_step_graph(ran, seen, lock, fail_b=False))
    await cli._resume(
        f"GH-{argv[argv.index('--issue') + 1]}",
        cfg,
        ports,
        yes=True,
        checkpointer=composition.checkpointer,
        run_lock=lock,
        registry=MemoryRunRegistry(),
    )
    assert "took over the lock" in capsys.readouterr().out
    assert ran == ["b"]  # only the pending node ran again
    (held,) = seen
    assert held.boot_id == "new" and held.pid == os.getpid()
    assert lock.read("GH-30") is None  # released on the way out, as any run is


class Records(Delivers):
    """A graph double that keeps what `ainvoke` was handed: the graph's input."""

    def __init__(self):
        super().__init__()
        self.inputs = []

    async def ainvoke(self, task, config):
        self.inputs.append(task)
        return self.final


async def _graph_input_of(monkeypatch, tmp_path, description, **cfg_fields):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    graph = Records()
    monkeypatch.setattr(cli, "graph", graph)
    task = {"id": "T-9", "title": "t", "description": description}
    cfg = Configuration(product_dir=tmp_path, **cfg_fields)
    await cli._run(task, cfg, memory_ports(), checkpointer=in_memory, run_lock=FakeLock())
    (entered,) = graph.inputs
    return entered


@pytest.mark.anyio
async def test_run_hands_the_graph_decided_when_the_request_carries_the_heading(
    monkeypatch, tmp_path
):
    entered = await _graph_input_of(monkeypatch, tmp_path, "Body\n\n## Decided\n- a\n")
    assert entered["decided"] is True


@pytest.mark.anyio
async def test_run_hands_the_graph_no_decided_key_without_the_heading(monkeypatch, tmp_path):
    entered = await _graph_input_of(monkeypatch, tmp_path, "Body, we decided things.\n")
    assert "decided" not in entered


@pytest.mark.anyio
async def test_run_with_the_cap_switched_off_hands_the_graph_no_decided_key(monkeypatch, tmp_path):
    entered = await _graph_input_of(
        monkeypatch, tmp_path, "Body\n\n## Decided\n- a\n", decided_cap=False
    )
    assert "decided" not in entered


class RecordsEntry(Delivers):
    """A graph double that remembers the state the run was entered with."""

    entered: dict | None = None

    async def ainvoke(self, task, config):
        self.entered = dict(task)
        return self.final


@pytest.mark.anyio
async def test_fresh_reads_the_landing_keys_before_the_archive_and_carries_them_into_the_run(
    monkeypatch, tmp_path
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    graph = RecordsEntry({"id": "GH-1", "title": "t", "status": "open"})
    monkeypatch.setattr(cli, "graph", graph)
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "system_commit": "abc1234",
            "landing_restarts": 1,
            "landing_cost_carried": 3.25,
            "cost_usd": 3.25,
            "sections": {"Landing Diagnoser": "The merge keeps failing on the same lock."},
        }
    )
    seen: list[object] = []
    real = cli._archive_previous_run
    monkeypatch.setattr(
        cli,
        "_archive_previous_run",
        lambda task_id, ports: (seen.append(ports.documents.load(task_id)), real(task_id, ports)),
    )
    task = {"id": "GH-1", "title": "t", "description": "d"}
    await cli._run(
        task, Configuration(product_dir=tmp_path), ports, fresh=True, checkpointer=in_memory
    )
    assert graph.entered is not None
    assert graph.entered["landing_restarts"] == 1
    assert graph.entered["landing_cost_carried"] == 3.25
    assert (
        "## Landing Diagnoser\n\nThe merge keeps failing on the same lock."
        in (graph.entered["description"])
    )
    assert seen  # the archive ran after the carry was read


@pytest.mark.anyio
async def test_fresh_of_a_task_the_landing_never_restarted_carries_nothing(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    graph = RecordsEntry({"id": "GH-1", "title": "t", "status": "open"})
    monkeypatch.setattr(cli, "graph", graph)
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234", "cost_usd": 4.0})
    task = {"id": "GH-1", "title": "t", "description": "d"}
    await cli._run(
        task, Configuration(product_dir=tmp_path), ports, fresh=True, checkpointer=in_memory
    )
    assert graph.entered is not None
    assert "landing_restarts" not in graph.entered
    assert "landing_cost_carried" not in graph.entered
    assert graph.entered["description"] == "d"


def test_land_run_kind_defaults_to_the_model_kind_and_takes_the_checks_kind(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    seen = capture_run(monkeypatch)
    base = ["agentic", "land", "--issue", "9", "--product-dir", str(root)]
    monkeypatch.setattr("sys.argv", base)
    cli.main()
    assert seen["fn"] is cli._land and seen["options"] == ("land_model",)
    monkeypatch.setattr("sys.argv", [*base, "--run-kind", "land_checks"])
    cli.main()
    assert seen["options"] == ("land_checks",)


@pytest.mark.parametrize("kind", ["merge", "role", "nonsense"])
def test_land_run_kind_other_than_the_two_land_kinds_is_a_usage_error(monkeypatch, tmp_path, kind):
    root = product(tmp_path, "p")
    capture_run(monkeypatch)
    argv = ["agentic", "land", "--issue", "9", "--product-dir", str(root), "--run-kind", kind]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("given", "registered"),
    [(None, "land_model"), ("land_checks", "land_checks"), ("land_model", "land_model")],
)
async def test_a_land_child_registers_once_under_the_kind_it_was_spawned_with(
    monkeypatch, tmp_path, given, registered
):
    from agentic.core.process.application.landing import LandResult
    from agentic.shared.configuration import Configuration
    from tests.fakes import MemoryRunRegistry, memory_ports

    registry = MemoryRunRegistry()
    monkeypatch.setattr(cli, "_registry_for", lambda cfg: registry)
    monkeypatch.setattr(cli, "land", lambda *args, **kwargs: LandResult("skipped", "no_head"))
    cfg = Configuration(product_dir=tmp_path)
    args = (cfg, memory_ports()) if given is None else (cfg, memory_ports(), given)

    await cli._land("GH-9", *args)

    assert registry.registered_kinds == [registered]
    assert registry.unregistered == 1


@pytest.mark.anyio
async def test_a_merge_child_registers_under_the_merge_kind(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import MemoryRunRegistry, memory_ports

    registry = MemoryRunRegistry()
    monkeypatch.setattr(cli, "_registry_for", lambda cfg: registry)
    cfg = Configuration(product_dir=tmp_path)

    with pytest.raises(cli.CannotStart):
        await cli._merge("GH-9", 9, cfg, memory_ports())

    assert registry.registered_kinds == ["merge"]
    assert registry.unregistered == 1


@pytest.mark.anyio
async def test_poll_a_timed_out_round_prints_its_line_sleeps_and_runs_the_next(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.process.application.ports import TrackerTimedOut
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeLauncher, MemoryRunRegistry

    rounds = []

    def run_once(*args, **kwargs):
        rounds.append(1)
        if len(rounds) == 1:
            raise TrackerTimedOut("gh pr list timed out after 60 s")
        return ["agentic: poll: nothing to do"]

    monkeypatch.setattr(cli.pollmod, "run_once", run_once)

    class Stop(Exception):
        pass

    sleeps = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise Stop()

    with pytest.raises(Stop):
        await cli._poll(
            Configuration(product_dir=tmp_path),
            memory_ports(),
            60.0,
            run_lock=FakeLock(),
            registry=MemoryRunRegistry(),
            launcher=FakeLauncher(),
            argv0="agentic",
            sleep=sleep,
        )
    assert sleeps == [60.0, 60.0] and len(rounds) == 2
    assert capsys.readouterr().out.splitlines() == [
        "agentic: poll: round ended early: gh pr list timed out after 60 s",
        "agentic: poll: nothing to do",
    ]


@pytest.mark.anyio
async def test_poll_a_plain_value_error_still_escapes_the_loop(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeLauncher, MemoryRunRegistry

    def run_once(*args, **kwargs):
        raise ValueError("not a timeout")

    monkeypatch.setattr(cli.pollmod, "run_once", run_once)

    async def sleep(seconds: float) -> None:
        raise AssertionError("a plain failure must not sleep and go on")

    with pytest.raises(ValueError, match="not a timeout"):
        await cli._poll(
            Configuration(product_dir=tmp_path),
            memory_ports(),
            60.0,
            run_lock=FakeLock(),
            registry=MemoryRunRegistry(),
            launcher=FakeLauncher(),
            argv0="agentic",
            sleep=sleep,
        )


# --- GH-420: the poller warns, once per change of state, when the checkout cannot fast-forward


async def _poll_rounds(monkeypatch, tmp_path, ports, rounds, between=lambda n: None):
    """Run `cli._poll` over `ports` for `rounds` rounds (`run_once` stubbed to say nothing);
    `between(n)` runs after round n, to change what the fake workspace answers."""
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeLauncher, MemoryRunRegistry

    monkeypatch.setattr(cli.pollmod, "run_once", lambda *args, **kwargs: [])

    class Stop(Exception):
        pass

    slept = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        between(len(slept))
        if len(slept) == rounds:
            raise Stop()

    with pytest.raises(Stop):
        await cli._poll(
            Configuration(product_dir=tmp_path),
            ports,
            60.0,
            run_lock=FakeLock(),
            registry=MemoryRunRegistry(),
            launcher=FakeLauncher(),
            argv0="agentic",
            sleep=sleep,
        )


def _blocked_ports(**answer):
    from agentic.core.process.application.ports import FastForwardView, Upstream

    ports = memory_ports()
    ports.workspace.upstream_answer = Upstream("origin/master", "a" * 40, None)
    ports.workspace.fast_forward_answer = FastForwardView(**answer)
    return ports


@pytest.mark.anyio
async def test_poll_warns_once_across_three_blocked_rounds_and_asks_upstream_once_per_round(
    monkeypatch, tmp_path, capsys
):
    ports = _blocked_ports(
        ancestor=True,
        changed=frozenset({".agentic/tasks/GH-1.md"}),
        dirty=frozenset({".agentic/tasks/GH-1.md"}),
    )
    await _poll_rounds(monkeypatch, tmp_path, ports, 3)
    assert capsys.readouterr().out.splitlines() == [
        "agentic: poll: product checkout cannot fast-forward to aaaaaaa (would_overwrite): "
        ".agentic/tasks/GH-1.md"
    ]
    assert ports.workspace.upstream_calls == 3
    assert ports.workspace.fast_forward_asks == ["a" * 40] * 3


@pytest.mark.anyio
async def test_poll_says_again_when_the_reason_changes_and_when_the_block_clears(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.process.application.ports import FastForwardView

    ports = _blocked_ports(ancestor=True, changed=frozenset({"x"}), dirty=frozenset({"x"}))
    answers = {
        1: FastForwardView(False, frozenset(), frozenset()),
        2: FastForwardView(True, frozenset(), frozenset()),
    }

    def between(n):
        if n in answers:
            ports.workspace.fast_forward_answer = answers[n]

    await _poll_rounds(monkeypatch, tmp_path, ports, 4, between)
    assert capsys.readouterr().out.splitlines() == [
        "agentic: poll: product checkout cannot fast-forward to aaaaaaa (would_overwrite): x",
        "agentic: poll: product checkout cannot fast-forward to aaaaaaa (diverged)",
        "agentic: poll: product checkout can fast-forward again",
    ]


@pytest.mark.anyio
async def test_poll_says_nothing_when_the_upstream_fell_back_to_head_and_keeps_its_memory(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.process.application.ports import Upstream

    ports = _blocked_ports(ancestor=False, changed=frozenset(), dirty=frozenset())
    noted = Upstream("HEAD", "b" * 40, "fetch failed")

    def between(n):
        ports.workspace.upstream_answer = (
            noted if n == 1 else Upstream("origin/master", "a" * 40, None)
        )

    await _poll_rounds(monkeypatch, tmp_path, ports, 3, between)
    # Round 1 warned; round 2 (a note) said nothing and kept the memory; round 3 is the same block.
    assert capsys.readouterr().out.splitlines() == [
        "agentic: poll: product checkout cannot fast-forward to aaaaaaa (diverged)"
    ]
    assert ports.workspace.fast_forward_asks == ["a" * 40] * 2


@pytest.mark.anyio
async def test_poll_warns_diverged_when_the_upstream_names_the_left_out_commits(
    monkeypatch, tmp_path, capsys
):
    """The real `upstream()` of a diverged checkout is a fetched tip *with* a note."""
    from agentic.core.process.application.ports import Upstream

    ports = _blocked_ports(ancestor=False, changed=frozenset(), dirty=frozenset())
    ports.workspace.upstream_answer = Upstream(
        "refs/remotes/origin/master", "a" * 40, "local commits left out: abc1234"
    )
    await _poll_rounds(monkeypatch, tmp_path, ports, 2)
    assert capsys.readouterr().out.splitlines() == [
        "agentic: poll: product checkout cannot fast-forward to aaaaaaa (diverged)"
    ]


@pytest.mark.anyio
async def test_poll_says_nothing_without_a_sha_and_never_for_a_clean_checkout(
    monkeypatch, tmp_path, capsys
):
    ports = memory_ports()
    await _poll_rounds(monkeypatch, tmp_path, ports, 2)
    clean = _blocked_ports(ancestor=True, changed=frozenset({"x"}), dirty=frozenset())
    await _poll_rounds(monkeypatch, tmp_path, clean, 2)
    assert capsys.readouterr().out == ""
    assert ports.workspace.fast_forward_asks == []


@pytest.mark.anyio
async def test_poll_prints_a_git_failure_once_and_the_round_goes_on(monkeypatch, tmp_path, capsys):
    ports = _blocked_ports(ancestor=True, changed=frozenset(), dirty=frozenset())
    ports.workspace.fast_forward_error = "not a repository"
    await _poll_rounds(monkeypatch, tmp_path, ports, 3)
    assert capsys.readouterr().out.splitlines() == [
        "agentic: poll: could not read the product checkout against the upstream: not a repository"
    ]
    assert ports.workspace.upstream_calls == 3


# --- GH-273: a task's scores, written at the finish of a run and at its close -----------


class RecordingScorer:
    """The scorer `cli._invoke` and `cli._close_document` write through, as a recording fake:
    what it was asked to write, and optionally a failure it raises instead of answering."""

    def __init__(self, raises: Exception | None = None):
        self.raises = raises
        self.calls: list[SimpleNamespace] = []

    def write(self, product, task_id, document, scores, task_time, dimensions) -> bool:
        self.calls.append(
            SimpleNamespace(
                product=product,
                task_id=task_id,
                document=document,
                scores={s.name: s for s in scores},
                task_time=task_time,
                dimensions=dimensions,
            )
        )
        if self.raises is not None:
            raise self.raises
        return True


class FakeApp:
    """`app.ainvoke` answering a canned state, or raising."""

    def __init__(self, final=None, raises: Exception | None = None):
        self.final, self.raises = final, raises

    async def ainvoke(self, graph_input, config=None):
        if self.raises is not None:
            raise self.raises
        return self.final


SCORED_RUN = {
    "role": "Developer",
    "section": "Developer",
    "version": 1,
    "session_id": "s",
    "model": "m",
    "cost_usd": 1.0,
    "cost_by_model": {},
    "turns": 1,
    "started_at": "2026-10-01T10:00:00.000Z",
    "ended_at": "2026-10-01T11:00:00.000Z",
    "queue_wait_s": 0.0,
}


def scored_task(**overrides):
    return close_task(runs=[SCORED_RUN], failures=[], **overrides)


def invoke_ports(root: Path):
    return close_ports(root, MemoryTracker())


async def invoke_for_scores(tmp_path, final, scorer, *, raises=None, ports=None, root=None):
    root = close_root(tmp_path) if root is None else root
    ports = invoke_ports(root) if ports is None else ports
    ports.documents.save(final)
    cfg = Configuration.from_sources({"product_dir": root}, {})
    entering = {"id": final["id"], "title": final["title"], "description": "d"}
    return await cli._invoke(
        FakeApp(final, raises), entering, {}, entering, ports, cfg, scorer=scorer
    )


@pytest.mark.anyio
async def test_a_finished_run_writes_its_scores_once_on_the_carrier_of_its_own_start(tmp_path):
    scorer = RecordingScorer()
    await invoke_for_scores(tmp_path, scored_task(), scorer)

    (call,) = scorer.calls
    assert (call.product, call.task_id) == ("p", "GH-9")
    assert call.task_time.start.isoformat() == "2026-10-01T10:00:00+00:00"
    assert call.task_time.end.isoformat() == "2026-10-01T11:00:00+00:00"
    assert call.scores["cost_usd"].value == 1.0
    assert call.scores["active_h"].value == 1.0
    assert "delivered" not in call.scores and "cycle_h" not in call.scores


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["escalated", "paused", "open"])
async def test_a_paused_invocation_writes_no_scores(tmp_path, status):
    scorer = RecordingScorer()
    await invoke_for_scores(tmp_path, scored_task(status=status), scorer)
    assert scorer.calls == []


@pytest.mark.anyio
async def test_a_crashed_invocation_writes_no_scores_and_still_raises(tmp_path):
    scorer = RecordingScorer()
    with pytest.raises(RuntimeError):
        await invoke_for_scores(tmp_path, scored_task(), scorer, raises=RuntimeError("boom"))
    assert scorer.calls == []


@pytest.mark.anyio
async def test_a_failing_scorer_leaves_the_runs_result_unchanged(tmp_path, capsys):
    final = scored_task()
    result = await invoke_for_scores(tmp_path, final, RecordingScorer(raises=RuntimeError("k")))
    assert result == final
    assert "scores" in capsys.readouterr().out


@pytest.mark.anyio
async def test_a_run_with_no_recorded_times_writes_nothing_rather_than_stamp_now(tmp_path):
    scorer = RecordingScorer()
    await invoke_for_scores(tmp_path, close_task(runs=[]), scorer)
    assert scorer.calls == []


REFERENCE = "https://github.com/o/r/pull/7"


def closing_ports(tmp_path, *, pull_request=None, issue_created="2026-08-31T01:00:00+00:00"):
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({REFERENCE: pull_request or close_pull_request()})
    if issue_created is not None:
        tracker.issue_created["GH-9"] = issue_created
    return root, close_ports(root, tracker)


def close_for_scores(root, ports, scorer):
    cfg = Configuration.from_sources({"product_dir": root}, {})
    return cli._close_document("GH-9", cfg, ports, scorer)


@pytest.mark.anyio
async def test_a_close_writes_delivered_and_cycle_h_on_the_carrier_the_finish_wrote(tmp_path):
    root, ports = closing_ports(tmp_path)
    finish, close = RecordingScorer(), RecordingScorer()
    await invoke_for_scores(tmp_path, scored_task(), finish, ports=ports, root=root)

    result = close_for_scores(root, ports, close)

    assert result.status == "closed"
    (before,), (after,) = finish.calls, close.calls
    assert "delivered" not in before.scores
    assert after.scores["delivered"].value == 1.0
    assert after.scores["cycle_h"].value == 24.0
    # The same start, so the same trace and the same score ids: the close upserts the finish.
    assert after.task_time == before.task_time
    assert after.scores["cost_usd"].id == before.scores["cost_usd"].id != ""
    assert after.scores["delivered"].id != ""


@pytest.mark.anyio
async def test_a_failed_issue_read_omits_cycle_h_and_never_fails_the_close(tmp_path, capsys):
    root, ports = closing_ports(tmp_path)
    ports.tracker.issue_created_errors.add("GH-9")
    ports.documents.save(scored_task())
    scorer = RecordingScorer()

    result = close_for_scores(root, ports, scorer)

    assert result.status == "closed" and result.outcome == "merged"
    (call,) = scorer.calls
    assert "cycle_h" not in call.scores and call.scores["delivered"].value == 1.0
    assert capsys.readouterr().out.count("cycle_h omitted") == 1


def test_a_failing_scorer_leaves_the_close_result_and_its_document_unchanged(tmp_path, capsys):
    root, ports = closing_ports(tmp_path)
    ports.documents.save(scored_task())

    result = close_for_scores(root, ports, RecordingScorer(raises=RuntimeError("secret-key")))

    assert result.status == "closed" and result.outcome == "merged"
    assert ports.documents.load("GH-9")["delivery_outcome"] == "merged"
    out = capsys.readouterr().out
    assert "not written (RuntimeError)" in out and "secret-key" not in out


def test_a_close_that_closes_nothing_writes_no_scores(tmp_path):
    root, ports = closing_ports(
        tmp_path, pull_request=close_pull_request(state="OPEN", merged=False)
    )
    ports.documents.save(scored_task())
    scorer = RecordingScorer()

    assert close_for_scores(root, ports, scorer).status == "still_open"
    assert scorer.calls == []


def test_a_closed_pull_request_that_never_merged_is_scored_not_delivered(tmp_path):
    root, ports = closing_ports(
        tmp_path, pull_request=close_pull_request(state="CLOSED", merged=False)
    )
    ports.documents.save(scored_task())
    scorer = RecordingScorer()

    close_for_scores(root, ports, scorer)

    (call,) = scorer.calls
    assert call.scores["delivered"].value == 0.0 and "cycle_h" not in call.scores


def test_the_composition_hands_a_scorer_only_when_all_three_variables_are_set(monkeypatch):
    from datetime import UTC, datetime

    from agentic.core.measurement.adapters.langfuse.scores import LangfuseScores
    from agentic.core.measurement.domain.task_scores import Dimensions, Score, TaskTime

    assert composition.task_scorer("apostrophe") is None
    monkeypatch.setenv("AGENTIC_LANGFUSE_HOST", "http://lf")
    monkeypatch.setenv("AGENTIC_LANGFUSE_PUBLIC_KEY", "pk")
    assert composition.task_scorer("apostrophe") is None
    monkeypatch.setenv("AGENTIC_LANGFUSE_SECRET_KEY", "")
    assert composition.task_scorer("apostrophe") is None
    monkeypatch.setenv("AGENTIC_LANGFUSE_SECRET_KEY", "sk")

    # A capturing transport that succeeds, so a refusal can only come from the binding and
    # not from the autouse failing transport. It is set before the scorer is built: the
    # writer resolves `transport or urllib_transport` at construction. Assertions on what it
    # recorded are made after `write` returns; the recorder runs on the adapter's thread.
    calls: list[tuple[str, bytes, dict[str, str]]] = []

    def recorder(url, body, headers, timeout_s):
        calls.append((url, body, headers))
        return 200, b"{}"

    monkeypatch.setattr(
        "agentic.core.measurement.adapters.langfuse.scores.urllib_transport", recorder
    )
    scorer = composition.task_scorer("apostrophe")
    assert isinstance(scorer, LangfuseScores)
    moment = datetime(2026, 10, 17, tzinfo=UTC)
    task_time = TaskTime(moment, moment)
    dimensions = Dimensions("v", "b", "a")
    scores = [Score("cost_usd", 1.0, "NUMERIC")]

    # Accepting control: the bound product reaches the transport, on both posts.
    assert scorer.write("apostrophe", "GH-1", {}, scores, task_time, dimensions) is True
    ingestion = [body for url, body, _ in calls if url.endswith("/api/public/ingestion")]
    assert len(ingestion) == 1
    events = json.loads(ingestion[0])["batch"]
    assert events
    assert {event["body"]["environment"] for event in events} == {"apostrophe"}
    assert any(url.endswith("/api/public/otel/v1/traces") for url, _, _ in calls)

    # Rejecting control: a foreign product is refused before any post. The recorder
    # succeeds, so this `False` is the refusal and not a failed post.
    posted = len(calls)
    assert scorer.write("gym", "GH-1", {}, scores, task_time, dimensions) is False
    assert len(calls) == posted
    # Never a `Configuration` field: a field would travel with the configuration into a role.
    fields = {ENV_PREFIX + name.upper() for name in Configuration.model_fields}
    assert not fields & set(composition.LANGFUSE_VARIABLES)


# `agentic scores backfill` (GH-273, subtask `backfill`): argv in, exit code, stdout and the
# scorer's own record out. The scorer is a fake with `LangfuseScores.write`'s shape; the
# tracker is `FakePullRequestTracker`, which also answers `issue_created_at`.


class FakeScorer:
    """`LangfuseScores.write`'s shape: records every call, answers `results` in order
    (`True` once they run out)."""

    def __init__(self, results=()):
        self.calls = []
        self._results = list(results)

    def write(self, product, task_id, document, scores, task_time, dimensions):
        self.calls.append((product, task_id, tuple(scores), task_time, dimensions))
        return self._results.pop(0) if self._results else True


def _backfill_run(monkeypatch, root: Path, ports, scorer, *extra: str) -> None:
    monkeypatch.setattr(cli, "default_ports", lambda cfg: ports)
    monkeypatch.setattr(cli.composition, "task_scorer", lambda product: scorer)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "scores", "backfill", "--product-dir", str(root), *extra]
    )


def _backfill(monkeypatch, root: Path, ports, scorer, *extra: str) -> int:
    _backfill_run(monkeypatch, root, ports, scorer, *extra)
    try:
        cli.main()
    except SystemExit as exit_:
        return exit_.code
    return 0


STARTED = "2026-09-20T10:00:00+00:00"
ENDED = "2026-09-20T11:00:00+00:00"


def finished_task(task_id="GH-9", **overrides):
    return close_task(
        id=task_id,
        title="A task",
        runs=[{"started_at": STARTED, "ended_at": ENDED, "queue_wait_s": 0, "cost_usd": 1.0}],
        **overrides,
    )


def close_it(ports, task_id="GH-9", outcome="merged"):
    ports.documents.record_closure(
        task_id,
        {"closed_at": "2026-09-21T00:00:00.000Z", "delivery_outcome": outcome},
    )


def test_a_second_backfill_sends_the_very_same_ids(monkeypatch, tmp_path, capsys):
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({"https://github.com/o/r/pull/7": close_pull_request()})
    tracker.issue_created["GH-9"] = "2026-09-19T00:00:00+00:00"
    ports = close_ports(root, tracker)
    ports.documents.save(finished_task())
    close_it(ports)
    scorer = FakeScorer()
    assert _backfill(monkeypatch, root, ports, scorer) == 0
    assert _backfill(monkeypatch, root, ports, scorer) == 0
    first, second = scorer.calls
    assert first == second
    assert {score.name for score in first[2]} >= {"cost_usd", "cycle_h", "delivered"}
    assert all(score.id for score in first[2])
    assert "scored 1, partial 0, failed 0" in capsys.readouterr().out.splitlines()[-1]


def test_a_second_backfill_of_a_task_with_families_sends_identical_ids_and_values(
    monkeypatch, tmp_path
):
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({"https://github.com/o/r/pull/7": close_pull_request()})
    tracker.issue_created["GH-9"] = "2026-09-19T00:00:00+00:00"
    ports = close_ports(root, tracker)
    ports.documents.save(
        finished_task(
            type="feature",
            changes_requested={"Tech Lead→Developer": 1},
            change_history=[
                {"pair": "Tech Lead→Developer", "category": "spec-scope", "reason": "r"}
            ],
            failures=[{"reason": "account_limit", "cost_usd": 0.5}],
            pause_log=[{"kind": "escalation", "value": "returns"}],
            cost_usd=2.0,
        )
    )
    close_it(ports)
    scorer = FakeScorer()
    assert _backfill(monkeypatch, root, ports, scorer) == 0
    assert _backfill(monkeypatch, root, ports, scorer) == 0
    first, second = ({s.name: (s.id, s.value) for s in call[2]} for call in scorer.calls)
    assert first == second
    assert first["returns.spec-scope"][1] == first["returns"][1] == 1.0
    assert first["failures.account_limit"][1] == 1.0
    assert first["escalations.returns"][1] == 1.0
    assert first["delivered_cost_usd.type.feature"][1] == 2.0
    assert len({id_ for id_, _ in first.values()}) == len(first)


def test_each_skip_reason_is_counted_and_a_skip_alone_exits_zero(monkeypatch, tmp_path, capsys):
    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(finished_task("GH-1", status="paused"))  # not finished
    ports.documents.save(
        close_task(id="GH-2", title="old", pull_request=None, status="ready_for_review")
    )  # no runs, no pull request, no issue time
    scorer = FakeScorer()
    assert _backfill(monkeypatch, root, ports, scorer) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "agentic: GH-1: skipped:not_finished" in lines
    assert "agentic: GH-2: skipped:no_time" in lines
    assert lines[-1] == "scored 0, partial 0, skipped:no_time 1, skipped:not_finished 1, failed 0"
    assert scorer.calls == []


def test_a_gh_read_that_fails_makes_the_document_partial_and_the_run_exit_one(
    monkeypatch, tmp_path, capsys
):
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({"https://github.com/o/r/pull/7": close_pull_request()})
    tracker.issue_created_errors.add("GH-9")
    ports = close_ports(root, tracker)
    ports.documents.save(finished_task())
    close_it(ports)
    scorer = FakeScorer()
    assert _backfill(monkeypatch, root, ports, scorer) == 1
    lines = capsys.readouterr().out.splitlines()
    assert "agentic: GH-9: partial:cycle_h" in lines
    assert lines[-1] == "scored 0, partial 1, failed 0"
    assert "cycle_h" not in {score.name for score in scorer.calls[0][2]}  # never a 0


def test_a_run_with_no_timestamps_takes_the_pull_requests_time_never_the_clock(
    monkeypatch, tmp_path
):
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({"https://github.com/o/r/pull/7": close_pull_request()})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(title="old"))
    scorer = FakeScorer()
    assert _backfill(monkeypatch, root, ports, scorer) == 0
    task_time = scorer.calls[0][3]
    assert task_time.start == task_time.end
    assert task_time.start.isoformat() == "2026-09-01T00:00:00+00:00"
    assert "active_h" not in {score.name for score in scorer.calls[0][2]}


def test_three_consecutive_failed_writes_stop_the_run_with_the_counts_so_far(
    monkeypatch, tmp_path, capsys
):
    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    for number in range(1, 6):
        ports.documents.save(finished_task(f"GH-{number}", pull_request=None))
    scorer = FakeScorer([False, False, False])
    assert _backfill(monkeypatch, root, ports, scorer) == 1
    out = capsys.readouterr().out.splitlines()
    assert len(scorer.calls) == 3  # GH-4 and GH-5 are never attempted
    assert "agentic: stopped after 3 consecutive failed writes" in out
    assert out[-1] == "scored 0, partial 0, failed 3"


def test_a_success_between_failures_resets_the_consecutive_count(monkeypatch, tmp_path, capsys):
    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    for number in range(1, 6):
        ports.documents.save(finished_task(f"GH-{number}", pull_request=None))
    scorer = FakeScorer([False, False, True, False, False])
    assert _backfill(monkeypatch, root, ports, scorer) == 1
    assert len(scorer.calls) == 5
    assert capsys.readouterr().out.splitlines()[-1] == "scored 1, partial 0, failed 4"


def test_dry_run_reads_everything_and_sends_nothing(monkeypatch, tmp_path, capsys):
    root = close_root(tmp_path)
    tracker = FakePullRequestTracker({"https://github.com/o/r/pull/7": close_pull_request()})
    ports = close_ports(root, tracker)
    ports.documents.save(finished_task())
    close_it(ports)
    scorer = FakeScorer()
    assert _backfill(monkeypatch, root, ports, scorer, "--dry-run") == 0
    assert scorer.calls == []
    assert tracker.pull_request_refs == ["https://github.com/o/r/pull/7"]
    assert capsys.readouterr().out.splitlines()[-1] == "scored 1, partial 0, failed 0"


def test_a_discarded_task_is_scored_without_delivered(monkeypatch, tmp_path):
    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(finished_task(status="discarded", pull_request=None))
    scorer = FakeScorer()
    assert _backfill(monkeypatch, root, ports, scorer) == 0
    names = {score.name for score in scorer.calls[0][2]}
    assert "delivered" not in names and "cycle_h" not in names
    assert {"cost_usd", "active_h", "returns", "origin", "type"} <= names


def test_without_a_scorer_and_without_dry_run_it_refuses_with_exit_two(
    monkeypatch, tmp_path, capsys
):
    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(finished_task(pull_request=None))
    assert _backfill(monkeypatch, root, ports, None) == 2
    assert "AGENTIC_LANGFUSE_HOST" in capsys.readouterr().err
    assert _backfill(monkeypatch, root, ports, None, "--dry-run") == 0


def test_a_backfill_writes_no_task_document(monkeypatch, tmp_path):
    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(finished_task(pull_request=None))
    before = ports.documents.path_for("GH-9").read_bytes()
    assert _backfill(monkeypatch, root, ports, FakeScorer()) == 0
    assert ports.documents.path_for("GH-9").read_bytes() == before


# --- GH-274: `agentic dashboards apply` ---------------------------------------------------


class DashboardFake:
    """A `DashboardStore` and its project, on a dict."""

    def __init__(self):
        from agentic.core.measurement.application import dashboards as app

        self.recorded = app.RECORDED_COLUMNS
        self.rows = {}

    def project_id(self):
        return "p1"

    def columns(self):
        return self.recorded

    def read(self, product):
        from agentic.core.measurement.domain import dashboards as dom

        return [r for r in self.rows.values() if dom.product_of(r) == product]

    def apply(self, changes):
        for row in changes.deletes:
            self.rows.pop(row.id, None)
        for row in changes.upserts:
            self.rows[row.id] = row


def _dashboards(monkeypatch, root, store, *extra, env=None, data=None):
    monkeypatch.setattr(
        cli, "default_ports", lambda cfg: close_ports(root, FakePullRequestTracker())
    )
    for name in (
        *cli.composition.DASHBOARD_VARIABLES,
        cli.composition.LANGFUSE_VARIABLES[1],
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli.composition, "dashboard_store", lambda: store)
    monkeypatch.setattr(cli.composition, "widget_data", lambda project: data)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "dashboards", "apply", "--product-dir", str(root), *extra]
    )
    try:
        cli.main()
    except SystemExit as exit_:
        return exit_.code
    return 0


DASHBOARD_ENV = {
    "AGENTIC_LANGFUSE_DATABASE_URL": "postgresql://x",
    "AGENTIC_LANGFUSE_PUBLIC_KEY": "pk",
}


def test_dashboards_apply_names_every_missing_variable_and_exits_2(monkeypatch, tmp_path, capsys):
    root = close_root(tmp_path)
    store = DashboardFake()
    code = _dashboards(monkeypatch, root, store, "--verify")
    err = capsys.readouterr().err
    assert code == 2
    for name in (
        "AGENTIC_LANGFUSE_DATABASE_URL",
        "AGENTIC_LANGFUSE_PUBLIC_KEY",
        "AGENTIC_LANGFUSE_CLICKHOUSE_URL",
        "AGENTIC_LANGFUSE_CLICKHOUSE_USER",
        "AGENTIC_LANGFUSE_CLICKHOUSE_PASSWORD",
    ):
        assert name in err
    assert store.rows == {}


def test_dashboards_apply_without_verify_does_not_ask_for_clickhouse(monkeypatch, tmp_path, capsys):
    code = _dashboards(monkeypatch, close_root(tmp_path), DashboardFake(), env=DASHBOARD_ENV)
    assert code == 0
    assert "CLICKHOUSE" not in capsys.readouterr().err


def test_a_dry_run_lists_every_create_with_title_and_id_and_writes_nothing(
    monkeypatch, tmp_path, capsys
):
    store = DashboardFake()
    code = _dashboards(monkeypatch, close_root(tmp_path), store, "--dry-run", env=DASHBOARD_ENV)
    out = capsys.readouterr().out
    assert code == 0 and store.rows == {}
    assert "create Panorama · " in out and "create Dónde va la plata · " in out
    assert "would created 23, updated 0, deleted 0, unchanged 0" in out


def test_a_second_apply_reports_everything_unchanged(monkeypatch, tmp_path, capsys):
    root, store = close_root(tmp_path), DashboardFake()
    assert _dashboards(monkeypatch, root, store, env=DASHBOARD_ENV) == 0
    capsys.readouterr()
    assert _dashboards(monkeypatch, root, store, env=DASHBOARD_ENV) == 0
    out = capsys.readouterr().out
    assert "created 0, updated 0, deleted 0, unchanged 23" in out
    assert "create " not in out


def test_verify_prints_rows_per_widget_and_a_zero_widget_exits_1(monkeypatch, tmp_path, capsys):
    class Data:
        def count(self, widget):
            return 0 if widget.key == "cycle_time" else 5

    env = {
        **DASHBOARD_ENV,
        "AGENTIC_LANGFUSE_CLICKHOUSE_URL": "http://c",
        "AGENTIC_LANGFUSE_CLICKHOUSE_USER": "u",
        "AGENTIC_LANGFUSE_CLICKHOUSE_PASSWORD": "p",
    }
    code = _dashboards(
        monkeypatch, close_root(tmp_path), DashboardFake(), "--verify", env=env, data=Data()
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "overview/delivered_per_week: 5 rows" in captured.out
    assert "overview/cycle_time: 0 rows" in captured.out
    assert "overview/cycle_time: reads 0 rows" in captured.err


def test_column_drift_refuses_with_exit_2_and_writes_nothing(monkeypatch, tmp_path, capsys):
    store = DashboardFake()
    store.recorded = store.recorded | {("dashboards", "owner_team")}
    code = _dashboards(monkeypatch, close_root(tmp_path), store, env=DASHBOARD_ENV)
    assert code == 2 and store.rows == {}
    assert "dashboards.owner_team" in capsys.readouterr().err


# --- GH-290: a gh that cannot reach GitHub ends one round, not the poller ----------------

# Captured from the pinned gh 2.100.0 on exit 1 (`GH_HOST=nonexistent.invalid gh api /`).
_UNREACHABLE_STDERR = (
    "error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com"
)


class _Stop(Exception):
    pass


def _poll_with(tmp_path, sleep):
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeLauncher, MemoryRunRegistry

    return cli._poll(
        Configuration(product_dir=tmp_path),
        memory_ports(),
        60.0,
        run_lock=FakeLock(),
        registry=MemoryRunRegistry(),
        launcher=FakeLauncher(),
        argv0="agentic",
        sleep=sleep,
    )


@pytest.mark.anyio
async def test_poll_an_unreachable_round_prints_its_lines_then_the_early_end_and_sleeps(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.process.application.ports import TrackerUnreachable

    def run_once(*args, **kwargs):
        yield "agentic: poll: launch #5 -> pid 1"
        raise TrackerUnreachable("gh issue list could not reach GitHub (error connecting to)")

    monkeypatch.setattr(cli.pollmod, "run_once", run_once)
    sleeps = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        raise _Stop()

    with pytest.raises(_Stop):
        await _poll_with(tmp_path, sleep)
    assert sleeps == [60.0]
    assert capsys.readouterr().out.splitlines() == [
        "agentic: poll: launch #5 -> pid 1",
        "agentic: poll: round ended early: gh issue list could not reach GitHub "
        "(error connecting to)",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        ValueError("not a connectivity failure"),
        cli.GitError("git failed"),
        OSError("disk"),
    ],
    ids=["value_error", "git_error", "os_error"],
)
async def test_poll_every_other_failure_still_escapes_the_loop(monkeypatch, tmp_path, error):
    def run_once(*args, **kwargs):
        yield "agentic: poll: nothing to do"
        raise error

    monkeypatch.setattr(cli.pollmod, "run_once", run_once)

    async def sleep(seconds: float) -> None:
        raise AssertionError("a failure that is not the transport must not sleep and go on")

    with pytest.raises(type(error)):
        await _poll_with(tmp_path, sleep)


def _poll_over_a_real_tracker(tmp_path, stderr, returncode=1, failing=1):
    """`_poll` over the real `run_once`, a real `GhTracker` and the real `run_gh`; the
    injected `run` fails the first `failing` `gh` calls with `stderr` and answers every
    other call with an empty list. The first call is the GH-348 sweep's read, which swallows
    a plain `ValueError` (the launch read after it fails again), so that case fails two."""
    import subprocess
    from pathlib import Path

    from agentic.core.process.adapters.github.tracker import GhTracker
    from agentic.shared.configuration import Configuration
    from tests.fakes import FakeLauncher, MemoryRunRegistry

    calls: list[list[str]] = []

    def run(args, cwd, capture_output, text, check):
        calls.append(args)
        if len(calls) <= failing:
            raise subprocess.CalledProcessError(returncode, args, stderr=stderr)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    ports = memory_ports(tracker=GhTracker(Path(tmp_path), run))
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise _Stop()

    poll = cli._poll(
        Configuration(product_dir=tmp_path),
        ports,
        60.0,
        run_lock=FakeLock(),
        registry=MemoryRunRegistry(),
        launcher=FakeLauncher(),
        argv0="agentic",
        sleep=sleep,
    )
    return poll, calls, sleeps


@pytest.mark.anyio
async def test_poll_the_incident_one_failed_round_then_the_next_round_retries(tmp_path, capsys):
    poll, calls, sleeps = _poll_over_a_real_tracker(tmp_path, _UNREACHABLE_STDERR, failing=2)

    with pytest.raises(_Stop):  # only the sentinel escapes: the poller lived through round 1
        await poll

    assert sleeps == [60.0, 60.0]  # two rounds ran
    first = calls[0]
    assert sum(1 for call in calls if call == first) >= 2  # round 2 made the same read again
    lines = capsys.readouterr().out.splitlines()
    # run_gh's line, once for the silent sweep read and once for the launch read that ends it
    assert sum(1 for line in lines if line.startswith("agentic: gh ")) == 2
    assert sum(1 for line in lines if "round ended early" in line) == 1
    assert not any("githubstatus" in line or "api.github.com" in line for line in lines)


@pytest.mark.anyio
async def test_poll_control_the_same_failure_with_bad_credentials_ends_the_poller(tmp_path):
    poll, _, sleeps = _poll_over_a_real_tracker(tmp_path, "HTTP 401: Bad credentials", failing=2)

    with pytest.raises(ValueError, match="Bad credentials") as raised:
        await poll
    assert type(raised.value) is ValueError
    assert sleeps == []


def write_compare_document(root: Path, task_id: str, baseline: str, runs: list[dict]) -> None:
    import yaml

    front = {
        "id": task_id,
        "title": "t",
        "status": "ready_for_review",
        "cost_usd": sum(r["cost_usd"] for r in runs),
        "system_baseline": baseline,
        "runs": runs,
    }
    tasks = root / ".agentic" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{task_id}.md").write_text(
        f"---\n{yaml.safe_dump(front)}---\n\n## Original request\n\nd\n"
    )


def compare_run(slug: str, cost: float, **tokens: int) -> dict:
    return {
        "role": slug,
        "slug": slug,
        "section": "s",
        "version": 1,
        "session_id": "x",
        "model": "m",
        "cost_usd": cost,
        "cost_by_model": {"m": cost},
        "turns": 4,
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, **tokens},
    }


@pytest.mark.anyio
async def test_eval_compare_prints_the_per_role_table_after_an_unchanged_scorecard(
    tmp_path, capsys
):
    root = eval_root(tmp_path, eval_set=None)
    write_compare_document(
        root, "GH-1", "bl-a", [compare_run("developer", 2.0, input=10, cache_read=500)]
    )
    write_compare_document(
        root,
        "GH-2",
        "bl-b",
        [compare_run("developer", 1.0, input=3), compare_run("developer", 0.5, input=4)],
    )
    cfg = Configuration(product_dir=root)

    await cli._eval_compare(cfg, memory_ports(), "bl-a", "bl-b", None)

    out = capsys.readouterr().out
    scorecard_part, _, table_part = out.partition("verdict:")
    assert "-- product / bl-a --" in scorecard_part
    assert "-- product / bl-b --" in scorecard_part
    assert "cost/delivery:" in scorecard_part
    # The estimate columns keep a space between their label and the figure.
    labels = [line for line in scorecard_part.splitlines() if line.startswith("actual/estimate ")]
    assert len(labels) == 4
    assert all(line.split(":", 1)[1].startswith(" ") for line in labels)
    # The table follows the verdict line, one block per baseline, one row per slug.
    assert "per-role usage -- product / bl-a" in table_part
    assert "per-role usage -- product / bl-b" in table_part
    rows = [line for line in table_part.splitlines() if line.startswith("developer")]
    assert len(rows) == 2
    assert "2.0000" in rows[0] and "500" in rows[0]
    assert "1.5000" in rows[1] and rows[1].split()[1] == "2"


def timed_run(role: str, section: str, start: str, end: str) -> dict:
    return {
        **compare_run(role.lower(), 0.1),
        "role": role,
        "section": section,
        "started_at": f"2026-10-20T{start}.000Z",
        "ended_at": f"2026-10-20T{end}.000Z",
    }


@pytest.mark.anyio
async def test_eval_compare_prints_the_parallel_factor_of_each_baseline(tmp_path, capsys):
    root = eval_root(tmp_path, eval_set=None)
    # bl-a: two Developers overlapping for 5 of their 10 minutes each -> 20 min over 15.
    write_compare_document(
        root,
        "GH-1",
        "bl-a",
        [
            timed_run("Developer", "Developer: a", "10:00:00", "10:10:00"),
            timed_run("Developer", "Developer: b", "10:05:00", "10:15:00"),
        ],
    )
    # bl-b: one Developer, then its review, strictly serial -> 1.0 for the task; a second
    # task with no timestamps adds nothing to either side of the ratio.
    write_compare_document(
        root,
        "GH-2",
        "bl-b",
        [timed_run("Developer", "Developer: a", "10:00:00", "10:10:00")],
    )
    write_compare_document(root, "GH-3", "bl-b", [compare_run("developer", 1.0)])
    cfg = Configuration(product_dir=root)

    await cli._eval_compare(cfg, memory_ports(), "bl-a", "bl-b", None)

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("parallel factor")]
    assert lines == [
        "parallel factor -- product / bl-a: 1.3333",
        "parallel factor -- product / bl-b: 1.0000",
    ]
    # It sits after the verdict, beside the other per-baseline reads, never inside a scorecard.
    assert out.index("verdict:") < out.index(lines[0])


@pytest.mark.anyio
async def test_eval_compare_says_n_a_when_a_baseline_has_no_timed_runs(tmp_path, capsys):
    root = eval_root(tmp_path, eval_set=None)
    write_compare_document(root, "GH-1", "bl-a", [compare_run("developer", 1.0)])
    write_compare_document(root, "GH-2", "bl-b", [compare_run("developer", 1.0)])

    await cli._eval_compare(Configuration(product_dir=root), memory_ports(), "bl-a", "bl-b", None)

    out = capsys.readouterr().out
    assert "parallel factor -- product / bl-a: n/a" in out
    assert "parallel factor -- product / bl-b: n/a" in out


# --- GH-295: criteria documents past the line cap are said, never refused -----------


def _commit_file(root, name, lines):
    (root / name).write_text("".join(f"rule {n}\n" for n in range(lines)))
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", name], cwd=root, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return head.stdout.strip()


def test_a_run_says_the_criteria_file_over_the_cap_at_head_and_proceeds(
    monkeypatch, tmp_path, capsys
):
    root = product(tmp_path, "p")
    _commit_file(root, "DESIGN_STANDARDS.md", 301)
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    seen = capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])

    cli.main()

    error = capsys.readouterr().err
    assert "agentic: `DESIGN_STANDARDS.md`: 301 lines (cap 300)" in error
    assert seen["task"]["title"] == "t"  # a warning, not a refusal


def test_a_run_with_a_file_of_exactly_the_cap_says_nothing_about_it(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    _commit_file(root, "DESIGN_STANDARDS.md", 300)
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])

    cli.main()

    assert "lines (cap" not in capsys.readouterr().err


def test_a_resume_measures_at_the_base_the_stack_check_returned_not_at_head(
    monkeypatch, tmp_path, capsys
):
    root = product(tmp_path, "p")
    base = _commit_file(root, "CODING_STANDARDS.md", 301)
    _commit_file(root, "CODING_STANDARDS.md", 200)  # HEAD is under the cap
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    monkeypatch.setattr(cli, "declared_stack_of", lambda task_id, ports: base)
    seen = capture_run(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "resume", "--issue", "9", "--yes", "--product-dir", str(root)]
    )

    cli.main()

    assert "agentic: `CODING_STANDARDS.md`: 301 lines" in capsys.readouterr().err
    assert seen["fn"] is cli._resume


TWO_HUMAN_DECISIONS = (
    "### Decision 1\n\nEscalated: the plan did not fit\n\nSplit it in two.\n\n"
    "### Decision 2\n\nKeep the CLI as is."
)


@pytest.mark.anyio
async def test_fresh_carries_every_human_decision_into_the_entered_state_and_the_next_is_third(
    monkeypatch, tmp_path
):
    from agentic.core.process.domain import rules
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    graph = RecordsEntry({"id": "GH-1", "title": "t", "status": "open"})
    monkeypatch.setattr(cli, "graph", graph)
    ports = memory_ports()
    ports.documents.save(
        {
            "id": "GH-1",
            "system_commit": "abc1234",
            "landing_restarts": 1,
            "landing_cost_carried": 3.25,
            "sections": {"Human": TWO_HUMAN_DECISIONS, "Landing Diagnoser": "Same lock again."},
            "landed_slices": [
                {"units": ["a"], "pull_request": "https://github.com/o/r/pull/7"},
                {"units": ["b"], "pull_request": "#9"},
            ],
        }
    )
    task = {"id": "GH-1", "title": "t", "description": "d"}
    await cli._run(
        task, Configuration(product_dir=tmp_path), ports, fresh=True, checkpointer=in_memory
    )
    assert graph.entered is not None
    assert graph.entered["sections"] == {"Human": TWO_HUMAN_DECISIONS}
    assert graph.entered["landing_restarts"] == 1
    description = graph.entered["description"]
    assert "## Landing Diagnoser\n\nSame lock again." in description
    assert "Already on master: a, b (#7, #9)" in description
    assert "Human" not in description and "Decision" not in description
    grown = rules.human_section(graph.entered, None, "Third call.")
    assert "### Decision 3\n\nThird call." in grown


@pytest.mark.anyio
async def test_fresh_carries_the_human_section_of_a_run_the_landing_never_restarted(
    monkeypatch, tmp_path
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    graph = RecordsEntry({"id": "GH-1", "title": "t", "status": "open"})
    monkeypatch.setattr(cli, "graph", graph)
    ports = memory_ports()
    ports.documents.save(
        {"id": "GH-1", "system_commit": "abc1234", "sections": {"Human": TWO_HUMAN_DECISIONS}}
    )
    task = {"id": "GH-1", "title": "t", "description": "d"}
    await cli._run(
        task, Configuration(product_dir=tmp_path), ports, fresh=True, checkpointer=in_memory
    )
    assert graph.entered is not None
    assert graph.entered["sections"] == {"Human": TWO_HUMAN_DECISIONS}
    assert "landing_restarts" not in graph.entered
    assert graph.entered["description"] == "d"


def test_landing_carry_of_an_unreadable_document_carries_nothing(monkeypatch):
    from tests.fakes import memory_ports

    ports = memory_ports()

    def unreadable(task_id):
        raise ValueError("not a task document")

    monkeypatch.setattr(ports.documents, "load", unreadable)
    assert cli._landing_carry("GH-1", ports) == (None, "")


class CrashedPipeline(Delivers):
    """A graph double whose checkpoint holds a run that crashed (no interrupt, nodes still
    to run), recording how the CLI seeded and invoked it."""

    def __init__(self, values, nxt=("pipeline",)):
        super().__init__({"id": "GH-1", "title": "t", "status": "ready_for_review"})
        self.values, self.next = values, nxt
        self.seeded: list[tuple[dict, str | None]] = []
        self.invoked: list[object] = []

    async def aget_state(self, config):
        return SimpleNamespace(values=self.values, next=self.next, interrupts=())

    async def aupdate_state(self, config, values, as_node=None):
        self.seeded.append((values, as_node))

    async def ainvoke(self, task, config):
        self.invoked.append(task)
        return self.final


def _crash_document(ports, reviewed_head="h-a"):
    """The document a crash in the pipeline leaves: a approved, b delivered, c and d building."""
    ports.documents.save(
        {
            "id": "GH-1",
            "title": "t",
            "description": "d",
            "status": "open",
            "current_role": "Developer",
            "cost_usd": 1.0,
            "subtasks": {
                "a": {"id": "a", "status": "approved", "branch": "t/a", "reviewed_head": "h-a"},
                "b": {"id": "b", "status": "delivered", "branch": "t/b", "depends_on": ["a"]},
                "c": {"id": "c", "status": "pending", "branch": "t/c"},
                "d": {"id": "d", "status": "pending", "branch": "t/d"},
            },
        }
    )
    ports.workspace.existing_branches = {
        "t/a": (reviewed_head, "base"),
        "t/b": ("h-b", "base"),
        "t/c": ("h-c-moved", "base"),
        "t/d": ("h-d", "base"),
    }


@pytest.mark.anyio
async def test_a_crash_in_the_pipeline_resumes_from_the_document_as_a_crash(monkeypatch, tmp_path):
    graph = CrashedPipeline({"id": "GH-1", "title": "t", "status": "open"})
    monkeypatch.setattr(cli, "graph", graph)
    ports = memory_ports()
    _crash_document(ports)

    await cli._resume(
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        yes=True,
        checkpointer=in_memory,
        run_lock=FakeLock(),
    )

    [(seed, as_node)] = graph.seeded
    assert as_node == "pipeline" and set(seed["subtasks"]) == {"a", "b", "c", "d"}
    assert graph.invoked == [None]  # a crash: never `Command(resume=...)`, never `pause`
    [(_, comment)] = ports.tracker.comments
    assert comment.startswith("Resumed: pipeline, interrupted at Developer,")


@pytest.mark.anyio
async def test_a_dry_run_of_a_crash_in_the_pipeline_reads_the_document(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(cli, "graph", CrashedPipeline({"id": "GH-1", "status": "open"}))
    ports = memory_ports()
    _crash_document(ports)

    await cli._resume(
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        dry_run=True,
        checkpointer=in_memory,
        run_lock=FakeLock(),
    )

    shown = capsys.readouterr().out
    assert "from:      document" in shown and "resumes:   pipeline" in shown


@pytest.mark.anyio
async def test_a_crash_elsewhere_than_the_pipeline_still_resumes_from_the_checkpoint(
    monkeypatch, tmp_path
):
    """The input the rule must not send to the document: the same crash, another node."""
    graph = CrashedPipeline({"id": "GH-1", "title": "t", "status": "open"}, nxt=("integrate",))
    monkeypatch.setattr(cli, "graph", graph)
    ports = memory_ports()
    _crash_document(ports)

    await cli._resume(
        "GH-1",
        Configuration(product_dir=tmp_path),
        ports,
        yes=True,
        checkpointer=in_memory,
        run_lock=FakeLock(),
    )

    assert graph.seeded == [] and graph.invoked == [None]


@pytest.mark.anyio
async def test_a_crash_whose_approved_branch_moved_after_its_review_is_refused(
    monkeypatch, tmp_path
):
    graph = CrashedPipeline({"id": "GH-1", "status": "open"})
    monkeypatch.setattr(cli, "graph", graph)
    ports = memory_ports()
    _crash_document(ports, reviewed_head="h-a-moved")

    with pytest.raises(cli.CannotStart, match="approved at h-a, but `t/a` is at h-a-moved"):
        await cli._resume(
            "GH-1",
            Configuration(product_dir=tmp_path),
            ports,
            yes=True,
            checkpointer=in_memory,
            run_lock=FakeLock(),
        )

    assert graph.seeded == [] and graph.invoked == [] and ports.tracker.comments == []


class _Signalling:
    """A `work` that sends its own process the signal(s) and then waits to be cancelled;
    `shielded` keeps it from unwinding for that many seconds, a role that outlives the
    unwinding. Records the reaper's calls at the moment the cancellation reaches it."""

    def __init__(self, reaper, *, times=1, shielded=0.0, lock=None, stop=None):
        self.reaper, self.times, self.shielded = reaper, times, shielded
        self.lock, self.stop = lock, stop
        self.at_cancel: list | None = None

    async def __call__(self):
        with contextlib.ExitStack() as held:
            if self.lock is not None:
                held.enter_context(cli._locked(self.lock, "T-9", self.stop))
            for _ in range(self.times):
                signal.raise_signal(signal.SIGTERM)
            try:
                await anyio.sleep_forever()
            except anyio.get_cancelled_exc_class():
                self.at_cancel = list(self.reaper.calls)
                with anyio.CancelScope(shield=True):
                    await anyio.sleep(self.shielded)
                raise


async def _until(condition, seconds=5.0):
    with anyio.fail_after(seconds):
        while not condition():
            await anyio.sleep(0.01)


@pytest.mark.anyio
async def test_a_signal_reaps_only_the_checks_before_it_cancels_and_marks_the_flag():
    from agentic.core.process.adapters.shell import EventStopFlag
    from tests.fakes import FakeReaper

    reaper, flag = FakeReaper(), EventStopFlag()
    stop = cli.StopCause(flag=flag, reaper=reaper)
    work = _Signalling(reaper)
    with pytest.raises(SystemExit):
        await cli._signal_guarded(work, stop)
    await _until(lambda: len(reaper.calls) >= 1)
    check = ("run", "check", os.getpid(), None)
    perf = ("run", "perf", os.getpid(), None)
    assert work.at_cancel in ([], [check], [check, perf])  # never a role's processes before it
    assert reaper.calls == [check, perf]
    assert flag.is_set()
    assert stop.signalled


@pytest.mark.anyio
async def test_a_second_signal_starts_no_second_reap_and_no_exit():
    from agentic.core.process.adapters.shell import EventStopFlag
    from tests.fakes import FakeReaper

    reaper, exits = FakeReaper(), []
    stop = cli.StopCause(flag=EventStopFlag(), reaper=reaper)
    work = _Signalling(reaper, times=3)
    with pytest.raises(SystemExit):
        await cli._signal_guarded(work, stop, hard_exit=exits.append)
    await _until(lambda: len(reaper.calls) >= 2)
    await anyio.sleep(0.2)
    assert reaper.calls == [
        ("run", "check", os.getpid(), None),
        ("run", "perf", os.getpid(), None),
    ]
    assert exits == []


@pytest.mark.anyio
async def test_the_deadline_reaps_roles_releases_the_lock_once_and_exits_once():
    from agentic.core.process.adapters.shell import EventStopFlag
    from tests.fakes import FakeReaper

    reaper, lock, exits, flushed = FakeReaper(), FakeLock(), [], []
    stop = cli.StopCause(flag=EventStopFlag(), reaper=reaper)
    work = _Signalling(reaper, times=2, shielded=0.6, lock=lock, stop=stop)
    with pytest.raises(SystemExit):
        await cli._signal_guarded(
            work,
            stop,
            deadline_s=0.05,
            hard_exit=exits.append,
            flush=lambda: flushed.append(1),
        )
    pid = os.getpid()
    assert reaper.calls == [
        ("run", "check", pid, None),
        ("run", "perf", pid, None),
        ("run", "T-9", pid, None),
        ("run", "check", pid, None),
        ("run", "perf", pid, None),
    ]
    assert lock.released == ["T-9"]  # the deadline released it; `_locked`'s finally did not
    assert (exits, flushed) == ([1], [1])


@pytest.mark.anyio
async def test_the_deadline_never_fires_when_the_run_unwinds_in_time():
    from agentic.core.process.adapters.shell import EventStopFlag
    from tests.fakes import FakeReaper

    reaper, exits = FakeReaper(), []
    stop = cli.StopCause(flag=EventStopFlag(), reaper=reaper)
    with pytest.raises(SystemExit):
        await cli._signal_guarded(_Signalling(reaper), stop, deadline_s=0.3, hard_exit=exits.append)
    await anyio.sleep(0.5)
    assert exits == []
    assert len(reaper.calls) == 2


@pytest.mark.anyio
async def test_the_previous_signal_handlers_come_back_after_the_guard():
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    with pytest.raises(SystemExit):
        from agentic.core.process.adapters.shell import EventStopFlag
        from tests.fakes import FakeReaper

        reaper = FakeReaper()
        await cli._signal_guarded(
            _Signalling(reaper), cli.StopCause(flag=EventStopFlag(), reaper=reaper)
        )
    assert {s: signal.getsignal(s) for s in before} == before


@pytest.mark.anyio
async def test_a_run_stopped_that_reaches_the_guard_is_the_cancellation_not_a_traceback():
    from agentic.core.process.application.ports import RunStopped

    stop = cli.StopCause()

    async def work():
        raise RunStopped

    with pytest.raises(SystemExit) as info:
        await cli._signal_guarded(work, stop)
    assert info.value.code == 1
    assert stop.signalled is True


@pytest.mark.anyio
async def test_a_stopped_run_releases_its_lock_once_and_never_marks_it_crashed():
    lock = FakeLock()
    stop = cli.StopCause()

    async def work():
        with cli._locked(lock, "T-9", stop):
            from agentic.core.process.application.ports import RunStopped

            raise RunStopped

    with pytest.raises(SystemExit):
        await cli._signal_guarded(work, stop)
    assert (lock.released, lock.kept, lock.kept_crashed) == (["T-9"], [], [])


# `build_parser()` (GH-400, subtask `entry-points`): the command line as a tree the docs
# generator can walk without running anything, and `main()` still answers as before.
COMMAND_PATHS = [
    ("run",),
    ("resume",),
    ("close",),
    ("merge",),
    ("land",),
    ("restore",),
    ("eval",),
    ("eval", "run"),
    ("eval", "score"),
    ("eval", "compare"),
    ("eval", "findings"),
    ("stack",),
    ("stack", "review"),
    ("scores",),
    ("scores", "backfill"),
    ("dashboards",),
    ("dashboards", "apply"),
    ("poll",),
    ("reflect",),
    ("perf",),
]


def _main_without_env(monkeypatch, argv):
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_replace_environ", lambda env: None)
    monkeypatch.setattr("sys.argv", ["agentic", *argv])
    with pytest.raises(SystemExit) as info:
        cli.main()
    return info.value.code


def test_build_parser_keys_every_command_by_its_path_in_creation_order(monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    tree = cli.build_parser()
    assert list(tree.commands) == COMMAND_PATHS
    assert tree.root.prog == "agentic"
    assert all(parser.prog.split()[1:] == list(path) for path, parser in tree.commands.items())


def test_build_parser_reads_no_environment(monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    first = {path: parser.format_help() for path, parser in cli.build_parser().commands.items()}
    monkeypatch.setenv(ENV_PREFIX + "PRODUCT_DIR", "/elsewhere")
    monkeypatch.setenv(ENV_PREFIX + "VERBOSE", "1")
    second = {path: parser.format_help() for path, parser in cli.build_parser().commands.items()}
    assert first == second


def test_main_prints_the_help_of_the_root_and_of_every_command(monkeypatch, capsys):
    tree = cli.build_parser()
    monkeypatch.setenv("COLUMNS", "100")
    assert _main_without_env(monkeypatch, ["--help"]) == 0
    assert capsys.readouterr().out == tree.root.format_help()
    for path, parser in tree.commands.items():
        assert _main_without_env(monkeypatch, [*path, "--help"]) == 0
        assert capsys.readouterr().out == parser.format_help(), path


def test_main_still_refuses_a_run_with_neither_title_nor_issue(monkeypatch, capsys):
    assert _main_without_env(monkeypatch, ["run"]) == 2
    run = cli.build_parser().commands[("run",)]
    assert capsys.readouterr().err == (
        f"{run.format_usage()}agentic run: error: give a title and a description, or --issue N\n"
    )


# GH-389: the failures of `announce` enter the resumed state through the reducer, with a
# second `aupdate_state` that must not disturb the run. A real LangGraph (no graph double)
# whose first node after the pause saves the document, as the real pipeline does.
def _pausing_graph(ports, seen):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    from agentic.core.process.adapters.langgraph.graph import ESCALATE, PIPELINE
    from agentic.core.process.domain.task import TaskInput, TaskState

    def escalate(state):
        ports.documents.save(state)  # the first node after the entry saves the document
        return {"status": "waiting_on_human", "escalated": "needs a decision"}

    def pause(state):
        interrupt("needs a decision")
        return {}

    def pipeline(state):
        seen["rows"] = list(state.get("tracker_failures", []))
        ports.documents.save(state)
        return {"status": "ready_for_review", "escalated": None}

    g = StateGraph(TaskState, input_schema=TaskInput)
    g.add_node(ESCALATE, escalate)
    g.add_node("pause", pause)
    g.add_node(PIPELINE, pipeline)
    g.add_edge(START, ESCALATE)
    g.add_edge(ESCALATE, "pause")
    g.add_edge("pause", PIPELINE)
    g.add_edge(PIPELINE, END)
    return g


def _watching_second_update(monkeypatch):
    """The `next` of the checkpoint right before and right after each second update."""
    seen = []
    real = cli._record_tracker_failures

    async def watched(app, config, failures, as_node):
        before = (await app.aget_state(config)).next
        await real(app, config, failures, as_node)
        after = await app.aget_state(config)
        seen.append((before, after.next, after.values.get("tracker_failures")))

    monkeypatch.setattr(cli, "_record_tracker_failures", watched)
    return seen


@pytest.mark.anyio
@pytest.mark.parametrize("from_document", [False, True])
async def test_a_failed_resumed_comment_is_recorded_and_the_run_goes_on(
    monkeypatch, tmp_path, from_document
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import BadGatewayComments, memory_ports

    # The "Resumed:" note is a real `GhTracker` call over a fake `gh` that keeps answering 502.
    ports, seen = memory_ports(tracker=BadGatewayComments()), {}
    monkeypatch.setattr(cli, "graph", _pausing_graph(ports, seen))
    updates = _watching_second_update(monkeypatch)
    cfg = Configuration(product_dir=tmp_path)
    saver, task = Saver(), {"id": "GH-1", "title": "Export", "description": "CSV."}
    ports.tracker.earlier = ["Escalated: needs a decision", "Go on."]
    if from_document:
        ports.documents.save(
            {**task, "type": "bug", "status": "waiting_on_human", "escalated": "needs a decision"}
        )
    else:
        with pytest.raises(SystemExit):
            await cli._run(task, cfg, ports, checkpointer=saver)

    await cli._resume("GH-1", cfg, ports, from_document=from_document, yes=True, checkpointer=saver)

    assert seen["rows"] == [{"target": "comment", "cls": "transient", "at": "2026-10-28T00:00:00Z"}]
    assert len(ports.tracker.gh_calls) == 3 and ports.tracker.slept  # retried, then given up
    assert ports.documents.saved[-1]["tracker_failures"] == seen["rows"]
    [(before, after, rows)] = updates
    assert before == after and rows == seen["rows"]  # the update did not move the checkpoint


@pytest.mark.anyio
async def test_a_resume_where_every_note_lands_makes_no_second_update(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports, seen = memory_ports(), {}
    monkeypatch.setattr(cli, "graph", _pausing_graph(ports, seen))
    updates = _watching_second_update(monkeypatch)
    cfg = Configuration(product_dir=tmp_path)
    saver, task = Saver(), {"id": "GH-1", "title": "Export", "description": "CSV."}
    ports.tracker.earlier = ["Escalated: needs a decision", "Go on."]
    with pytest.raises(SystemExit):
        await cli._run(task, cfg, ports, checkpointer=saver)
    await cli._resume("GH-1", cfg, ports, yes=True, checkpointer=saver)
    [(before, after, rows)] = updates
    assert before == after and not rows and seen["rows"] == []  # nothing recorded
    assert ports.documents.saved[-1].get("tracker_failures", []) == []


@pytest.mark.anyio
async def test_no_failure_means_no_second_aupdate_state():
    calls = []

    class App:
        async def aupdate_state(self, config, values, as_node=None):
            calls.append((values, as_node))

    row = {"target": "comment", "cls": "transient", "at": "2026-10-28T00:00:00Z"}
    await cli._record_tracker_failures(App(), {}, [], "escalate")
    assert calls == []
    await cli._record_tracker_failures(App(), {}, [row], "escalate")
    assert calls == [({"tracker_failures": [row]}, "escalate")]


@pytest.mark.anyio
async def test_a_failed_archive_notice_enters_the_run_as_a_tracker_failure(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports = memory_ports()
    monkeypatch.setattr(cli, "graph", _pausing_graph(ports, {}))
    ports.documents.save(
        {"id": "GH-1", "system_commit": "abc1234", "cost_usd": 4.0, "escalated": "old"}
    )
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")
    ports.tracker.comment_failures = 1  # the "Started over" notice keeps failing
    cfg = Configuration(product_dir=tmp_path)
    task = {"id": "GH-1", "title": "t", "description": "d"}
    with pytest.raises(SystemExit):
        await cli._run(task, cfg, ports, fresh=True, checkpointer=Saver())
    assert ports.tracker.unlabeled == [("GH-1", "needs-human")]  # the other write still ran
    saved = ports.documents.saved[-1]
    assert [(r["target"], r["cls"]) for r in saved["tracker_failures"]] == [("comment", "refused")]


@pytest.mark.anyio
async def test_a_run_whose_archive_notice_landed_enters_with_no_failure(monkeypatch, tmp_path):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    ports = memory_ports()
    monkeypatch.setattr(cli, "graph", _pausing_graph(ports, {}))
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234", "cost_usd": 4.0})
    cfg = Configuration(product_dir=tmp_path)
    task = {"id": "GH-1", "title": "t", "description": "d"}
    with pytest.raises(SystemExit):
        await cli._run(task, cfg, ports, fresh=True, checkpointer=Saver())
    assert not ports.documents.saved[-1].get("tracker_failures")


# --- agentic restore (GH-406) --------------------------------------------------------------


def test_restore_takes_the_issue_and_the_archive_prefix_and_runs_the_restore(monkeypatch, tmp_path):
    root = product(tmp_path, "restoring")
    asked: list[object] = []
    monkeypatch.setattr(cli, "default_ports", lambda cfg: memory_ports())
    monkeypatch.setattr(cli.anyio, "run", lambda func, *args: asked.append(args[0]))
    argv = ["agentic", "restore", "--issue", "374", "--from", "archive/run1-93e1891"]
    monkeypatch.setattr("sys.argv", [*argv, "--product-dir", str(root)])

    cli.main()

    (work,) = asked
    assert work.func is cli._restore
    assert work.args[:2] == ("GH-374", "archive/run1-93e1891")


@pytest.mark.parametrize(
    "argv",
    [
        ["restore", "--from", "archive/run1-93e1891"],
        ["restore", "--issue", "374"],
        ["restore", "--issue", "374", "--from", "agentic/GH-374"],
        ["restore", "--issue", "374", "--from", "archive/run1-"],
    ],
    ids=["no issue", "no prefix", "not an archive prefix", "no commit"],
)
def test_restore_refuses_a_missing_or_malformed_argument_before_touching_anything(
    monkeypatch, tmp_path, argv
):
    root = product(tmp_path, "restoring")
    monkeypatch.setattr(cli, "default_ports", lambda cfg: memory_ports())
    monkeypatch.setattr("sys.argv", ["agentic", *argv, "--product-dir", str(root)])

    with pytest.raises(SystemExit) as info:
        cli.main()

    assert info.value.code == 2


@pytest.mark.anyio
async def test_restore_prints_the_refusal_and_exits_like_any_command_that_cannot_start(
    tmp_path, capsys
):
    root = product(tmp_path, "restoring")
    cfg = Configuration(product_dir=root)

    with pytest.raises(cli.CannotStart, match="GH-374 refused: no_archive") as info:
        await cli._restore("GH-374", "archive/run1-93e1891", cfg, memory_ports())

    assert info.value.code == 2


@pytest.mark.anyio
async def test_restore_of_a_task_whose_lock_is_held_refuses_task_live(tmp_path):
    root = product(tmp_path, "restoring")
    cfg = Configuration(product_dir=root)
    lock = cli._lock_for(cfg)
    lock.acquire("GH-374", "agentic run --issue 374")
    try:
        with pytest.raises(cli.CannotStart, match="GH-374 refused: task_live"):
            await cli._restore("GH-374", "archive/run1-93e1891", cfg, memory_ports())
    finally:
        lock.release("GH-374")
