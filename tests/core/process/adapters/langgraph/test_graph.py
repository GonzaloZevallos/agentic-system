"""The path end to end through the compiled graph: routing, loops, fan-out,
escalation, with a scripted runner, the real filesystem adapters in a temporary
product, and git and GitHub in memory."""

from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentic import composition
from agentic.composition import default_ports
from agentic.core.measurement.adapters.otel import semconv, telemetry
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.langgraph.graph import build_graph, configuration_from
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application import resume
from agentic.core.process.application.ports import AttemptFailed
from agentic.core.process.application.preflight import UNKNOWN_PRODUCT
from agentic.core.process.domain.roles import FINDINGS, PAIR_PM_PA, PJM, PM, PRODUCT_ANALYST
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN
from agentic.shared.configuration import Configuration
from tests.conftest import BatchReader, points
from tests.fakes import (
    REPORTS,
    MemoryCommands,
    MemoryKnowledge,
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    ScriptedRunner,
    answers,
    classified,
    defined,
    delivered,
    diagnosed,
    finding,
    memory_ports,
    planned,
    questions,
    reviewed,
    ruled,
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
    return await compiled(script, telemetry, fakes).ainvoke(TASK, config=config)


def compiled(script: ScriptedRunner, telemetry=NoTelemetry(), fakes=None, saver=None, stack=None):
    """The graph with the real ports over the temporary product, the scripted runner in
    place of the SDK, git and GitHub in memory, and a checkpointer, as the CLI compiles
    it. `default_ports` is what the exported graph uses."""
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
            # The product is no repository and the checkouts are names: nothing to run.
            stack=stack or MemoryStack(),
            commands=MemoryCommands(),
        )

    return build_graph(ports_for, telemetry, arm_for=composition._arm_for).compile(
        checkpointer=saver or InMemorySaver()
    )


class _Rooted:
    """The filesystem knowledge, ignoring the memory checkout paths: everything reads
    and writes in the temporary product."""

    def __init__(self, inner, root):
        self.inner, self.root = inner, root

    def read(self, root=None):
        return self.inner.read(self.root)

    def standards(self, root=None):
        return self.inner.standards(self.root)

    def brief(self, root=None):
        return self.inner.brief(self.root)

    def approval_check(self, task_type):
        return self.inner.approval_check(task_type)

    def clarification_check(self):
        return self.inner.clarification_check()

    def consolidate(self, verdict, root=None):
        return self.inner.consolidate(verdict, self.root)


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
        FINDINGS,  # written by the fan-in, before the wave it hands the table to
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
            developer__api=[delivered("api")],
            developer__domain=[delivered("domain"), delivered("domain again")],
            developer_integrator=[delivered("merged")],
            tech_lead_review__api=[reviewed("approved")],
            tech_lead_review__domain=[
                reviewed("returned", "no test at the seam"),
                reviewed("approved"),
            ],
            tech_lead_review=[reviewed("approved")],  # the integration, on the task branch
        )
    )
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == "ready_for_review"
    # Wave one builds and reviews both subtasks; wave two is only the one that came
    # back; wave three is the integration. A review belongs to its own Developer's
    # branch, so it no longer waits for the slowest of the wave. Which of two branches
    # runs first inside a wave is the event loop's business, hence the sets.
    built, reviews = script.ran("developer"), script.ran("tech_lead_review")
    assert set(built[:2]) == {"api", "domain"} and built[2:] == ["domain"]
    assert set(reviews[:2]) == {"api", "domain"} and reviews[2:] == ["domain", ""]
    # Every branch merged cleanly and the plan declared no integration work, so no
    # integrator runs: the fan-in already joined them. The integration review does.
    assert script.ran("developer_integrator") == []
    subtasks = final["subtasks"]
    assert {s["status"] for s in subtasks.values()} == {"approved"}
    assert subtasks["api"]["branch"] == "agentic/GH-1.api"
    assert subtasks[INTEGRATION]["branch"] == "agentic/GH-1"
    assert final["changes_requested"] == {"Tech Lead→Developer:domain": 1}
    assert "Developer: domain (v1)" in final["sections"]
    assert "Tech Lead: review domain (v1)" in final["sections"]
    rerun = script.calls[8 + 4]
    assert rerun["cwd"] == MemoryWorkspace.ROOT / "GH-1.domain"
    # The return goes back to the same Developer session, over a delta: not the whole
    # context again (ADR-0004, sixth addendum).
    assert rerun["resume"] == "dev"
    assert "## Tech Lead: review domain" in rerun["prompt"]
    assert "**Returned:** no test at the seam" in rerun["prompt"]
    assert "### Verdict\n\n**returned** (`spec-behaviour`)" in rerun["prompt"]
    assert "## Developer: api" not in rerun["prompt"]  # not its subtask
    assert "## PM" not in rerun["prompt"]  # its session already has it
    assert "## Original request" not in rerun["prompt"]
    # The integration review reads the whole task branch from where it started.
    integration = script.calls[-1]
    assert integration["slug"] == "tech_lead_review"
    assert integration["cwd"] == MemoryWorkspace.ROOT / "GH-1"


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
    config["configurable"]["max_changes_requested_per_pair"] = 0
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


def bug(**queues) -> ScriptedRunner:
    """A runner with the bug path scripted unless overridden: PjM classifies a bug, the
    Product Analyst reproduces and rules it one, PM approves, then the rest."""
    defaults = dict(
        pjm=[classified(type_="bug")], product_analyst_bug=[diagnosed()], pm_bug=[ruled()]
    )
    return ScriptedRunner(**{**defaults, **queues})


async def test_a_bug_is_diagnosed_validated_and_built_like_a_feature(config, tmp_path):
    """The bug path (README.md, 2; issue #65): the diagnosis in place of the definition
    stage, no interview and no flags, then the same technical stage as a feature, PM
    consultant included, up to the pull request."""
    fakes = Fakes()
    script = bug(**technical())
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == "ready_for_review" and final["type"] == "bug"
    assert script.slugs == [
        "pjm",
        "product_analyst_bug",
        "pm_bug",
        "tech_lead_ephemeral",
        "tech_lead",
        "developer",
        "tech_lead_review",
    ]
    assert list(final["sections"]) == [
        PJM.name,
        PRODUCT_ANALYST.name,
        PM.name,
        SOLUTION,
        "Tech Lead",
        FINDINGS,
        "Developer: api",
        "Tech Lead: review api",
    ]
    assert "flags" not in final  # nobody proposed any: a bug changes no scope
    assert final["pull_request"].endswith("/pull/7")
    # PM ruled against a written definition, so nothing was consolidated: the only
    # commit before the Developer's is the solution interview's.
    assert [m for _, m in fakes.workspace.commits] == ["GH-1: solution interview"]
    assert not (tmp_path / "docs/product").exists()
    # The Product Analyst reproduces in the task's checkout, with the product's checks.
    diagnosis = script.calls[1]
    assert diagnosis["cwd"] == MemoryWorkspace.ROOT / "GH-1"
    assert "## PjM\n\nClear." in diagnosis["prompt"]
    assert "Bash(pytest:*)" in diagnosis["role"].allowed
    # PM reads the diagnosis whole; the Tech Leads read PM's ruling, as on a feature.
    assert "## Product Analyst\n\n### Reproduction\n\nRan it." in script.calls[2]["prompt"]
    assert "## PM\n\n### Verdict\n\napproved: bug" in script.calls[3]["prompt"]
    assert fakes.tracker.comments[-1][1].startswith("Ready for review")
    doc = FilesystemTaskDocuments(tmp_path).load("GH-1")
    assert doc["type"] == "bug" and doc["status"] == "ready_for_review"


async def test_a_bug_whose_behaviour_is_expected_is_discarded(config, tmp_path):
    """PM ruled the product right: no plan, no pull request, no label. The issue is
    told why, and the human closes it (README.md, 3, "Cierre")."""
    fakes = Fakes()
    script = bug(pm_bug=[ruled("approved", "expected_behaviour", "weekly by definition")])
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == "discarded" and final["type"] == "bug"
    assert len(script.calls) == 3
    assert "pull_request" not in final and "escalated" not in final
    assert fakes.tracker.pull_requests == [] and fakes.workspace.pushed == []
    assert fakes.tracker.labels == [] and fakes.workspace.cleaned == ["GH-1"]
    assert len(fakes.tracker.comments) == 1
    text = fakes.tracker.comments[0][1]
    assert text.startswith("Discarded: weekly by definition")
    assert "Task document: `.agentic/tasks/GH-1.md`" in text
    assert final["discard_reason"] == "weekly by definition"
    assert final["current_role"] == PJM.name
    doc = FilesystemTaskDocuments(tmp_path).load("GH-1")
    assert doc["status"] == "discarded" and doc["discard_reason"] == "weekly by definition"
    assert not (tmp_path / "docs/product").exists()


async def test_a_returned_diagnosis_goes_back_to_the_product_analyst_then_approves(config):
    script = bug(
        product_analyst_bug=[
            diagnosed("undefined", "", for_pm=["Is weekly the default?"]),
            diagnosed(),
        ],
        pm_bug=[ruled("returned", reason="not reproduced"), ruled()],
        **technical(),
    )
    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    assert final["changes_requested"] == {PAIR_PM_PA.key: 1}
    assert script.slugs[:5] == [
        "pjm",
        "product_analyst_bug",
        "pm_bug",
        "product_analyst_bug",
        "pm_bug",
    ]
    second = script.calls[3]["prompt"]
    assert "## Product Analyst\n\n### Reproduction" in second  # its own previous section
    assert "## PM\n\n### Verdict\n\nreturned: bug" in second and "not reproduced" in second
    assert "You have 1 left" in script.calls[4]["system_prompt"]
    assert f"{PRODUCT_ANALYST.name} (v1)" in final["sections"]
    assert f"{PM.name} (v1)" in final["sections"]
    assert [(r["section"], r["version"]) for r in final["runs"][:5]] == [
        ("PjM", 1),
        ("Product Analyst", 1),
        ("PM", 1),
        ("Product Analyst", 2),
        ("PM", 2),
    ]


async def test_a_platform_enhancement_goes_from_pjm_straight_to_the_plan(config, tmp_path):
    """The platform path (README.md, 2; issue #46): no Product Analyst, no PM, no flags.
    The same technical stage as a feature, run by the platform variants of the Tech
    Lead, and the delivery with no definition to show."""
    fakes = Fakes()
    script = ScriptedRunner(
        pjm=[classified(type_="platform_enhancement")],
        tech_lead_ephemeral_platform=[questions(done=True)],
        tech_lead_platform=[planned()],
        developer=[delivered()],
        tech_lead_review=[reviewed("approved")],
    )
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == "ready_for_review"
    assert final["type"] == "platform_enhancement"
    assert script.slugs == [
        "pjm",
        "tech_lead_ephemeral_platform",
        "tech_lead_platform",
        "developer",
        "tech_lead_review",
    ]
    assert list(final["sections"]) == [
        PJM.name,
        SOLUTION,
        "Tech Lead",
        FINDINGS,
        "Developer: api",
        "Tech Lead: review api",
    ]
    assert "flags" not in final  # no Product Analyst ran, so no flags were ever proposed
    assert final["pull_request"].endswith("/pull/7")
    assert [m for _, m in fakes.workspace.commits] == ["GH-1: solution interview"]
    # The Tech Leads read PjM's reason, and nothing claims a definition that is not there.
    for call in script.calls[1:3]:
        assert "## PjM\n\nClear." in call["prompt"]
        assert "## PM" not in call["prompt"]
    assert "platform enhancement" in script.calls[1]["system_prompt"]
    doc = FilesystemTaskDocuments(tmp_path).load("GH-1")
    assert doc["type"] == "platform_enhancement" and PM.name not in doc["sections"]


async def test_return_loops_back_then_approves(config):
    script = script_for(
        product_analyst=[defined(1), defined(2)],
        pm=[verdict("returned", "missing empty state"), verdict("approved")],
        **technical(),
    )
    final = await run_graph(script, config)

    assert final["status"] == "ready_for_review"
    assert final["changes_requested"] == {PAIR_PM_PA.key: 1}
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
    assert final["changes_requested"] == {PAIR_PM_PA.key: 2}
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
    run = dict(doc["runs"][0])
    for field in ("started_at", "ended_at", "queue_wait_s"):
        assert field in run
        del run[field]
    assert run == {
        "role": "PjM",
        "slug": "pjm",
        "section": "PjM",
        "version": 1,
        "session_id": "",
        "model": "claude-sonnet-5",
        "cost_usd": 0.01,
        "cost_by_model": {},
        "turns": 0,
        "max_turns": 2,
        "tokens": {},
        "calls": 0,
        "tool_calls": {},
        "subagent_cost_usd": 0,
        "resumed_from": "",
        "cold_fallback": False,
        "failed_cost_usd": 0,
        "denials": 0,
        "turn_notice": False,
        "turn_notice_firings": 0,
        "deliveries": 0,
        "presubmit_denials": 0,
    }
    assert doc["status"] == WAITING_ON_HUMAN


async def test_the_task_cap_escalates_once_during_a_fan_out(config):
    # The definition and the plan spend 0.86; both Developer branches start from that
    # total, both find the cap reached, and the escalation reaches the issue once
    # (ADR-0004, third addendum).
    config["configurable"]["task_max_budget_usd"] = 0.7
    fakes = Fakes()
    plan = planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})
    script = script_for(**technical(tech_lead=[plan]))
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == WAITING_ON_HUMAN
    assert final["escalated"] == "Developer stopped by the task cap: 0.86 of 0.70 USD spent"
    assert "developer" not in script.slugs
    assert final["cost_usd"] == pytest.approx(0.86)
    assert fakes.tracker.labels == [("GH-1", "needs-human")]
    assert len(fakes.tracker.comments) == 1
    assert {s["status"] for s in final["subtasks"].values()} == {"pending"}


async def test_escalating_during_a_fan_out_saves_the_document_with_every_branch(config, tmp_path):
    # Both Developers leave their tree dirty twice and escalate in the same superstep.
    # Each branch saves its own view of the document, so whichever writes last loses
    # the other's failures; `escalate` saves the merged state, and the document ends
    # with both whatever the order.
    fakes = Fakes()
    fakes.workspace.dirty.update(
        {MemoryWorkspace.ROOT / "GH-1.api", MemoryWorkspace.ROOT / "GH-1.domain"}
    )
    plan = planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})
    script = script_for(**technical(tech_lead=[plan], developer=[delivered()] * 4))
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == WAITING_ON_HUMAN
    assert final["escalated"].startswith("Developer failed 2 times: structured output invalid")
    assert len(final["failures"]) == 4
    doc = FilesystemTaskDocuments(tmp_path).load("GH-1")
    assert len(doc["failures"]) == 4
    assert doc["status"] == WAITING_ON_HUMAN and doc["escalated"] == final["escalated"]
    assert len(fakes.tracker.comments) == 1


async def test_a_branch_that_escalates_does_not_lose_the_sister_that_delivered(config, tmp_path):
    # `domain` leaves its tree dirty twice and escalates; `api` is approved in the same
    # wave. Before the fan-in this was not testable: both branches saved the document
    # and both wrote `status`, so whether the run ended `developing` or
    # `waiting_on_human`, and whether `api`'s approval survived, was the event loop's
    # call. `integrate` settles both, once, with every branch's work merged in.
    fakes = Fakes()
    fakes.workspace.dirty.add(MemoryWorkspace.ROOT / "GH-1.domain")
    plan = planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})
    script = script_for(
        **technical(
            tech_lead=[plan],
            developer__api=[delivered("api")],
            developer__domain=[delivered()] * 2,
            tech_lead_review__api=[reviewed("approved")],
        )
    )
    final = await run_graph(script, config, fakes=fakes)

    assert final["status"] == WAITING_ON_HUMAN
    assert final["escalated"].startswith("Developer failed 2 times")
    assert final["subtasks"]["api"]["status"] == "approved"
    assert final["subtasks"]["domain"]["status"] == "pending"
    assert "Tech Lead: review api" in final["sections"]
    doc = FilesystemTaskDocuments(tmp_path).load("GH-1")
    assert doc["status"] == WAITING_ON_HUMAN and doc["escalated"] == final["escalated"]
    assert doc["subtasks"]["api"]["status"] == "approved"
    # Told once, however many branches came back.
    assert fakes.tracker.labels == [("GH-1", "needs-human")]
    assert len(fakes.tracker.comments) == 1


async def test_the_task_cap_counts_what_the_sister_branches_spent(config):
    # A branch of the fan-out carries a copy of the state, so `cost_usd` only tells it
    # what the task had spent at the fan-out: on its own, each branch would spend a
    # whole cap's worth. What every branch does share is `RunLimits` (ADR-0004, fifth
    # addendum), and the cap is read against it. One role at a time, so the order the
    # branches spend in is the test's, not the event loop's.
    config["configurable"]["task_max_budget_usd"] = 2.0
    config["configurable"]["max_concurrent_roles"] = 1
    plan = planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})
    script = script_for(
        **technical(
            tech_lead=[plan],
            developer=[delivered("api"), delivered("domain")],
            tech_lead_review=[reviewed("approved"), reviewed("approved")],
        )
    )
    final = await run_graph(script, config)

    assert final["status"] == WAITING_ON_HUMAN
    assert "stopped by the task cap" in final["escalated"]
    # The definition stage and the plan cost 0.86, each Developer 0.5, each review 0.2.
    # Whichever role would take the run past 2.00 never starts, so at most one role ran
    # beyond the cap and the overrun is one role, not one per branch.
    assert final["cost_usd"] < 2.0 + 0.5
    assert script.slugs.count("developer") + script.slugs.count("tech_lead_review") < 4
    assert INTEGRATION not in final["subtasks"]


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
            "reason": "other",
            "slug": "product_analyst",
            "section": "Product Analyst",
            "cold_fallback": False,
            "denials": 0,
            "turn_notice": False,
            "turn_notice_firings": 0,
            "deliveries": 0,
            "presubmit_denials": 0,
        }
    ]


async def test_a_tasks_rework_usd_equals_the_sum_of_its_role_samples_end_to_end(config):
    """The acceptance case (`docs/product/rework.md`, PM's consolidated definition) on
    the real path: a return and a failed attempt retried successfully, folded by the
    graph's own reducers exactly as `agentic run` would, never a hand-written state.
    `agentic.task.rework_usd` equals the sum of the `rework="true"`
    `agentic.role.cost_usd` samples, and every `agentic.role.cost_usd` sample sums to
    the state's own `cost_usd` (ADR-0008, 2026-09-18 second addendum). The `_node`
    `except` path (`run.finish({})`) is not exercised here — that money is outside both
    views on purpose (`test_a_crash_in_a_node_still_finishes_the_roles_span` covers the
    zero-cost sample it leaves) — this test only proves the equality on the path that
    actually delivers."""
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = BatchReader()
    telemetry.configure(
        tracer_provider=tracer_provider, meter_provider=telemetry.meter_provider_for(reader)
    )
    try:
        script = script_for(
            product_analyst=[AttemptFailed("invalid", 0.02), defined(1), defined(2)],
            pm=[verdict("returned", "gap"), verdict("approved")],
            **technical(),
        )
        with telemetry.task_span("GH-1", "t", "gym-saas", arm="default") as run:
            final = await run_graph(script, config, telemetry)
            run.finish(final)
    finally:
        telemetry.configure()
    got = points(reader)
    role_costs = got["agentic.role.cost_usd"]
    assert sum(c.value for c in role_costs) == pytest.approx(final["cost_usd"])
    rework_total = sum(c.value for c in role_costs if c.attributes["rework"])
    (task_rework,) = got["agentic.task.rework_usd"]
    assert rework_total == pytest.approx(
        task_rework.value, abs=telemetry.system_metrics.RESIDUAL_FLOOR_USD
    )


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
        with telemetry.task_span("GH-1", "t", "gym-saas", arm="default") as run:
            run.finish(await run_graph(script, config, telemetry))
    finally:
        telemetry.configure()
    finished = exporter.get_finished_spans()
    task = finished[-1]
    # Every run record now leaves its own child span too, under the role span it
    # belongs to rather than the task: filtering for the task's direct children
    # recovers exactly one span per node, whatever a node's own patch held.
    task_id = task.get_span_context().span_id
    role_spans = [s for s in finished[:-1] if s.parent and s.parent.span_id == task_id]
    assert [s.name for s in role_spans] == [
        "invoke_agent PjM",
        "invoke_agent Product Analyst",
        "invoke_agent PM",
        "invoke_agent Product Analyst",
        "invoke_agent PM",
        "invoke_agent Tech Lead",
        "invoke_agent Developer",
        "invoke_agent Tech Lead",
    ]
    # Every span but the task's own hangs off some role span, directly or (a run's
    # child) one level further down.
    role_ids = {s.get_span_context().span_id for s in role_spans}
    assert all(s.parent.span_id == task_id or s.parent.span_id in role_ids for s in finished[:-1])
    # The role spans carry what each use case decided; the task span, the totals.
    outcomes = [s.attributes["role.outcome"] for s in role_spans]
    assert outcomes == ["delivered"] * 2 + ["returned"] + ["delivered"] * 5
    assert (
        role_spans[3].attributes["agentic.role.version"] == 2
        and role_spans[3].attributes["agentic.role.rework"]
    )
    assert role_spans[6].attributes["agentic.role.model"] == "claude-sonnet-5"
    assert task.attributes["task.status"] == "ready_for_review"
    assert task.attributes["task.returns"] == 1
    # Every role span says which product the run was over, read from what the product
    # declares and not from the task span it hangs under (issue #47).
    assert {s.attributes["product"] for s in role_spans} == {"product"}
    # The plan and the review are both "Tech Lead", but not the same role: before the
    # fix the review node passed the plan's own role, and every review's cost, tokens
    # and duration landed under `role.slug=tech_lead` next to the plan's (the 58% of
    # spend the request opens with). `gen_ai.agent.id` tells them apart now.
    tech_lead_spans = [s for s in role_spans if s.name == "invoke_agent Tech Lead"]
    plan_span, *review_spans = tech_lead_spans
    assert plan_span.attributes[semconv.AGENT_ID] == "tech_lead"
    assert review_spans and all(
        s.attributes[semconv.AGENT_ID] == "tech_lead_review" for s in review_spans
    )


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
        "diagnose",
        "validate",
        "plan",
        "pipeline",
        "integrate",
        "deliver",
        "discard",
        "escalate",
        "pause",
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
    # The role span, plus its one run record's own child span (both named for PjM).
    assert [s.name for s in exporter.get_finished_spans()] == [
        "invoke_agent PjM",
        "invoke_agent PjM",
    ]


async def test_a_crash_in_a_node_still_finishes_the_roles_span(config):
    """An exception outside the use case's own failure handling (here: the knowledge
    cannot be written) propagates, but the span closes with an outcome and cost."""

    class BrokenKnowledge(MemoryKnowledge):
        def consolidate(self, verdict, root=None):
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
                stack=MemoryStack(),
                tracker=MemoryTracker(),
            )

        with pytest.raises(OSError, match="read-only"):
            await (
                build_graph(ports_for, telemetry, arm_for=composition._arm_for)
                .compile()
                .ainvoke(TASK, config=config)
            )
    finally:
        telemetry.configure()
    pm = exporter.get_finished_spans()[-1]
    assert pm.name == "invoke_agent PM"
    assert (
        pm.attributes["role.outcome"] == "failed" and pm.attributes["agentic.role.cost_usd"] == 0.0
    )
    assert pm.status.status_code.name == "ERROR"  # the exception is on the span too


# Pause and resume (ADR-0003, addendum): an escalation pauses in `pause`, after
# `escalate` told the human once; the answer resumes the task where it stopped.


def two_subtasks(**queues) -> ScriptedRunner:
    plan = planned({"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]})
    return script_for(
        **technical(
            tech_lead=[plan],
            developer=[delivered("api"), delivered("domain")],
            developer_integrator=[delivered("merged")],
            tech_lead_review=[reviewed("approved")] * 3,
            **queues,
        )
    )


def raised(config, **more) -> dict:
    """The same thread with the cap the human raises on `agentic resume`."""
    configurable = {**config["configurable"], "task_budget_usd": 100.0, **more}
    configurable.pop("task_max_budget_usd", None)
    return {"configurable": configurable}


async def test_the_task_cap_pauses_the_task_and_a_raised_cap_resumes_it_to_delivery(config):
    config["configurable"]["task_max_budget_usd"] = 0.7
    fakes = Fakes()
    script = two_subtasks()
    app = compiled(script, fakes=fakes)

    paused = await app.ainvoke(TASK, config=config)
    assert paused["status"] == WAITING_ON_HUMAN
    (waiting,) = paused["__interrupt__"]
    assert waiting.value == {
        "reason": "Developer stopped by the task cap: 0.86 of 0.70 USD spent",
        "spent_usd": pytest.approx(0.86),
        "questions": [],
    }
    assert (await app.aget_state(config)).next == ("pause",)
    assert fakes.tracker.labels == [("GH-1", "needs-human")]
    assert "`agentic resume --issue 1 --task-budget 16`" in fakes.tracker.comments[0][1]

    final = await app.ainvoke(Command(resume={"decision": None}), config=raised(config))
    assert final["status"] == "ready_for_review" and final["escalated"] is None
    assert script.slugs[8:] == [
        "developer",
        "developer",
        "tech_lead_review",
        "tech_lead_review",
        # No integrator: the fan-in merged both branches, so only the review runs.
        "tech_lead_review",
    ]
    assert final["cost_usd"] == pytest.approx(0.86 + 0.5 * 2 + 0.2 * 3)
    assert "Human" not in final["sections"]  # nothing was decided
    assert len(fakes.tracker.labels) == 1  # told once: the resume did not escalate again
    assert fakes.tracker.comments[-1][1].startswith("Ready for review")


async def test_a_subtask_at_its_limit_resumes_with_the_decision_and_the_pair_reset(config):
    config["configurable"]["max_changes_requested_per_pair"] = 0
    fakes = Fakes()
    script = script_for(
        **technical(
            developer=[delivered("v1"), delivered("v2")],
            tech_lead_review=[reviewed("returned", "no header row"), reviewed("approved")],
        )
    )
    app = compiled(script, fakes=fakes)
    paused = await app.ainvoke(TASK, config=config)
    assert paused["escalated"] == "Tech Lead→Developer:api hit 0 returns: no header row"
    assert paused["subtasks"]["api"]["status"] == "returned"

    decision = "Ship it without the header; the auditors import by position."
    final = await app.ainvoke(Command(resume={"decision": decision}), config=config)
    assert final["status"] == "ready_for_review"
    assert final["changes_requested"] == {"Tech Lead→Developer:api": 0}
    human = final["sections"]["Human"]
    assert human.startswith("### Decision 1\n\nEscalated: Tech Lead→Developer:api hit 0 returns")
    assert human.endswith(decision)
    developer, review = script.calls[-2], script.calls[-1]
    assert developer["slug"] == "developer" and "no header row" in developer["prompt"]
    assert review["slug"] == "tech_lead_review" and f"## Human\n\n{human}" in review["prompt"]


async def test_questions_for_the_human_resume_the_interview_with_the_answer(config, tmp_path):
    # A retake (ADR-0014 addendum), not a second run from scratch: the definition
    # interview closed at round 2 with a question still open, and the resume gets it
    # exactly `INTERVIEW_RETAKE_ROUNDS` (2) more, both seats resumed on the sessions the
    # first run left, never the ordinary cap's worth again.
    fakes = Fakes()
    script = script_for(
        product_analyst_interviewer=[
            questions(1, session="q-old"),
            questions(done=True, session="q-old"),
            questions(2, session="q-new"),
            questions(done=True, session="q-new"),
        ],
        pm_interviewee=[
            answers(1, for_human=["Which currency?"], session="a-old"),
            answers(2, session="a-new"),
        ],
        **technical(),
    )
    app = compiled(script, fakes=fakes)
    paused = await app.ainvoke(TASK, config=config)
    assert paused["questions_for_human"] == ["Which currency?"]

    final = await app.ainvoke(Command(resume={"decision": "USD, always."}), config=config)
    assert final["status"] == "ready_for_review" and final["questions_for_human"] == []

    pa_calls = [c for c in script.calls if c["slug"] == "product_analyst_interviewer"]
    pm_calls = [c for c in script.calls if c["slug"] == "pm_interviewee"]
    # Both seats resume on the sessions the first (paused) run left: the interviewer's
    # own session across the whole thing, and the interviewee's from before the pause.
    assert [c["resume"] for c in pa_calls] == [None, "q-old", "q-old", "q-new"]
    assert [c["resume"] for c in pm_calls] == [None, "a-old"]

    retake_ask, retake_answer = pa_calls[2]["prompt"], pm_calls[1]["prompt"]
    # The interviewer reads the human's decision as its next message, not through
    # context a resumed session never re-reads.
    assert retake_ask.startswith("Branch `agentic/GH-1` is at `")
    assert "## Human\n\n### Decision 1" in retake_ask and "USD, always." in retake_ask
    # The interviewee gets it before the next round's questions, not instead of them.
    assert "## Human\n\n### Decision 1" in retake_answer and "USD, always." in retake_answer
    assert "## Questions (round 3)" in retake_answer

    section = final["sections"]["Interview: Product Analyst→PM"]
    assert "### Round 1: questions" in section and "### Round 3: questions" in section
    assert "USD, always." in FilesystemTaskDocuments(tmp_path).load("GH-1")["sections"]["Human"]


async def test_a_task_seeded_from_its_document_resumes_like_a_checkpoint(config, tmp_path):
    # The rescue path: the checkpoint is gone, the document and git are what is left.
    config["configurable"]["task_max_budget_usd"] = 0.7
    fakes = Fakes()
    script = two_subtasks()
    await compiled(script, fakes=fakes).ainvoke(TASK, config=config)

    # The Developers the cap stopped had their branches created already.
    fakes.workspace.existing_branches.update(
        {"agentic/GH-1": ("t1", "base0"), "agentic/GH-1.api": ("base1", "base1")}
    )
    fakes.workspace.existing_branches["agentic/GH-1.domain"] = ("base1", "base1")
    ports = replace(memory_ports(), documents=FilesystemTaskDocuments(tmp_path))
    ports = replace(ports, workspace=fakes.workspace)
    state = resume.from_document("GH-1", ports)
    app = compiled(script, fakes=fakes)  # a new, empty checkpointer
    await app.aupdate_state(config, state, as_node="escalate")
    final = await app.ainvoke(Command(resume={"decision": None}), config=raised(config))

    assert final["status"] == "ready_for_review"
    assert final["cost_usd"] == pytest.approx(0.86 + 0.5 * 2 + 0.2 * 3)
    assert len(final["runs"]) == 8 + 5  # the document's runs, then the resumed ones
    assert len(fakes.tracker.labels) == 1


async def test_two_tasks_through_one_graph_do_not_share_what_they_spent(tmp_path):
    """`agentic.composition` builds the graph once, at import, and a `langgraph dev`
    server runs every task through it. One `RunLimits` per graph would hand the second
    task the first one's spend and stop it on its first role."""
    fakes = Fakes()
    fakes.workspace.moved.add(MemoryWorkspace.ROOT / "GH-2")
    one = script_for(**technical())
    both = ScriptedRunner(**{slug: queue * 2 for slug, queue in one.queues.items()})
    # One compiled graph for the two tasks, as a `langgraph dev` server has it.
    app = compiled(both, fakes=fakes)

    # A cap one run fits under and two do not: sharing the counter would stop the
    # second task on its first role, which is what this is about.
    where = {"product_dir": str(tmp_path), "task_max_budget_usd": 2.0}
    first = {"configurable": {**where, "thread_id": "GH-1"}}
    final = await app.ainvoke(TASK, config=first)
    assert final["status"] == "ready_for_review"
    assert 1.0 < final["cost_usd"] < 2.0

    second = {"configurable": {**where, "thread_id": "GH-2"}}
    again = await app.ainvoke({**TASK, "id": "GH-2"}, config=second)
    assert again["status"] == "ready_for_review"
    assert again["cost_usd"] == pytest.approx(final["cost_usd"])


async def test_what_one_review_found_reaches_the_next_wave_and_the_integration(config):
    """Issue #17: run 4's two integration returns were already in subtask reviews as
    "worth a remark, not a charge", and nobody downstream ever saw them. They travel now,
    through the fan-in, which is the only place that sees every branch of a wave."""
    remark = {
        **finding(severity="important", claim="counts subscriptions twice", scope="plan"),
        "location": "revenue.py:12",
    }
    plan = planned(
        {"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"]},
        invariants=["Every subscription query folds duplicates."],
    )
    script = script_for(
        **technical(
            tech_lead_ephemeral=[questions(done=True, invariants=["fold duplicates"])],
            tech_lead=[plan],
            developer__api=[delivered("api")],
            developer__domain=[delivered("domain"), delivered("domain again")],
            tech_lead_review__api=[reviewed("approved", findings=[remark])],
            tech_lead_review__domain=[
                reviewed("returned", "no test at the seam"),
                # The pass that approves settles its own row, by location. Nothing else
                # closes a row: an incremental pass is told not to re-read what it
                # already approved, so its silence is not a verdict.
                reviewed("approved", settled=["x.py:1"]),
            ],
            tech_lead_review=[reviewed("approved")],  # the integration
        )
    )
    final = await run_graph(script, config)
    assert final["status"] == "ready_for_review"

    # The ephemeral proposed; the Tech Lead adopted, and every role that builds or
    # reviews gets the invariant, from the first wave on, when the table is still empty.
    assert final["invariants"] == ["Every subscription query folds duplicates."]
    first_wave = [c for c in script.calls if c["slug"] == "developer"][0]
    assert "- Every subscription query folds duplicates." in first_wave["system_prompt"]
    assert f"## {FINDINGS}\n\nNone open." in first_wave["prompt"]

    # The second wave's Developer rebuilds `domain` seeing what `api`'s review found,
    # which its own review never told it and its sister could not.
    rebuilt = [c for c in script.calls if c["slug"] == "developer"][-1]
    assert "counts subscriptions twice" in rebuilt["prompt"]
    # And so does the integration review, the one that reads the whole branch.
    assert "counts subscriptions twice" in script.calls[-1]["prompt"]
    assert script.calls[-1]["slug"] == "tech_lead_review"

    # The raw record keeps every pass; the section keeps what is still open. Which of
    # the two branches of a wave appended first is the event loop's business.
    assert {f["subtask"] for f in final["findings"]} == {"api", "domain"}
    assert final["settled_findings"] == ["x.py:1"]
    table = final["sections"][FINDINGS]
    assert table.count("\n") == 2  # a header, its rule, one row: `domain`'s was settled
    assert "counts subscriptions twice" in table and "no test at the seam" not in table


async def test_a_node_whose_product_declares_no_name_still_runs(config):
    """A label is not worth the task: past `preflight` the run is under way, and this is
    the door `preflight` does not guard, `langgraph dev` (issue #47)."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.configure(tracer_provider=provider)
    try:
        script = script_for(pjm=[classified(confidence="low")])
        graph = compiled(script, telemetry, stack=MemoryStack(product=None))
        await graph.ainvoke(TASK, config=config)
    finally:
        telemetry.configure()
    assert exporter.get_finished_spans()[0].attributes["product"] == UNKNOWN_PRODUCT
