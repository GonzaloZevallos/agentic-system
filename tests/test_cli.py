import json
import os
import subprocess
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import cli
from agentic.core.process.adapters.github import inbox
from agentic.core.process.application.ports import LockHeld, LockRecord


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
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    capture_run(monkeypatch)
    monkeypatch.setattr("sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root)])
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
        if self.held is not None:
            raise LockHeld(self.held, f"{task_id} is already running: pid {self.held.pid}")
        self.held = LockRecord(os.getpid(), "h", "2026-09-21T00:00:00Z", command)
        return self.taken_over

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
