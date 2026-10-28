"""Two-tier verification through the compiled task graph, with fake ports (GH-358): a tiered
product's deliveries run only the fast checks, the whole suite runs once on the integrated
head before the push, a red full run loops back to a green one, a state with no stamp is
healed through `integrate`, and a product with no tiers never visits the node."""

from dataclasses import replace
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import composition
from agentic.composition import default_ports
from agentic.core.process.adapters.langgraph.graph import build_graph
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.ports import CommandResult
from agentic.core.process.domain import rules
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
    consolidation,
    delivered,
    design_draft,
    design_findings,
    final_validation,
    reviewed,
    verdict,
)

pytestmark = pytest.mark.anyio

CHECKS = {"tests": "pytest", "lint": "ruff", "e2e": "playwright"}
FAST = ("tests", "lint")


class _Rebased(_Rooted):
    """`_Rooted` writes in the temporary product; the checkout the graph names is a memory
    path, so the files `consolidate` wrote are reported under that checkout, as a real one
    would."""

    def consolidate(self, verdict, root=None):
        written = super().consolidate(verdict, root)
        return [root / path.relative_to(self.root) for path in written] if root else written


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


def build(script, stack, commands, fakes=None, workspace=None):
    fakes = fakes or Fakes()
    fakes.workspace = workspace or SteadyWorkspace()
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
            knowledge=_Rebased(knowledge, cfg.product_dir),
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


class DocsWorkspace(SteadyWorkspace):
    """`commit_paths` is a commit like the Developer's: it advances the head, so the
    close-time capability commit is a new head on top of the reviewed one."""

    def commit_paths(self, path, relatives, message):
        super().commit_paths(path, relatives, message)
        self.commit(path)
        return self.head(path)


CAPABILITY = {
    "capability": "dashboard",
    "content": "## Jobs\n\nCuando entreno, quiero ver mi semana.",
}


def ui_script(definitions):
    return script_for(
        pm=[verdict("approved", flags={"touches_ui": True, "touches_copy": False})],
        ux_designer=[design_draft()],
        ux_lead=[design_findings("approved")],
        ui_designer=[design_draft()],
        ui_lead=[design_findings("approved")],
        pm_consolidator=[consolidation("approved")],
        ux_lead_final=[design_findings("approved")],
        ui_lead_final=[design_findings("approved")],
        pm_final=[final_validation("approved", definitions=definitions)],
        **technical(),
    )


async def test_pm_final_definitions_move_the_head_and_the_suite_runs_once_more_on_it(config):
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    graph, fakes, script = build(
        ui_script([CAPABILITY]), stack, commands, workspace=DocsWorkspace()
    )
    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "ready_for_review"
    docs = final["close_docs"]
    assert docs["paths"] == ["docs/product/dashboard.md"]
    assert rules.same_commit(docs["parent"], final["subtasks"]["api"]["reviewed_head"])
    # The fan-in stamped the documentation head, and the suite covered it.
    assert final["task_head"] == docs["head"] != docs["parent"]
    assert final["full_checks"]["passed"] and final["full_checks"]["head"] == docs["head"]
    assert commands_run(commands).count("playwright") == 2  # once per head, no heal
    assert "full_checks_healed" not in final
    assert script.slugs.count("pm_final") == 1
    assert rules.approved_head(final) == docs["head"]
    assert fakes.workspace.events.count("push") == 1


async def test_pm_final_definitions_of_a_product_with_no_tiers_go_straight_to_delivery(config):
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS)
    graph, fakes, script = build(
        ui_script([CAPABILITY]), stack, commands, workspace=DocsWorkspace()
    )
    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "ready_for_review"
    docs = final["close_docs"]
    assert docs["paths"] == ["docs/product/dashboard.md"]
    for key in ("task_head", "full_checks", "full_checks_healed"):
        assert key not in final
    assert commands_run(commands) == ["pytest", "ruff", "playwright"]  # the one gate, no suite
    assert rules.approved_head(final) == docs["head"]
    assert fakes.workspace.events.count("push") == 1


async def test_a_pm_final_with_no_definitions_leaves_the_head_and_the_record_alone(config):
    commands = MemoryCommands()
    stack = MemoryStack(checks=CHECKS, delivery_checks=FAST)
    graph, fakes, script = build(ui_script([]), stack, commands, workspace=DocsWorkspace())
    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "ready_for_review"
    assert not final.get("close_docs")
    assert commands_run(commands).count("playwright") == 1
    assert rules.approved_head(final) == final["task_head"]
