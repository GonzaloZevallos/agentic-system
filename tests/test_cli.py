import json
import subprocess

import pytest

from agentic import cli
from agentic.core.process.adapters.github import inbox


def product(tmp_path, name):
    root = tmp_path / name
    (root / ".agentic").mkdir(parents=True)
    return root


def capture_run(monkeypatch):
    """Replace the graph run with a recorder of what the CLI would have run."""
    seen = {}

    def fake_run(fn, task, cfg, ports):
        seen.update(task=task, cfg=cfg, ports=ports)

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
    calls = []

    def run(cmd, **kwargs):
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
        def compile(self):
            return self

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
            await cli._run(task, Configuration(product_dir=tmp_path), memory_ports())
    finally:
        telemetry.configure()
    (span,) = exporter.get_finished_spans()
    assert span.name == "T-9" and span.attributes["task.status"] == "crashed"
    assert span.attributes["task.cost_usd"] == 0.0
