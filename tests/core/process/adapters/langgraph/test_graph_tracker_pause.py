"""The graph's `pause` node on a pause over typed tracker items (GH-620): only a non-blank
decision resumes it, through the compiled graph with a checkpointer."""

import pytest
from langgraph.types import Command

from tests.core.process.adapters.langgraph.test_graph import Fakes, compiled, script_for

pytestmark = pytest.mark.anyio

HASH = "abc123abc123"
PAUSED = {
    "id": "GH-1",
    "title": "t",
    "description": "d",
    "type": "feature",
    "status": "waiting_on_human",
    "pull_request": "https://github.com/o/r/pull/7",
    "escalated": "the plan leaves 1 decision(s) to the owner",
    "escalation_class": "questions",
    "questions_for_human": ["Ship it? — Recommended: Yes"],
    "tracker_human": [HASH],
    "tracker_actions_done": {HASH: {"at": "2026-01-01T00:00:00+00:00"}},
}


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


async def _paused(config):
    app = compiled(script_for(), fakes=Fakes())
    await app.aupdate_state(config, PAUSED, as_node="escalate")
    return app


@pytest.mark.parametrize("answer", [None, "  "])
async def test_a_blank_decision_interrupts_again_and_keeps_the_items_pending(config, answer):
    app = await _paused(config)
    await app.ainvoke(Command(resume={"decision": answer}), config=config)
    snapshot = await app.aget_state(config)
    assert snapshot.interrupts
    assert snapshot.values["tracker_human"] == [HASH]
    assert snapshot.values["status"] == "waiting_on_human"


async def test_a_real_decision_records_the_answer_and_does_not_escalate_again(config):
    app = await _paused(config)
    final = await app.ainvoke(Command(resume={"decision": "Yes, ship it"}), config=config)
    assert final["tracker_human"] == []
    assert "answered" in final["tracker_actions_done"][HASH]
    assert not final.get("escalated")
