"""The path end to end through the compiled graph: routing, loops, fan-out,
escalation, with a scripted runner, the real filesystem adapters in a temporary
product, and git and GitHub in memory."""

from dataclasses import replace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentic import composition
from agentic.composition import default_ports
from agentic.core.measurement.adapters.otel import telemetry
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.langgraph.graph import build_graph, configuration_from
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.domain.roles import PAIR_PM_PA, PJM, PM, PRODUCT_ANALYST
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN
from agentic.shared.configuration import Configuration
from tests.fakes import (
    REPORTS,
    MemoryKnowledge,
    MemoryTracker,
    MemoryWorkspace,
    ScriptedRunner,
    answers,
    classified,
    defined,
    delivered,
    planned,
    questions,
    reviewed,
    verdict,
)

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-1",
    "title": "Export report",
    "description": "Users want CSV.\n\n## Why\n\nAudits.",
}
DEFINITION = "Interview: Product Analyst→PM"
SOLUTION = "Interview: Tech Lead efímero→Tech Lead"


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


def script_for(**queues) -> ScriptedRunner:
    """A runner with the definition stage scripted unless overridden: PjM classifies,
    the Product Analyst asks one round and closes, PM answers, then the rest."""
    defaults = dict(
        pjm=[classified()],
        product_analyst_interviewer=[questions(1), questions(done=True)],
        pm_interviewee=[answers(1)],
        product_analyst=[defined()],
        pm=[verdict("approved")],
    )
    return ScriptedRunner(**{**defaults, **queues})


def technical(**queues) -> dict:
    """The technical stage scripted for one subtask, `api`, approved first time."""
    defaults = dict(
        tech_lead_ephemeral=[questions(done=True)],
        tech_lead=[planned()],
        developer=[delivered()],
        tech_lead_review=[reviewed("approved")],
    )
    return {**defaults, **queues}


class Fakes:
    def __init__(self):
        self.workspace = MemoryWorkspace()
        self.tracker = MemoryTracker()


async def run_graph(script: ScriptedRunner, config, telemetry=NoTelemetry(), fakes=None):
    """The real ports over the temporary product, with the scripted runner in place
    of the SDK and git and GitHub in memory. `default_ports` is what the exported
    graph uses."""
    fakes = fakes or Fakes()
    # Every checkout "moved": Developers commit. The knowledge is read and written in
    # the memory worktree paths, which do not exist: point them at the product.
    fakes.workspace.moved.update({MemoryWorkspace.ROOT / "GH-1", MemoryWorkspace.ROOT / "GH-1.api"})
    fakes.workspace.moved.update({MemoryWorkspace.ROOT / "GH-1.domain"})

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
        )

    return await build_graph(ports_for, telemetry).compile().ainvoke(TASK, config=config)


class _Rooted:
    """The filesystem knowledge, ignoring the memory checkout paths: everything reads
    and writes in the temporary product."""

    def __init__(self, inner, root):
        self.inner, self.root = inner, root

    def read(self, root=None):
        return self.inner.read(self.root)

    def standards(self, root=None):
        return self.inner.standards(self.root)

    def approval_check(self, task_type):
        return self.inner.approval_check(task_type)

    def clarification_check(self):
        return self.inner.clarification_check()

    def distill(self, verdict, root=None):
        return self.inner.distill(verdict, self.root)


async def test_definition_stage_then_delivery(config, tmp_path):
    fakes = Fakes()
    script = script_for(**technical())
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == "ready_for_review"
    assert script.slugs == [
        "pjm",
        "product_analyst_interviewer",
        "pm_interviewee",
        "product_analyst_interviewer",
        "product_analyst",
        "pm",
        "tech_lead_ephemeral",
        "tech_lead",
        "developer",
        "tech_lead_review",
    ]
    assert list(final["sections"]) == [
        PJM.name,
        DEFINITION,
        PRODUCT_ANALYST.name,
        PM.name,
        SOLUTION,
        "Tech Lead",
        "Developer: api",
        "Tech Lead: review api",
    ]
    assert final["flags"] == {"touches_ui": True, "touches_copy": False}  # PM replaces PA's set
    assert final["failures"] == []
    assert final["branch"] == "agentic/GH-1" and final["pull_request"].endswith("/pull/7")
    # A single subtask builds on the task branch itself (ADR-0011).
    assert final["subtasks"]["api"]["branch"] == "agentic/GH-1"
    assert final["subtasks"]["api"]["status"] == "approved" and INTEGRATION not in final["subtasks"]
    assert fakes.workspace.pushed == ["agentic/GH-1"] and fakes.workspace.cleaned == ["GH-1"]
    assert [m for _, m in fakes.workspace.commits] == [
        "GH-1: definition interview",
        "GH-1: product definition",
        "GH-1: solution interview",
    ]
    assert fakes.tracker.labels == [] and len(fakes.tracker.comments) == 1

    pm_input = script.calls[5]["prompt"]
    assert "## Original request" in pm_input and "Users want CSV." in pm_input
    assert f"## {DEFINITION}\n\n### Round 1: questions" in pm_input  # PM checks fidelity
    assert "## Product Analyst\n\n### Behaviour\n\nv1" in pm_input  # the full section
    assert "## PjM" not in pm_input  # PM does not declare it
    assert "You have 2 left" in script.calls[5]["system_prompt"]
    assert script.calls[0]["cwd"] == tmp_path  # PjM runs in the product dir
    assert script.calls[1]["cwd"] == MemoryWorkspace.ROOT / "GH-1"  # the rest, in the task checkout

    doc = FilesystemTaskDocuments(tmp_path).load("GH-1")
    assert doc["status"] == "ready_for_review"
    assert doc["sections"][PM.name].startswith("### Verdict")
    assert doc["pull_request"] == final["pull_request"]


async def test_two_subtasks_fan_out_loop_on_a_return_and_integrate(config):
    fakes = Fakes()
    plan = planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})
    script = script_for(
        **technical(
            tech_lead=[plan],
            developer=[delivered("api"), delivered("domain"), delivered("domain again")],
            developer_integrator=[delivered("merged")],
            tech_lead_review=[
                reviewed("approved"),
                reviewed("returned", "no test at the seam"),
                reviewed("approved"),
                reviewed("approved"),
            ],
        )
    )
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == "ready_for_review"
    slugs = script.slugs[8:]
    assert slugs[:2] == ["developer", "developer"]  # both subtasks in one superstep
    assert slugs[2:] == [
        "tech_lead_review",
        "tech_lead_review",
        "developer",  # only `domain` comes back
        "tech_lead_review",
        "developer_integrator",
        "tech_lead_review",
    ]
    subtasks = final["subtasks"]
    assert {s["status"] for s in subtasks.values()} == {"approved"}
    assert subtasks["api"]["branch"] == "agentic/GH-1.api"
    assert subtasks[INTEGRATION]["branch"] == "agentic/GH-1"
    assert final["returns"] == {"Tech Lead→Developer:domain": 1}
    assert "Developer: domain (v1)" in final["sections"]
    assert "Tech Lead: review domain (v1)" in final["sections"]
    rerun = script.calls[8 + 4]
    assert rerun["cwd"] == MemoryWorkspace.ROOT / "GH-1.domain"
    assert "## Tech Lead: review domain\n\n### Verdict\n\nreturned" in rerun["prompt"]
    assert "## Developer: api" not in rerun["prompt"]  # not its subtask
    integrator = script.calls[-2]
    assert "`agentic/GH-1.api` (api: API)" in integrator["system_prompt"]
    assert "`agentic/GH-1.domain` (domain: Domain)" in integrator["system_prompt"]


async def test_approval_writes_the_product_knowledge(config, tmp_path):
    (tmp_path / "CONTEXT.md").write_text("# Product\n\n## Language\n\n**Gym**:\nA business.\n")
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/members.md").write_text("# Members\n\nOld.\n")
    script = script_for(
        pm=[
            verdict(
                "approved",
                definitions=[{"capability": "reports", "content": REPORTS}],
                glossary=[{"term": "Report", "definition": "A CSV file.", "avoid": "export"}],
            )
        ],
        **technical(),
    )
    final = await run_graph(script, config)
    assert final["status"] == "ready_for_review"

    # Product roles read the knowledge, whole, before the task document; PjM does not.
    assert "## Product knowledge" not in script.calls[0]["prompt"]
    # The request's own headings never look like sections, in context or in the file.
    assert "### Why" in script.calls[0]["prompt"] and "\n## Why" not in script.calls[0]["prompt"]
    assert "\n## Why" not in (tmp_path / ".agentic/tasks/GH-1.md").read_text()
    for call in script.calls[1:6]:
        if call["resume"]:  # a resumed session already holds the context
            continue
        assert "## Product knowledge" in call["prompt"]
        assert "A business." in call["prompt"] and "Old." in call["prompt"]
        assert "## Original request" in call["prompt"]
    # The roles that build read the standards first, then the knowledge.
    assert script.calls[6]["prompt"].startswith("## Engineering standards")
    assert "## Product knowledge" in script.calls[6]["prompt"]

    assert (tmp_path / "docs/product/reports.md").read_text() == REPORTS + "\n"
    assert (tmp_path / "docs/product/members.md").read_text() == "# Members\n\nOld.\n"
    glossary = (tmp_path / "CONTEXT.md").read_text()
    assert "**Gym**:\nA business." in glossary
    assert glossary.rstrip().endswith("**Report**:\nA CSV file.\n_Avoid_: export")


async def test_return_writes_nothing_and_escalation_reaches_the_issue(config, tmp_path):
    # No returns allowed: the first one escalates, and the knowledge stays untouched.
    config["configurable"]["max_returns_per_pair"] = 0
    fakes = Fakes()
    script = script_for(pm=[verdict("returned", "gap")])
    final = await run_graph(script, config, fakes=fakes)
    assert final["status"] == WAITING_ON_HUMAN and "hit 0 returns: gap" in final["escalated"]
    assert not (tmp_path / "docs/product").exists()
    assert not (tmp_path / "CONTEXT.md").exists()
    assert fakes.tracker.labels == [("GH-1", "needs-human")]
    assert fakes.tracker.comments[0][1].startswith("Escalated: PM→Product Analyst hit 0 returns")


async def test_questions_for_the_human_escalate_with_the_list(config):
    fakes = Fakes()
    script = script_for(pm_interviewee=[answers(1, for_human=["Which currency?"])])
    final = await run_graph(script, config, fakes=fakes)
    assert final["status"] == WAITING_ON_HUMAN
    assert final["questions_for_human"] == ["Which currency?"]
    assert script.slugs[-1] == "product_analyst_interviewer"  # the interview ran to its end
    assert "1. Which currency?" in fakes.tracker.comments[0][1]


async def test_approving_a_feature_without_definitions_is_retried_then_escalated(config, tmp_path):
    empty = verdict("approved", definitions=[])
    script = script_for(pm=[empty, empty])
    final = await run_graph(script, config)
    assert final["status"] == WAITING_ON_HUMAN
    assert "without `definitions`" in final["escalated"]
    assert "without `definitions`" in script.calls[6]["prompt"]  # the error went back to PM
    assert not (tmp_path / "docs/product").exists()


async def test_models_follow_kind(config):
    script = script_for(**technical())
    await run_graph(script, config)
    models = {c["slug"]: c["model"] for c in script.calls}
    assert models["pjm"] == models["product_analyst"] == "claude-sonnet-5"
    assert models["product_analyst_interviewer"] == models["developer"] == "claude-sonnet-5"
    assert models["pm"] == models["pm_interviewee"] == "claude-opus-5"
    assert models["tech_lead_ephemeral"] == models["tech_lead"] == "claude-opus-5"
    assert models["tech_lead_review"] == "claude-opus-5"


async def test_low_confidence_escalates_before_defining(config):
    script = ScriptedRunner(pjm=[classified(confidence="low")])
    final = await run_graph(script, config)
    assert final["status"] == WAITING_ON_HUMAN
    assert "PjM could not classify" in final["escalated"]
    assert len(script.calls) == 1


async def test_non_feature_escalates(config):
    script = ScriptedRunner(pjm=[classified(type_="bug")])
    final = await run_graph(script, config)
    assert final["type"] == "bug"
    assert final["status"] == WAITING_ON_HUMAN
    assert final["escalated"] == "bug path not implemented yet"
    assert len(script.calls) == 1


async def test_return_loops_back_then_approves(config):
    script = script_for(
        product_analyst=[defined(1), defined(2)],
        pm=[verdict("returned", "missing empty state"), verdict("approved")],
        **technical(),
    )
    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    assert final["returns"] == {PAIR_PM_PA.key: 1}
    assert script.slugs[4:8] == ["product_analyst", "pm", "product_analyst", "pm"]
    second_define = script.calls[6]
    assert second_define["resume"] is None  # the interview is not repeated
    assert (
        "## Product Analyst\n\n### Behaviour\n\nv1" in second_define["prompt"]
    )  # its own previous
    assert "## PM\n\n### Verdict\n\nreturned" in second_define["prompt"]  # PM's full section
    assert "missing empty state" in second_define["prompt"]
    second_validate = script.calls[7]["prompt"]
    assert "v2" in second_validate and "### Verdict\n\nreturned" in second_validate
    assert "You have 1 left" in script.calls[7]["system_prompt"]
    # The current section holds the latest delivery; the returned one stays as history.
    assert "v2" in final["sections"][PRODUCT_ANALYST.name]
    assert "v1" in final["sections"][f"{PRODUCT_ANALYST.name} (v1)"]
    assert "returned" in final["sections"][f"{PM.name} (v1)"]
    assert [(r["section"], r["version"]) for r in final["runs"][:8]] == [
        ("PjM", 1),
        (DEFINITION, 1),
        (DEFINITION, 2),
        (DEFINITION, 3),
        ("Product Analyst", 1),
        ("PM", 1),
        ("Product Analyst", 2),
        ("PM", 2),
    ]
    # History is not context: the second PM pass reads only current sections.
    assert "(v1)" not in second_validate


async def test_return_limit_escalates(config):
    script = script_for(
        product_analyst=[defined(1), defined(2), defined(3)],
        pm=[verdict("returned"), verdict("returned"), verdict("returned", "still no metrics")],
    )
    final = await run_graph(script, config)

    assert final["status"] == WAITING_ON_HUMAN
    assert final["returns"] == {PAIR_PM_PA.key: 2}
    assert "hit 2 returns: still no metrics" in final["escalated"]
    assert "You have 0 left" in script.calls[-1]["system_prompt"]
    assert len(script.calls) == 10


async def test_role_failure_escalates_and_keeps_failures(config, tmp_path):
    script = script_for(
        product_analyst=[
            AttemptFailed("no structured output", 0.05),
            AttemptFailed("empty markdown", 0.03),
        ],
    )
    final = await run_graph(script, config)

    assert final["status"] == WAITING_ON_HUMAN
    assert final["escalated"].startswith("Product Analyst failed 2 times")
    assert [f["error"] for f in final["failures"]] == ["no structured output", "empty markdown"]
    assert final["cost_usd"] == pytest.approx(0.01 + 0.05 * 2 + 0.1 + 0.05 + 0.03)
    assert PRODUCT_ANALYST.name not in final["sections"]
    doc = FilesystemTaskDocuments(tmp_path).load("GH-1")
    assert [f["error"] for f in doc["failures"]] == ["no structured output", "empty markdown"]
    assert doc["runs"][0] == {
        "role": "PjM",
        "section": "PjM",
        "version": 1,
        "session_id": "",
        "model": "claude-sonnet-5",
        "cost_usd": 0.01,
        "cost_by_model": {},
        "turns": 0,
    }
    assert doc["status"] == WAITING_ON_HUMAN


async def test_retried_failure_is_recorded_but_not_escalated(config):
    script = script_for(product_analyst=[AttemptFailed("invalid", 0.02), defined()], **technical())
    final = await run_graph(script, config)
    assert final["status"] == "ready_for_review"
    assert final["failures"] == [
        {
            "role": "Product Analyst",
            "attempt": 1,
            "error": "invalid",
            "cost_usd": 0.02,
            "session_id": "",
        }
    ]


async def test_each_node_opens_and_finishes_the_roles_span(config):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    try:
        script = script_for(
            product_analyst=[defined(1), defined(2)],
            pm=[verdict("returned", "gap"), verdict("approved")],
            **technical(),
        )
        with telemetry.task_span("GH-1", "t") as run:
            run.finish(await run_graph(script, config, telemetry))
    finally:
        telemetry.configure()
    finished = exporter.get_finished_spans()
    assert [s.name for s in finished] == [
        "PjM",
        "Product Analyst",
        "PM",
        "Product Analyst",
        "PM",
        "Tech Lead",
        "Developer",
        "Tech Lead",
        "GH-1",
    ]
    task = finished[-1]
    assert all(s.parent.span_id == task.get_span_context().span_id for s in finished[:-1])
    # The role spans carry what each use case decided; the task span, the totals.
    outcomes = [s.attributes["role.outcome"] for s in finished[:-1]]
    assert outcomes == ["delivered"] * 2 + ["returned"] + ["delivered"] * 5
    assert finished[3].attributes["role.version"] == 2 and finished[3].attributes["role.rework"]
    assert finished[6].attributes["role.model"] == "claude-sonnet-5"
    assert task.attributes["task.status"] == "ready_for_review"
    assert task.attributes["task.returns"] == 1


def test_configuration_comes_from_configurable_and_env(monkeypatch, tmp_path):
    cfg = configuration_from({"configurable": {"product_dir": str(tmp_path), "verbose": True}})
    assert cfg.product_dir == tmp_path and cfg.verbose is True
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", "/elsewhere")
    assert str(
        configuration_from({"configurable": {"product_dir": str(tmp_path)}}).product_dir
    ) == ("/elsewhere")


def test_exported_graph_uses_the_default_ports():
    assert set(composition.graph.nodes) == {
        "classify",
        "define",
        "validate",
        "plan",
        "develop",
        "review",
        "deliver",
        "escalate",
    }


async def test_composition_wires_the_real_telemetry(config, tmp_path):
    """The exported graph and the SDK runner get `core.measurement`'s telemetry, not
    the `NoTelemetry` default: a run through `composition.graph` produces role spans."""
    assert default_ports(Configuration(product_dir=tmp_path)).runner.telemetry is telemetry
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    try:
        # The nodes call `default_ports` for real; only the runner and the tracker are
        # swapped, via the module, because `composition.graph` is wired at import time.
        script = ScriptedRunner(pjm=[classified(confidence="low")])
        real = composition.ClaudeSdkRunner, composition.GhTracker
        composition.ClaudeSdkRunner = lambda **_: script
        composition.GhTracker = lambda *_: MemoryTracker()
        try:
            await composition.graph.compile().ainvoke(TASK, config=config)
        finally:
            composition.ClaudeSdkRunner, composition.GhTracker = real
    finally:
        telemetry.configure()
    assert [s.name for s in exporter.get_finished_spans()] == ["PjM"]


async def test_a_crash_in_a_node_still_finishes_the_roles_span(config):
    """An exception outside the use case's own failure handling (here: the knowledge
    cannot be written) propagates, but the span closes with an outcome and cost."""

    class BrokenKnowledge(MemoryKnowledge):
        def distill(self, verdict, root=None):
            raise OSError("read-only checkout")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    try:
        script = script_for()

        def ports_for(cfg):
            return replace(
                default_ports(cfg),
                runner=script,
                knowledge=BrokenKnowledge(),
                workspace=MemoryWorkspace(),
                tracker=MemoryTracker(),
            )

        with pytest.raises(OSError, match="read-only"):
            await build_graph(ports_for, telemetry).compile().ainvoke(TASK, config=config)
    finally:
        telemetry.configure()
    pm = exporter.get_finished_spans()[-1]
    assert pm.name == "PM"
    assert pm.attributes["role.outcome"] == "failed" and pm.attributes["role.cost_usd"] == 0.0
    assert pm.status.status_code.name == "ERROR"  # the exception is on the span too
