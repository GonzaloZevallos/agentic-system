import json
import os
import subprocess
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import cli
from agentic.core.process.adapters.github import inbox


@asynccontextmanager
async def in_memory(cfg):
    yield InMemorySaver()


def product(tmp_path, name):
    root = tmp_path / name
    (root / ".agentic").mkdir(parents=True)
    return root


def capture_run(monkeypatch):
    """Replace the graph run with a recorder of what the CLI would have run."""
    seen = {}

    def fake_run(work):
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
    """Only `gh`. The patch lands on the `subprocess` module itself, which every adapter
    shares, so anything else (preflight asking git about the product's repo) has to go
    through for real."""
    calls = []
    real = subprocess.run

    def run(cmd, **kwargs):
        if cmd[0] != "gh":
            return real(cmd, **kwargs)
        calls.append((cmd, kwargs))
        if returncode:
            raise subprocess.CalledProcessError(returncode, cmd, stderr=stderr)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(inbox.subprocess, "run", run)
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
    async def __call__(self, cfg):
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
    monkeypatch.setattr(cli, "graph", build_graph(lambda cfg: ports))
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
