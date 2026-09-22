import json
import os
import signal
import socket
import subprocess
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import cli
from agentic.core.measurement.domain import evaluation
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.github import inbox
from agentic.core.process.application.ports import LockHeld, LockRecord
from agentic.core.process.domain.task import PullRequest, PullRequestCommit
from tests.fakes import MemoryStack, MemoryTracker, memory_ports


@asynccontextmanager
async def in_memory(cfg):
    yield InMemorySaver()


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
        (work,) = args
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

    graph = build_graph(ports_for)
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


class MetricPoints:
    """Every data point `_invoke`'s two invocations exported, by metric name: a resume
    emitting the task's whole accumulated cost again, instead of only what it spent
    itself, is exactly the bug the request opens with (README.md, 14)."""

    def __init__(self):
        from opentelemetry.sdk.metrics.export import MetricReader

        class _Reader(MetricReader):
            def _receive_metrics(_self, metrics_data, timeout_millis=10_000, **kw):
                for rm in metrics_data.resource_metrics:
                    for sm in rm.scope_metrics:
                        for metric in sm.metrics:
                            self.points.setdefault(metric.name, []).extend(metric.data.data_points)

            def shutdown(_self, timeout_millis=30_000, **kw):
                pass

        self.points: dict[str, list] = {}
        self.reader = _Reader()


@pytest.mark.anyio
async def test_a_resume_emits_only_what_it_spent_never_the_tasks_whole_cost(
    monkeypatch, tmp_path, capsys
):
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
    monkeypatch.setattr(cli, "graph", build_graph(lambda cfg: ports))
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIC_TASK_MAX_BUDGET_USD", "0.7")
    cfg = Configuration.from_sources({}, os.environ)
    saver, task = Saver(), {"id": "GH-1", "title": "Export", "description": "CSV."}

    points = MetricPoints()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(points.reader))
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

    invocations = points.points["agentic.task.invocations"]
    assert [p.attributes["invocation.kind"] for p in invocations] == ["run", "resume"]
    assert invocations[0].attributes["task.status"] == "waiting_on_human"
    assert invocations[1].attributes["task.status"] == "ready_for_review"

    # One pause, by the task cap's class (`agentic.task.human_decisions` is covered at
    # its own seam, `test_telemetry.py`, where a decision is easy to script directly).
    (paused,) = points.points["agentic.task.paused"]
    assert paused.attributes["escalation.class"] == "cap"

    run_cost, resume_cost = (p.value for p in points.points["agentic.invocation.cost_usd"])
    total_cost = points.points["agentic.task.cost_usd"][-1].value
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


def test_an_over_cap_brief_refuses_a_run_by_word_count(monkeypatch, tmp_path, capsys):
    over_cap = VALID_BRIEF.replace(
        "Gym owners cannot tell who is about to churn.",
        " ".join(f"word{i}" for i in range(600)),
    )
    root = product(tmp_path, "p")
    (root / "BRIEF.md").write_text(over_cap)
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 2
    assert "words (cap 500)" in capsys.readouterr().err


class FakeLock:
    """A `RunLock` double: no real process, no real file. `held` is what a live process
    already wrote, if any; `taken_over` is what `acquire` hands back for a dead one."""

    def __init__(self, held: LockRecord | None = None, taken_over: LockRecord | None = None):
        self.held = held
        self.taken_over = taken_over
        self.acquired: list[tuple[str, str]] = []
        self.released: list[str] = []

    def read(self, task_id):
        return self.held

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
async def test_a_run_acquires_the_lock_for_this_task_and_releases_it_even_if_the_graph_breaks(
    monkeypatch, tmp_path
):
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    class Broken(Delivers):
        async def ainvoke(self, task, config):
            raise RuntimeError("boom")

    monkeypatch.setattr(cli, "graph", Broken())
    lock = FakeLock()
    task = {"id": "T-9", "title": "t", "description": "d"}
    with pytest.raises(RuntimeError, match="boom"):
        await cli._run(
            task,
            Configuration(product_dir=tmp_path),
            memory_ports(),
            checkpointer=in_memory,
            run_lock=lock,
        )
    assert lock.acquired == [("T-9", " ".join(__import__("sys").argv))]
    assert lock.released == ["T-9"]


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
    assert ports.tracker.comments == [("GH-1", "Resumed: diagnose, under a task cap of 30.00 USD.")]


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


def close_pull_request(*, state="MERGED", merged=True, human_commit=False):
    commits = [
        PullRequestCommit(
            author_emails=("agentic-system@users.noreply.github.com",),
            message_headline="feat: x",
            additions=10,
            deletions=2,
        )
    ]
    if human_commit:
        commits.append(
            PullRequestCommit(
                author_emails=("human@x.com",),
                message_headline="fix typo",
                additions=3,
                deletions=1,
            )
        )
    return PullRequest(
        state=state,
        created_at="2026-09-01T00:00:00+00:00",
        merged_at="2026-09-01T01:00:00+00:00" if merged else None,
        commits=tuple(commits),
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


def test_close_refuses_with_no_document(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    points = MetricPoints()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(points.reader))
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "GH-9 has no document" in capsys.readouterr().err
    assert points.points == {}
    assert not ports.documents.path_for("GH-9").exists()


def test_close_refuses_with_no_pull_request_value(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker())
    ports.documents.save(close_task(pull_request=None))
    before = ports.documents.path_for("GH-9").read_bytes()
    points = MetricPoints()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(points.reader))
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "GH-9 has no pull request" in capsys.readouterr().err
    assert points.points == {}
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
            "human_commits": "0",
            "human_lines_changed": "0",
        },
    )
    before = ports.documents.path_for("GH-9").read_bytes()
    points = MetricPoints()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(points.reader))
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "already closed on 2026-09-20T00:00:00.000Z" in capsys.readouterr().err
    assert points.points == {}
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
    points = MetricPoints()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(points.reader))
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    error = capsys.readouterr().err
    assert "still open" in error and "nothing to close" in error
    assert points.points == {}
    assert ports.documents.path_for("GH-9").read_bytes() == before


def test_close_refuses_with_no_metrics_endpoint(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request()})
    ports = close_ports(root, tracker)
    ports.documents.save(close_task(pull_request=reference))
    before = ports.documents.path_for("GH-9").read_bytes()
    telemetry.configure()  # no OTEL_EXPORTER_OTLP_ENDPOINT: `configure()` builds nothing
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "no metrics endpoint configured; nothing recorded" in capsys.readouterr().err
    assert ports.documents.path_for("GH-9").read_bytes() == before


def test_close_refuses_when_the_product_has_no_name(monkeypatch, tmp_path, capsys):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    ports = close_ports(root, FakePullRequestTracker(), product=None)
    points = MetricPoints()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(points.reader))
    try:
        _closed_run(monkeypatch, root, ports)
        with pytest.raises(SystemExit) as info:
            cli.main()
    finally:
        telemetry.configure()
    assert info.value.code == 2
    assert "expected `product`" in capsys.readouterr().err
    assert points.points == {}


def test_close_happy_path_emits_one_sample_per_family_and_writes_the_four_keys(
    monkeypatch, tmp_path, capsys
):
    from agentic.core.measurement.adapters.otel import telemetry

    root = close_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request(human_commit=True)})
    ports = close_ports(root, tracker)
    FilesystemTaskDocuments(root, system_version=lambda: "v123").save(
        close_task(pull_request=reference)
    )
    points = MetricPoints()
    telemetry.configure(meter_provider=telemetry.meter_provider_for(points.reader))
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

    for name in (
        "agentic.delivery.outcome",
        "agentic.delivery.human_commits",
        "agentic.delivery.human_lines_changed",
        "agentic.delivery.time_to_merge",
    ):
        (point,) = points.points[name]
        assert point.attributes["product"] == "p"
        assert point.attributes["task.type"] == "bug"
        assert point.attributes["system.version"] == "v123"
    outcome_point = points.points["agentic.delivery.outcome"][0]
    assert outcome_point.attributes["outcome"] == "merged_with_changes"

    doc = ports.documents.load("GH-9")
    assert doc["delivery_outcome"] == "merged_with_changes"
    assert doc["human_commits"] == "1"
    assert doc["human_lines_changed"] == "4"
    assert doc["closed_at"]
    assert doc["status"] == "ready_for_review"  # no new `Status` value is added


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
    assert seen["args"] == (root, out) or seen["args"][-1] == out


@pytest.mark.anyio
async def test_eval_run_refuses_when_the_declaration_file_is_missing(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path, eval_set=None)
    with pytest.raises(cli.CannotStart, match="eval-set.yaml"):
        await cli._eval_run(
            Configuration(product_dir=root), memory_ports(), None, 200.0, True, False
        )


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
async def test_eval_run_refuses_when_a_set_tasks_pull_request_is_still_open(tmp_path):
    from agentic.shared.configuration import Configuration

    root = eval_root(tmp_path)
    reference = "https://github.com/o/r/pull/7"
    tracker = FakePullRequestTracker({reference: close_pull_request(state="OPEN", merged=False)})
    ports = memory_ports(tracker=tracker)
    ports.documents.save({"id": "GH-1", "pull_request": reference})
    launch = FakeLaunch()
    with pytest.raises(cli.CannotStart, match="still open"):
        await cli._eval_run(
            Configuration(product_dir=root), ports, None, 200.0, True, False, launch=launch
        )
    assert launch.calls == []


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
        launch.calls.append(argv)
        issue = argv[-1]
        ports.documents.save({"id": f"GH-{issue}", "status": "ready_for_review", "cost_usd": 40.0})

    launch.calls = []
    await cli._eval_run(
        Configuration(product_dir=root), ports, None, 200.0, True, False, launch=launch
    )
    assert launch.calls == [
        [cli.sys.argv[0], "run", "--fresh", "--issue", "1"],
        [cli.sys.argv[0], "run", "--fresh", "--issue", "2"],
    ]


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
