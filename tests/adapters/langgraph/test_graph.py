"""The path end to end through the compiled graph: routing, loops, escalation,
with a scripted runner and the real filesystem adapters in a temporary product."""

from dataclasses import replace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentic import composition
from agentic.adapters.filesystem import FilesystemTaskDocuments
from agentic.adapters.langgraph.graph import build_graph, configuration_from
from agentic.adapters.otel import tracing
from agentic.application.ports import AttemptFailed
from agentic.composition import default_ports
from agentic.domain.roles import PAIR_PM_PA, PJM, PM, PRODUCT_ANALYST
from agentic.domain.task import WAITING_ON_HUMAN
from tests.fakes import ScriptedRunner, classified, defined, verdict

pytestmark = pytest.mark.anyio

TASK = {
    "id": "T-1",
    "title": "Export report",
    "description": "Users want CSV.\n\n## Why\n\nAudits.",
}


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "T-1"}}


async def run_graph(script: ScriptedRunner, config):
    """The real ports over the temporary product, with the scripted runner in place
    of the SDK. `default_ports` is what the exported graph uses."""

    def ports_for(cfg):
        return replace(default_ports(cfg), runner=script)

    return await build_graph(ports_for).compile().ainvoke(TASK, config=config)


def calls(script):
    return [(c["slug"], c["prompt"], c["system_prompt"], c["model"]) for c in script.calls]


async def test_happy_path(config, tmp_path):
    script = ScriptedRunner(
        pjm=[classified()], product_analyst=[defined()], pm=[verdict("approved")]
    )
    final = await run_graph(script, config)

    assert final["status"] == "definition_ready"
    assert script.slugs == ["pjm", "product_analyst", "pm"]
    assert list(final["sections"]) == [PJM.name, PRODUCT_ANALYST.name, PM.name]
    assert final["flags"] == {"touches_ui": True, "touches_copy": False}  # PM replaces PA's set
    assert final["cost_usd"] == pytest.approx(0.31)
    assert final["failures"] == []
    assert set(final["prompts"]) == {"pjm", "product_analyst", "pm"}
    pm_input = script.calls[2]["prompt"]
    assert "## Original request" in pm_input and "Users want CSV." in pm_input
    assert "## Product Analyst\n\n### Behaviour\n\nv1" in pm_input  # the full section
    assert "## PjM" not in pm_input  # PM does not declare it
    assert "You have 2 left" in script.calls[2]["system_prompt"]
    assert "- `touches_ui`: " in script.calls[2]["system_prompt"]
    assert script.calls[0]["cwd"] == tmp_path  # the SDK subprocess runs in the product dir

    doc = FilesystemTaskDocuments(tmp_path).load("T-1")
    assert doc["status"] == "definition_ready"
    assert doc["cost_usd"] == 0.31
    assert doc["sections"][PM.name].startswith("### Verdict")


async def test_approval_writes_the_product_knowledge(config, tmp_path):
    (tmp_path / "CONTEXT.md").write_text("# Product\n\n## Language\n\n**Gym**:\nA business.\n")
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/members.md").write_text("# Members\n\nOld.\n")
    script = ScriptedRunner(
        pjm=[classified()],
        product_analyst=[defined()],
        pm=[
            verdict(
                "approved",
                definitions=[{"capability": "reports", "content": "# Reports\n\nCSV export."}],
                glossary=[{"term": "Report", "definition": "A CSV file.", "avoid": "export"}],
            )
        ],
    )
    final = await run_graph(script, config)
    assert final["status"] == "definition_ready"

    # Product roles read the knowledge, whole, before the task document; PjM does not.
    assert "## Product knowledge" not in script.calls[0]["prompt"]
    # The request's own headings never look like sections, in context or in the file.
    assert "### Why" in script.calls[0]["prompt"] and "\n## Why" not in script.calls[0]["prompt"]
    assert "\n## Why" not in (tmp_path / ".agentic/tasks/T-1.md").read_text()
    for call in script.calls[1:]:
        assert call["prompt"].startswith("## Product knowledge")
        assert "A business." in call["prompt"] and "Old." in call["prompt"]
        assert "## Original request" in call["prompt"]

    assert (tmp_path / "docs/product/reports.md").read_text() == "# Reports\n\nCSV export.\n"
    assert (tmp_path / "docs/product/members.md").read_text() == "# Members\n\nOld.\n"
    glossary = (tmp_path / "CONTEXT.md").read_text()
    assert "**Gym**:\nA business." in glossary
    assert glossary.rstrip().endswith("**Report**:\nA CSV file.\n_Avoid_: export")


async def test_return_writes_nothing(config, tmp_path):
    # No returns allowed: the first one escalates, and the knowledge stays untouched.
    config["configurable"]["max_returns_per_pair"] = 0
    script = ScriptedRunner(
        pjm=[classified()], product_analyst=[defined()], pm=[verdict("returned", "gap")]
    )
    final = await run_graph(script, config)
    assert final["status"] == WAITING_ON_HUMAN and "hit 0 returns: gap" in final["escalated"]
    assert not (tmp_path / "docs/product").exists()
    assert not (tmp_path / "CONTEXT.md").exists()


async def test_approving_a_feature_without_definitions_is_retried_then_escalated(config, tmp_path):
    empty = verdict("approved", definitions=[])
    script = ScriptedRunner(pjm=[classified()], product_analyst=[defined()], pm=[empty, empty])
    final = await run_graph(script, config)
    assert final["status"] == WAITING_ON_HUMAN
    assert "without `definitions`" in final["escalated"]
    assert "without `definitions`" in script.calls[3]["prompt"]  # the error went back to PM
    assert not (tmp_path / "docs/product").exists()


async def test_models_follow_kind(config):
    script = ScriptedRunner(
        pjm=[classified()], product_analyst=[defined()], pm=[verdict("approved")]
    )
    await run_graph(script, config)
    models = {c["slug"]: c["model"] for c in script.calls}
    assert models["pjm"] == models["product_analyst"] == "claude-sonnet-5"
    assert models["pm"] == "claude-opus-5"


async def test_low_confidence_escalates_before_defining(config):
    script = ScriptedRunner(pjm=[classified(confidence="low")], product_analyst=[], pm=[])
    final = await run_graph(script, config)
    assert final["status"] == WAITING_ON_HUMAN
    assert "PjM could not classify" in final["escalated"]
    assert len(script.calls) == 1


async def test_non_feature_escalates(config):
    script = ScriptedRunner(pjm=[classified(type_="bug")], product_analyst=[], pm=[])
    final = await run_graph(script, config)
    assert final["type"] == "bug"
    assert final["status"] == WAITING_ON_HUMAN
    assert final["escalated"] == "bug path not implemented yet"
    assert len(script.calls) == 1


async def test_return_loops_back_then_approves(config):
    script = ScriptedRunner(
        pjm=[classified()],
        product_analyst=[defined(1), defined(2)],
        pm=[verdict("returned", "missing empty state"), verdict("approved")],
    )
    final = await run_graph(script, config)

    assert final["status"] == "definition_ready"
    assert final["returns"] == {PAIR_PM_PA: 1}
    assert script.slugs == ["pjm", "product_analyst", "pm", "product_analyst", "pm"]
    second_define = script.calls[3]["prompt"]
    assert "## Product Analyst\n\n### Behaviour\n\nv1" in second_define  # its own previous
    assert "## PM\n\n### Verdict\n\nreturned" in second_define  # PM's full section
    assert "missing empty state" in second_define
    second_validate = script.calls[4]["prompt"]
    assert "v2" in second_validate and "### Verdict\n\nreturned" in second_validate
    assert "You have 1 left" in script.calls[4]["system_prompt"]
    # The current section holds the latest delivery; the returned one stays as history.
    assert "v2" in final["sections"][PRODUCT_ANALYST.name]
    assert "v1" in final["sections"][f"{PRODUCT_ANALYST.name} (v1)"]
    assert "returned" in final["sections"][f"{PM.name} (v1)"]
    assert [(r["role"], r["version"]) for r in final["runs"]] == [
        ("PjM", 1),
        ("Product Analyst", 1),
        ("PM", 1),
        ("Product Analyst", 2),
        ("PM", 2),
    ]
    # History is not context: the second PM pass reads only current sections.
    assert "(v1)" not in script.calls[4]["prompt"]


async def test_return_limit_escalates(config):
    script = ScriptedRunner(
        pjm=[classified()],
        product_analyst=[defined(1), defined(2), defined(3)],
        pm=[verdict("returned"), verdict("returned"), verdict("returned", "still no metrics")],
    )
    final = await run_graph(script, config)

    assert final["status"] == WAITING_ON_HUMAN
    assert final["returns"] == {PAIR_PM_PA: 2}
    assert "hit 2 returns: still no metrics" in final["escalated"]
    assert "You have 0 left" in script.calls[-1]["system_prompt"]
    assert len(script.calls) == 7


async def test_role_failure_escalates_and_keeps_failures(config, tmp_path):
    script = ScriptedRunner(
        pjm=[classified()],
        product_analyst=[
            AttemptFailed("no structured output", 0.05),
            AttemptFailed("empty markdown", 0.03),
        ],
        pm=[],
    )
    final = await run_graph(script, config)

    assert final["status"] == WAITING_ON_HUMAN
    assert final["escalated"].startswith("Product Analyst failed 2 times")
    assert [f["error"] for f in final["failures"]] == ["no structured output", "empty markdown"]
    assert final["cost_usd"] == pytest.approx(0.01 + 0.05 + 0.03)  # failed attempts still cost
    assert PRODUCT_ANALYST.name not in final["sections"]
    doc = FilesystemTaskDocuments(tmp_path).load("T-1")
    assert [f["error"] for f in doc["failures"]] == ["no structured output", "empty markdown"]
    assert doc["runs"] == [
        {
            "role": "PjM",
            "version": 1,
            "session_id": "",
            "model": "claude-sonnet-5",
            "cost_usd": 0.01,
            "turns": 0,
        }
    ]
    assert doc["status"] == WAITING_ON_HUMAN


async def test_retried_failure_is_recorded_but_not_escalated(config):
    script = ScriptedRunner(
        pjm=[classified()],
        product_analyst=[AttemptFailed("invalid", 0.02), defined()],
        pm=[verdict("approved")],
    )
    final = await run_graph(script, config)
    assert final["status"] == "definition_ready"
    assert final["failures"] == [
        {"role": "Product Analyst", "attempt": 1, "error": "invalid", "cost_usd": 0.02}
    ]
    assert final["cost_usd"] == pytest.approx(0.33)


async def test_each_node_opens_the_roles_span(config, monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_provider", provider)
    monkeypatch.setattr(tracing, "_configured", True)
    script = ScriptedRunner(
        pjm=[classified()], product_analyst=[defined()], pm=[verdict("approved")]
    )
    with tracing.task_span("T-1", "t"):
        await run_graph(script, config)
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans) == {"T-1", "PjM", "Product Analyst", "PM"}
    task_id = spans["T-1"].get_span_context().span_id
    assert all(spans[r].parent.span_id == task_id for r in ("PjM", "Product Analyst", "PM"))


def test_configuration_comes_from_configurable_and_env(monkeypatch, tmp_path):
    cfg = configuration_from({"configurable": {"product_dir": str(tmp_path), "verbose": True}})
    assert cfg.product_dir == tmp_path and cfg.verbose is True
    monkeypatch.setenv("AGENTIC_PRODUCT_DIR", "/elsewhere")
    assert str(
        configuration_from({"configurable": {"product_dir": str(tmp_path)}}).product_dir
    ) == ("/elsewhere")


def test_exported_graph_uses_the_default_ports():
    assert set(composition.graph.nodes) == {"classify", "define", "validate"}
