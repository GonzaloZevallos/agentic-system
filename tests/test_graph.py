"""Routing with recorded outputs. `agent.run` is replaced by a script per role."""

import pytest

from agentic import graph as g
from agentic.agent import Output, RoleFailure
from agentic.document import read
from agentic.roles import PAIR_PM_PA, PJM, PM, PRODUCT_ANALYST

pytestmark = pytest.mark.anyio

TASK = {
    "id": "T-1",
    "title": "Export report",
    "description": "Users want CSV.\n\n## Why\n\nAudits.",
}


def classified(type_="feature", confidence="high"):
    return Output(
        text="Clear.", data={"type": type_, "confidence": confidence, "reason": "r"}, cost_usd=0.01
    )


def defined(n=1):
    return Output(
        text=f"### Behaviour\n\nv{n}\n\n### For the next role\n\nDefinition v{n}.",
        data={"flags": {"touches_ui": False, "touches_copy": True}},
        cost_usd=0.1,
    )


def verdict(kind, reason="r", definitions=None, glossary=None):
    if definitions is None:
        definitions = (
            [{"capability": "reports", "content": "# Reports"}] if kind == "approved" else []
        )
    return Output(
        text=f"### Verdict\n\n{kind}\n\n### For the next role\n\n{reason}",
        data={
            "verdict": kind,
            "reason": reason,
            "flags": {"touches_ui": True, "touches_copy": False},
            "definitions": definitions,
            "glossary": glossary or [],
        },
        cost_usd=0.2,
    )


class Script:
    """Answers `run` calls from per-role queues and records the prompts it saw."""

    def __init__(self, **queues):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls = []
        self.checks = {}

    async def __call__(
        self, role, prompt, *, system_prompt, model, retries, flags, cwd, check=None, observer=None
    ):
        self.calls.append((role.slug, prompt, system_prompt, model))
        self.checks[role.slug] = check
        assert set(flags) == {"touches_ui", "touches_copy"}
        assert cwd is not None  # the SDK subprocess runs in the product dir
        item = self.queues[role.slug].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "T-1"}}


async def run_graph(monkeypatch, script, config):
    monkeypatch.setattr(g, "run", script)
    return await g.graph.compile().ainvoke(TASK, config=config)


async def test_happy_path(monkeypatch, config, tmp_path):
    script = Script(pjm=[classified()], product_analyst=[defined()], pm=[verdict("approved")])
    final = await run_graph(monkeypatch, script, config)

    assert final["status"] == "definition_ready"
    assert [c[0] for c in script.calls] == ["pjm", "product_analyst", "pm"]
    assert list(final["sections"]) == [PJM.name, PRODUCT_ANALYST.name, PM.name]
    assert final["flags"] == {"touches_ui": True, "touches_copy": False}  # PM replaces PA's set
    assert final["cost_usd"] == pytest.approx(0.31)
    assert final["failures"] == []
    assert set(final["prompts"]) == {"pjm", "product_analyst", "pm"}
    pm_input = script.calls[2][1]
    assert "## Original request" in pm_input and "Users want CSV." in pm_input
    assert "## Product Analyst\n\n### Behaviour\n\nv1" in pm_input  # the full section
    assert "## PjM" not in pm_input  # PM does not declare it
    assert "You have 2 left" in script.calls[2][2]
    assert "- `touches_ui`: " in script.calls[2][2]

    doc = read(tmp_path / ".agentic/tasks/T-1.md")
    assert doc["status"] == "definition_ready"
    assert doc["cost_usd"] == 0.31
    assert doc["sections"][PM.name].startswith("### Verdict")


async def test_approval_writes_the_product_knowledge(monkeypatch, config, tmp_path):
    (tmp_path / "CONTEXT.md").write_text("# Product\n\n## Language\n\n**Gym**:\nA business.\n")
    (tmp_path / "docs/product").mkdir(parents=True)
    (tmp_path / "docs/product/members.md").write_text("# Members\n\nOld.\n")
    script = Script(
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
    final = await run_graph(monkeypatch, script, config)
    assert final["status"] == "definition_ready"

    # Product roles read the knowledge, whole, before the task document; PjM does not.
    assert "## Product knowledge" not in script.calls[0][1]
    # The request's own headings never look like sections, in context or in the file.
    assert "### Why" in script.calls[0][1] and "\n## Why" not in script.calls[0][1]
    assert "\n## Why" not in (tmp_path / ".agentic/tasks/T-1.md").read_text()
    for call in script.calls[1:]:
        assert call[1].startswith("## Product knowledge")
        assert "A business." in call[1] and "Old." in call[1]
        assert "## Original request" in call[1]
    # PM's output is checked against the task type before it counts.
    assert script.checks["pm"] is not None and script.checks["product_analyst"] is None

    assert (tmp_path / "docs/product/reports.md").read_text() == "# Reports\n\nCSV export.\n"
    assert (tmp_path / "docs/product/members.md").read_text() == "# Members\n\nOld.\n"
    glossary = (tmp_path / "CONTEXT.md").read_text()
    assert "**Gym**:\nA business." in glossary
    assert glossary.rstrip().endswith("**Report**:\nA CSV file.\n_Avoid_: export")


async def test_return_writes_nothing(monkeypatch, config, tmp_path):
    script = Script(
        pjm=[classified()],
        product_analyst=[defined(1), defined(2)],
        pm=[verdict("returned", "gap"), verdict("approved", definitions=[])],
    )
    await run_graph(monkeypatch, script, config)
    assert not (tmp_path / "docs/product").exists()
    assert not (tmp_path / "CONTEXT.md").exists()


async def test_models_follow_kind(monkeypatch, config):
    script = Script(pjm=[classified()], product_analyst=[defined()], pm=[verdict("approved")])
    await run_graph(monkeypatch, script, config)
    models = {slug: model for slug, _, _, model in script.calls}
    assert models["pjm"] == models["product_analyst"] == "claude-sonnet-5"
    assert models["pm"] == "claude-opus-5"


async def test_low_confidence_escalates_before_defining(monkeypatch, config):
    script = Script(pjm=[classified(confidence="low")], product_analyst=[], pm=[])
    final = await run_graph(monkeypatch, script, config)
    assert final["status"] == g.WAITING_ON_HUMAN
    assert "PjM could not classify" in final["escalated"]
    assert len(script.calls) == 1


async def test_non_feature_escalates(monkeypatch, config):
    script = Script(pjm=[classified(type_="bug")], product_analyst=[], pm=[])
    final = await run_graph(monkeypatch, script, config)
    assert final["type"] == "bug"
    assert final["status"] == g.WAITING_ON_HUMAN
    assert final["escalated"] == "bug path not implemented yet"
    assert len(script.calls) == 1


async def test_return_loops_back_then_approves(monkeypatch, config):
    script = Script(
        pjm=[classified()],
        product_analyst=[defined(1), defined(2)],
        pm=[verdict("returned", "missing empty state"), verdict("approved")],
    )
    final = await run_graph(monkeypatch, script, config)

    assert final["status"] == "definition_ready"
    assert final["returns"] == {PAIR_PM_PA: 1}
    assert [c[0] for c in script.calls] == ["pjm", "product_analyst", "pm", "product_analyst", "pm"]
    second_define = script.calls[3][1]
    assert "## Product Analyst\n\n### Behaviour\n\nv1" in second_define  # its own previous
    assert "## PM\n\n### Verdict\n\nreturned" in second_define  # PM's full section
    assert "missing empty state" in second_define
    second_validate = script.calls[4][1]
    assert "v2" in second_validate and "### Verdict\n\nreturned" in second_validate
    assert "You have 1 left" in script.calls[4][2]
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
    assert "(v1)" not in script.calls[4][1]


async def test_return_limit_escalates(monkeypatch, config):
    script = Script(
        pjm=[classified()],
        product_analyst=[defined(1), defined(2), defined(3)],
        pm=[verdict("returned"), verdict("returned"), verdict("returned", "still no metrics")],
    )
    final = await run_graph(monkeypatch, script, config)

    assert final["status"] == g.WAITING_ON_HUMAN
    assert final["returns"] == {PAIR_PM_PA: 2}
    assert "hit 2 returns: still no metrics" in final["escalated"]
    assert "You have 0 left" in script.calls[-1][2]
    assert len(script.calls) == 7


async def test_role_failure_escalates_and_keeps_failures(monkeypatch, config, tmp_path):
    failures = [
        {
            "role": "Product Analyst",
            "attempt": 1,
            "error": "no structured output",
            "cost_usd": 0.05,
        },
        {"role": "Product Analyst", "attempt": 2, "error": "empty markdown", "cost_usd": 0.03},
    ]
    script = Script(
        pjm=[classified()], product_analyst=[RoleFailure(PRODUCT_ANALYST, failures)], pm=[]
    )
    final = await run_graph(monkeypatch, script, config)

    assert final["status"] == g.WAITING_ON_HUMAN
    assert final["escalated"].startswith("Product Analyst failed 2 times")
    assert final["failures"] == failures
    assert final["cost_usd"] == pytest.approx(0.01 + 0.05 + 0.03)  # failed attempts still cost
    assert PRODUCT_ANALYST.name not in final["sections"]
    doc = read(tmp_path / ".agentic/tasks/T-1.md")
    assert [f["error"] for f in doc["failures"]] == ["no structured output", "empty markdown"]
    assert doc["runs"] == [
        {"role": "PjM", "version": 1, "session_id": "", "model": "", "cost_usd": 0.01, "turns": 0}
    ]
    assert doc["status"] == g.WAITING_ON_HUMAN


async def test_retried_failure_is_recorded_but_not_escalated(monkeypatch, config):
    retried = defined()
    retried.failures = [
        {"role": "Product Analyst", "attempt": 1, "error": "invalid", "cost_usd": 0.02}
    ]
    script = Script(pjm=[classified()], product_analyst=[retried], pm=[verdict("approved")])
    final = await run_graph(monkeypatch, script, config)
    assert final["status"] == "definition_ready"
    assert final["failures"] == retried.failures
