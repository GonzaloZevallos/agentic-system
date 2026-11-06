"""The `single` arm's shadow through the compiled graph (GH-473): whatever the shadow review
returns, raises or overruns, the state's decision keys equal those of a run with the shadows
stubbed out; only `shadow_reviews`, `shadow_wait_s`, its section and the cost differ."""

import asyncio

import pytest

from agentic.core.process.application import shadow_review
from tests.core.process.adapters.langgraph.test_graph import (
    TASK,
    Fakes,
    MemoryWorkspace,
    compiled,
    script_for,
    technical,
)
from tests.fakes import BlockingRunner, delivered, planned, reviewed

pytestmark = pytest.mark.anyio

SAME = (
    "findings",
    "settled_findings",
    "changes_requested",
    "escalated",
    "auto_merge_decision",
    "runs",
    "failures",
    "tracker_failures",
)


def config(tmp_path, grace=30):
    return {
        "configurable": {
            "product_dir": str(tmp_path),
            "thread_id": "GH-1",
            "review_mode": "single",
            "shadow_grace_s": grace,
        }
    }


def scenario(**queues) -> tuple[BlockingRunner, Fakes]:
    domain = {"id": "domain", "title": "Domain", "brief": "b", "seams": ["x"], "depends_on": []}
    base = script_for(**technical(tech_lead=[planned(domain)]))
    scripted = {
        **base.queues,
        "developer__api": [delivered("api")],
        "developer__domain": [delivered("domain")],
        "tech_lead_review": [reviewed("approved")],  # the integration, on the task branch
        "tech_lead_review__shadow-api": [reviewed("approved")],
        "tech_lead_review__shadow-domain": [reviewed("approved")],
        **queues,
    }
    fakes = Fakes()
    for name in ("api", "domain"):
        fakes.workspace.moved.add(MemoryWorkspace.ROOT / f"GH-1.{name}")
    return BlockingRunner(**scripted), fakes


async def run(tmp_path, monkeypatch, *, shadows: bool, grace=30, hold=None, **queues) -> dict:
    runner, fakes = scenario(**queues)
    if hold:
        runner.hold(hold)
    graph = compiled(runner, fakes=fakes)

    async def alone(state, cfg, ports, main):
        return await main(), shadow_review.ShadowOutcome()

    with monkeypatch.context() as patched:
        if not shadows:
            patched.setattr(shadow_review, "run_shadows", alone)
        return await asyncio.wait_for(graph.ainvoke(TASK, config=config(tmp_path, grace)), 60)


def decision(final: dict) -> dict:
    return {
        **{key: final.get(key) for key in SAME if key != "runs"},
        "runs": sorted((r["role"], r["slug"], r["section"], r["version"]) for r in final["runs"]),
        "statuses": {sid: sub["status"] for sid, sub in final["subtasks"].items()},
        "status": final["status"],
    }


async def test_a_shadow_that_blocks_changes_nothing_but_its_own_record(tmp_path, monkeypatch):
    blocking = reviewed("returned", "the shadow disagrees")
    off = await run(tmp_path / "a", monkeypatch, shadows=False)
    on = await run(
        tmp_path / "b", monkeypatch, shadows=True, **{"tech_lead_review__shadow-api": [blocking]}
    )
    assert decision(on) == decision(off)
    by = {r["subtask"]: r for r in on["shadow_reviews"]}
    assert by["api"]["status"] == "ran" and by["api"]["findings"][0]["severity"] == "blocking"
    assert not off.get("shadow_reviews")
    assert on["cost_usd"] > off["cost_usd"]


async def test_a_shadow_that_raises_is_unavailable_and_changes_nothing(tmp_path, monkeypatch):
    off = await run(tmp_path / "a", monkeypatch, shadows=False)
    on = await run(
        tmp_path / "b",
        monkeypatch,
        shadows=True,
        **{"tech_lead_review__shadow-api": [RuntimeError("boom")]},
    )
    assert decision(on) == decision(off)
    by = {r["subtask"]: r for r in on["shadow_reviews"]}
    assert (by["api"]["status"], by["api"]["cause"]) == ("unavailable", "failure")
    assert by["domain"]["status"] == "ran"


async def test_a_shadow_that_overruns_the_grace_is_late_and_changes_nothing(tmp_path, monkeypatch):
    off = await run(tmp_path / "a", monkeypatch, shadows=False)
    on = await run(
        tmp_path / "b", monkeypatch, shadows=True, grace=0, hold="tech_lead_review__shadow-api"
    )
    assert decision(on) == decision(off)
    by = {r["subtask"]: r for r in on["shadow_reviews"]}
    assert (by["api"]["status"], by["api"]["cause"]) == ("late", "late")
    assert by["api"]["findings"] == []
    assert on["shadow_wait_s"] >= 0
