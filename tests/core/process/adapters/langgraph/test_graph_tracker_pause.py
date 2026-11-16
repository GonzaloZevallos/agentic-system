"""The graph's `pause` node on a pause over typed tracker items (GH-620): only a non-blank
decision resumes it, through the compiled graph with a checkpointer."""

import pytest
from langgraph.types import Command

from agentic.core.process.domain import tracker_items
from agentic.core.process.domain.landing import waits_on_human
from tests.core.process.adapters.langgraph.test_graph import (
    TASK,
    Fakes,
    compiled,
    script_for,
    technical,
)
from tests.core.process.domain.test_landing import _spent
from tests.fakes import planned

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


async def test_a_plan_with_one_human_item_escalates_with_needs_human_through_deliver(config):
    """GH-663, GH-784: the Tech Lead's plan writes the `human` item and the plan node raises it as
    a `questions` escalation before anything is built: no pull request, no Ready comment and no
    'Tracker work for you' comment, the task not the landing head."""
    item = {"kind": "human", "question": "Close #12 as superseded?", "recommendation": "Close."}
    fakes = Fakes()
    script = script_for(**technical(tech_lead=[planned(tracker_items=[item])]))
    app = compiled(script, fakes=fakes)

    paused = await app.ainvoke(TASK, config=config)
    snapshot = await app.aget_state(config)
    state = snapshot.values
    comments = [text for _, text in fakes.tracker.comments]

    assert "pull_request" not in paused and "pull_request" not in state
    assert fakes.tracker.pull_requests == []
    assert not any(c.startswith("Ready for review") for c in comments)
    assert state["status"] == "waiting_on_human"
    assert state["escalation_class"] == "questions"
    assert state["tracker_human"] == [tracker_items.item_hash(item)]
    assert ("GH-1", "needs-human") in fakes.tracker.labels
    assert not [c for c in comments if c.startswith("Tracker work for you")]
    assert any("Close #12 as superseded?" in c for c in comments)
    assert waits_on_human(
        state,
        None,
        None,
        spent=_spent(),
        stopped=False,
        needs_human=False,
        stranded=False,
    )


async def _drawing_paused(config):
    from agentic.core.process.domain import drawing, drawing_answer

    departures = [
        drawing.Departure("design/home.png", e, "blue", "green", "keep", "approved", "dev")
        for e in ("hero", "footer")
    ]
    base = {"id": "GH-1", "title": "t", "description": "d", "type": "feature"}
    patch = drawing_answer.escalation_patch(base, departures, "T0")  # type: ignore[arg-type]
    state = {**base, **patch, "status": "waiting_on_human"}
    app = compiled(script_for(), fakes=Fakes())
    await app.aupdate_state(config, state, as_node="escalate")
    return app, state


async def test_a_drawing_answer_from_prepare_reaches_the_state_through_the_pause_node(config):
    from agentic.core.process.application import resume
    from agentic.core.process.domain import drawing_answer
    from agentic.shared.configuration import Configuration
    from tests.fakes import memory_ports

    app, state = await _drawing_paused(config)
    ports = memory_ports()
    ports.tracker.earlier = ["Escalated: x", "Drawing 1: accept", "Drawing 2: reject"]
    r = resume.prepare(
        "GH-1",
        state,
        "checkpoint",
        Configuration(product_dir="/tmp/p"),
        ports,  # type: ignore[arg-type]
    )
    async for update in app.astream(
        Command(resume=r.answer()), config=config, stream_mode="updates"
    ):
        if "pause" in update:
            break  # what runs after the pause is not what this test is about
    final = (await app.aget_state(config)).values
    assert drawing_answer.unanswered(final) == []
    assert {x["number"]: x["answer"]["by"] for x in final["drawing_departures"]} == {
        1: "comment",
        2: "comment",
    }


async def test_a_plain_resume_has_no_drawing_departures_key(config):
    from agentic.core.process.application import resume

    assert (
        "drawing_departures"
        not in resume.Resumption("GH-1", "checkpoint", {}, {}, "x", "plan", 1.0).answer()
    )
