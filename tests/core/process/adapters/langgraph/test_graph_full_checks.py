"""Two-tier verification through the compiled task graph, with fake ports (GH-358): a tiered
product's deliveries run only the fast checks, the whole suite runs once on the integrated
head before the push, a red full run loops back to a green one, a state with no stamp is
healed through `integrate`, and a product with no tiers never visits the node."""

from dataclasses import replace
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentic import composition
from agentic.composition import default_ports
from agentic.core.process.adapters.langgraph.graph import build_graph
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.ports import CommandResult
from agentic.core.process.domain.roles import DEVELOPER, prompt_hash
from tests.core.process.adapters.langgraph.test_graph import (
    TASK,
    Fakes,
    MemoryWorkspace,
    _Rooted,
    script_for,
    technical,
)
from tests.fakes import (
    MemoryCommands,
    MemorySampleApp,
    MemoryStack,
    ScriptedRunner,
    delivered,
    reviewed,
)

pytestmark = pytest.mark.anyio

CHECKS = {"tests": "pytest", "lint": "ruff", "e2e": "playwright"}
FAST = ("tests", "lint")


class SteadyWorkspace(MemoryWorkspace):
    """Heads that only a commit advances, as a real checkout's: the base class advances on
    every look, which a record of one head could never match."""

    def __init__(self) -> None:
        super().__init__()
        self.delivered_commits: dict[Path, int] = {}

    def commit(self, path: Path) -> None:
        self.delivered_commits[path] = self.delivered_commits.get(path, 0) + 1

    def head(self, path: Path) -> str:
        return f"head{self.delivered_commits[path]}" if self.delivered_commits.get(path) else "base"

    def full_head(self, path: Path) -> str:
        return self.head(path)


class CommittingRunner(ScriptedRunner):
    """A Developer's delivery is a commit on its checkout, made before the gate looks."""

    def __init__(self, workspace: SteadyWorkspace, script: ScriptedRunner) -> None:
        super().__init__()
        self.queues, self.workspace = script.queues, workspace

    async def run_once(self, role, prompt, **kwargs):
        if role.slug.startswith("developer"):
            self.workspace.commit(kwargs["cwd"])
        return await super().run_once(role, prompt, **kwargs)


@pytest.fixture
def config(tmp_path):
    return {
        "configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"},
        "recursion_limit": 40,
    }


def build(script, stack, commands, fakes=None):
    fakes = fakes or Fakes()
    fakes.workspace = SteadyWorkspace()
    script = CommittingRunner(fakes.workspace, script)

    def ports_for(cfg):
        real = default_ports(cfg)
        knowledge = real.knowledge
        knowledge.product_dir = cfg.product_dir
        return replace(
            real,
            runner=script,
            workspace=fakes.workspace,
            tracker=fakes.tracker,
            knowledge=_Rooted(knowledge, cfg.product_dir),
            stack=stack,
            commands=commands,
            sample_app=MemorySampleApp(),
            check_results=None,  # these tests pin the tiers, not the reuse of a pass
        )

    graph = build_graph(ports_for, NoTelemetry(), arm_for=composition._arm_for)
    return graph.compile(checkpointer=InMemorySaver()), fakes, script


def commands_run(commands) -> list[str]:
    return [command for _, command in commands.ran]


async def test_a_tiered_delivery_runs_the_fast_tier_then_the_full_suite_once_before_the_push(
    config,
):
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    script = script_for(**technical())
    graph, fakes, script = build(script, stack, commands)
    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "ready_for_review"
    # The gate of the one delivery ran the fast tier alone; then the node ran every check.
    assert commands_run(commands) == ["pytest", "ruff", "pytest", "ruff", "playwright"]
    assert final["full_checks"]["passed"] is True
    assert final["full_checks"]["head"] == final["task_head"]
    assert list(final["full_checks"]["declared"]) == list(CHECKS)
    # The node left the checkout as its head holds it, and the push came after it.
    assert fakes.workspace.restored == [MemoryWorkspace.ROOT / "GH-1"]
    assert "push" in fakes.workspace.events


async def test_a_red_full_run_reopens_loops_back_through_review_and_delivers_when_green(config):
    commands = MemoryCommands(playwright=[CommandResult(1, "e2e failed")] * 2)
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    script = script_for(
        **technical(
            developer=[delivered(), delivered()],
            tech_lead_review=[reviewed("approved"), reviewed("approved")],
        )
    )
    graph, fakes, script = build(script, stack, commands)
    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "ready_for_review"
    assert final["full_checks"]["version"] == 2 and final["full_checks"]["passed"] is True
    ran = commands_run(commands)
    # The suite ran twice, once per head; every delivery gate in between ran fast only.
    assert ran.count("playwright") == 3  # red, its rerun, then green on the next head
    assert ran[-3:] == ["pytest", "ruff", "playwright"]
    assert [c["slug"] for c in script.calls].count("developer") == 2
    assert [c["slug"] for c in script.calls].count("tech_lead_review") == 2
    assert "push" in fakes.workspace.events
    rows = [f for f in final["findings"] if f["location"].startswith("full_checks:")]
    assert [(r["location"], r["version"]) for r in rows] == [("full_checks:e2e", 1)]
    assert final["changes_requested"]


async def test_a_state_with_no_stamp_reaches_deliver_which_pushes_nothing_until_the_suite_ran(
    config,
):
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    graph, fakes, script = build(ScriptedRunner(), stack, commands)
    approved = {
        "id": "api",
        "title": "api",
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "approved",
        "branch": "agentic/GH-1.api",
        "base": "base",
    }
    seeded = {**TASK, "type": "feature", "subtasks": {"api": approved}, "branch": "agentic/GH-1"}
    # The fan-in has run, as if the document had been rebuilt with no `task_head` on it.
    await graph.aupdate_state(config, seeded, as_node="integrate")
    final = await graph.ainvoke(None, config=config)

    assert final["status"] == "ready_for_review"
    assert final["full_checks_healed"] == final["task_head"] == final["full_checks"]["head"]
    assert commands_run(commands) == ["pytest", "ruff", "playwright"]
    # One push, after the suite ran: the first `deliver` pushed nothing.
    assert fakes.workspace.events.count("push") == 1
    assert fakes.tracker.pull_requests and len(fakes.tracker.pull_requests) == 1


async def test_a_product_with_no_tiers_never_visits_the_node_and_writes_no_new_key(config):
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS)
    script = script_for(**technical())
    graph, fakes, script = build(script, stack, commands)
    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "ready_for_review"
    assert commands_run(commands) == ["pytest", "ruff", "playwright"]
    for key in ("task_head", "full_checks", "full_checks_healed"):
        assert key not in final
    assert "Full checks" not in final["sections"]
    assert fakes.workspace.restored == []
    developer = next(c for c in script.calls if c["slug"] == DEVELOPER.slug)
    assert "Delivery checks" not in developer["system_prompt"]
    raw, _ = default_ports(
        __import__("agentic.shared.configuration", fromlist=["x"]).Configuration(
            product_dir=Path(config["configurable"]["product_dir"])
        )
    ).prompts.raw(DEVELOPER)
    assert final["prompts"][DEVELOPER.slug] == prompt_hash(raw)


def increment_world(tmp_path):
    """GH-373's shape (GH-392): `api` approved, the integration approved at `APPROVED`, the task
    branch at `MOVED`, a descendant, and the run stopped by a role failure. Returns the document
    ports the resume seeds from and the configuration."""
    from agentic.core.process.domain import rules
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    api = {
        "id": "api",
        "title": "api",
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "approved",
        "branch": "agentic/GH-1.api",
        "base": "base",
        "reviewed_head": "1" * 40,
    }
    integration = {
        **api,
        "id": "integration",
        "branch": rules.task_branch("GH-1"),
        "reviewed_head": APPROVED,
        "depends_on": ["api"],
    }
    stopped = {
        **TASK,
        "type": "feature",
        "status": "waiting_on_human",
        "escalated": "Developer failed: no space left on device",
        "escalation_class": "failure",
        "branch": rules.task_branch("GH-1"),
        "subtasks": {"api": api, "integration": integration},
    }
    ports = memory_ports()
    ports.documents.save(stopped)
    ports.workspace.existing_branches.update(
        {rules.task_branch("GH-1"): (MOVED, "base"), "agentic/GH-1.api": ("1" * 40, "base")}
    )
    ports.workspace.ancestors.add(APPROVED)
    return ports, Configuration(product_dir=tmp_path)


APPROVED = "a" * 40
MOVED = "b" * 40


def watch_the_reviews(fakes, script, monkeypatch):
    """The integrated checkout answers `MOVED`, and the order of the review and the push is kept."""
    monkeypatch.setattr(fakes.workspace, "full_head", lambda path: MOVED)
    run_once = script.run_once

    async def spy(role, prompt, **kwargs):
        fakes.workspace.events.append(f"role:{role.slug}")
        return await run_once(role, prompt, **kwargs)

    monkeypatch.setattr(script, "run_once", spy)


def only_the_integration_review(script, fakes, final, commands):
    assert final["status"] == "ready_for_review"
    # Only the integration review ran: no Developer, no other role, and it was incremental.
    assert [c["slug"] for c in script.calls] == ["tech_lead_review"]
    review = script.calls[0]["system_prompt"]
    assert "This is an incremental review." in review
    assert f"from the commit `{APPROVED}`" in review
    assert final["subtasks"]["integration"]["status"] == "approved"
    # The full suite ran on the head the fan-in stamped, before the one push.
    assert final["task_head"] == MOVED
    assert final["full_checks"]["head"] == MOVED
    # The review covered the increment: the head it records is the moved one, over base H.
    assert final["subtasks"]["integration"]["reviewed_head"] == MOVED
    assert commands_run(commands) == ["pytest", "ruff", "playwright"]
    events = fakes.workspace.events
    assert events.count("push") == 1
    assert events.index("role:tech_lead_review") < events.index("push")  # nothing pushed before
    assert final["findings"] == []


async def test_an_increment_on_a_tiered_product_is_reviewed_checked_on_its_head_then_delivered(
    config, tmp_path, monkeypatch
):
    """GH-392: the resume sends only the integration back to an incremental review from the
    approved head; the fan-in stamps `task_head`, and the full suite runs before `deliver`."""
    from agentic.core.process.application import resume

    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    graph, fakes, script = build(
        script_for(tech_lead_review=[reviewed("approved")]), stack, commands
    )
    watch_the_reviews(fakes, script, monkeypatch)
    ports, cfg = increment_world(tmp_path)
    seed = resume.seed_increment("GH-1", cfg, ports)
    r = resume.prepare("GH-1", seed.state, "document", cfg, ports, increment=seed.increment)
    assert r.target == "integrate"

    await graph.aupdate_state(config, r.paused, as_node="escalate")
    final = await graph.ainvoke(Command(resume=r.answer()), config=config)

    only_the_integration_review(script, fakes, final, commands)


async def test_resume_from_the_document_reviews_the_increment_through_the_cli(
    monkeypatch, config, tmp_path
):
    """GH-373 through `agentic resume --from-document --yes`: the shape that was refused."""
    from types import SimpleNamespace

    from agentic import cli
    from tests.test_cli import FakeLock, in_memory

    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    app, fakes, script = build(script_for(tech_lead_review=[reviewed("approved")]), stack, commands)
    watch_the_reviews(fakes, script, monkeypatch)
    monkeypatch.setattr(cli, "graph", SimpleNamespace(compile=lambda checkpointer: app))
    monkeypatch.setattr(cli, "_configurable", lambda task_id, log_file: config["configurable"])
    ports, cfg = increment_world(tmp_path)

    await cli._resume(
        "GH-1",
        cfg,
        ports,
        from_document=True,
        yes=True,
        checkpointer=in_memory,
        run_lock=FakeLock(),
    )

    final = (await app.aget_state(config)).values
    only_the_integration_review(script, fakes, final, commands)
    assert f"{APPROVED}..{MOVED}" in ports.tracker.comments[0][1]
    assert ports.workspace.commits == []  # a clean (absent) checkout: nothing to commit


async def test_a_dry_run_of_an_increment_prints_the_plan_and_writes_nothing(
    monkeypatch, config, tmp_path, capsys
):
    """GH-392: `--dry-run` shows the increment and the commit it would make, and makes none:
    no commit, no comment, no label change, and the thread is not seeded."""
    from types import SimpleNamespace

    from agentic import cli
    from tests.test_cli import FakeLock, in_memory

    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    app, fakes, script = build(
        script_for(tech_lead_review=[reviewed("approved")]), stack, MemoryCommands()
    )
    monkeypatch.setattr(cli, "graph", SimpleNamespace(compile=lambda checkpointer: app))
    monkeypatch.setattr(cli, "_configurable", lambda task_id, log_file: config["configurable"])
    ports, cfg = increment_world(tmp_path)
    checkout = Path("/p/.agentic/worktrees/GH-1")
    ports.workspace.checkouts = [checkout]
    ports.workspace.dirty.add(checkout)

    await cli._resume(
        "GH-1",
        cfg,
        ports,
        from_document=True,
        dry_run=True,
        checkpointer=in_memory,
        run_lock=FakeLock(),
    )

    shown = capsys.readouterr().out
    assert f"reviewing the increment {APPROVED}..{MOVED}" in shown
    assert f"uncommitted files in `{checkout}` will be committed into the increment" in shown
    assert ports.workspace.commits == []
    assert ports.tracker.comments == [] and ports.tracker.unlabeled == []
    assert script.calls == [] and fakes.workspace.events == []
    assert ports.workspace.events == []  # the workspace the resume used was only read
    assert not (await app.aget_state(config)).values  # the thread was never seeded
