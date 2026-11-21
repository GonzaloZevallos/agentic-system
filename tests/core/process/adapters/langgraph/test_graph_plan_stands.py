"""The graph's `pause` node (`_pause_node`) restores the plan a plan-stage drawing pause held when
`resume.prepare` rechecked it and the owner's labels add no promotion (GH-1004 part 2), and
composes exactly the patch `prepare` composed. The node runs alone in a one-node graph with a
checkpointer, resumed with `Resumption.answer()` as the CLI resumes it."""

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agentic.core.process.adapters.langgraph.graph import _pause_node
from agentic.core.process.application import resume
from agentic.core.process.application.ports import IssueState
from agentic.core.process.domain import document, drawing_answer, rules
from agentic.core.process.domain.task import TaskState, apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_plan_drawing_gate import LIGHT
from tests.core.process.application.test_resume_plan_stands import (
    ACCEPT,
    AT,
    paused_by_the_plan,
    resuming,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def through_the_node(paused, payload, ports, tmp_path):
    """The state after the `pause` node resumed from `paused` with `payload`."""
    builder = StateGraph(TaskState)
    builder.add_node("pause", _pause_node(lambda _cfg, _config: ports))
    builder.add_edge(START, "pause")
    builder.add_edge("pause", END)
    app = builder.compile(checkpointer=MemorySaver())
    config = {"configurable": {"product_dir": str(tmp_path), "thread_id": paused["id"]}}
    await app.ainvoke(paused, config=config)
    return await app.ainvoke(Command(resume=payload), config=config)


def carries(ours, resumed):
    """`ours` (the graph's state, which also holds a default for every key no node wrote) holds
    every value `resumed` (what `prepare` composed) does."""
    return {k: ours.get(k) for k, v in resumed.items() if v is not None} == {
        k: v for k, v in resumed.items() if v is not None
    }


async def parity(paused, cfg, tmp_path, comment=ACCEPT):
    """`prepare`'s resumed state and the node's, from the same paused state and ports."""
    r = resume.prepare("GH-12", paused, "checkpoint", cfg, resuming(comment))
    ours = await through_the_node(paused, r.answer(), resuming(comment), tmp_path)
    return r, ours


async def test_the_node_composes_the_patch_prepare_composed(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    r, ours = await parity(paused, cfg, tmp_path)

    assert r.plan_recheck is True
    assert ours["status"] == "planned" and not ours.get("plan_due") and ours["plan_held"] is None
    assert carries(ours, r.resumed)


async def test_the_node_agrees_with_prepare_on_a_state_rendered_and_loaded_through_the_document(
    cfg, tmp_path
):
    paused = await paused_by_the_plan(tmp_path)
    loaded = resume.state_from(document.parse(document.render(paused)))
    r, ours = await parity(loaded, cfg, tmp_path)

    assert r.plan_recheck is True and r.target == "integrate"
    assert carries(ours, r.resumed)


async def test_the_node_agrees_with_prepare_on_a_state_that_never_carried_publish_owed(
    cfg, tmp_path
):
    paused = await paused_by_the_plan(tmp_path)
    assert "publish_owed" not in paused
    r, ours = await parity(paused, cfg, tmp_path)
    assert "publish_owed" not in ours or ours["publish_owed"] is None
    assert carries(ours, r.resumed)


async def test_the_node_agrees_with_prepare_on_a_resume_that_replans(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    r, ours = await parity(paused, cfg, tmp_path, comment="Drawing 1: reject")

    assert r.plan_recheck is False and ours["plan_due"] is True
    assert carries(ours, r.resumed)


async def test_a_payload_without_plan_recheck_plans_again_as_today(cfg, tmp_path):
    paused = await paused_by_the_plan(tmp_path)
    r = resume.prepare("GH-12", paused, "checkpoint", cfg, resuming(ACCEPT))
    payload = {k: v for k, v in r.answer().items() if k != "plan_recheck"}
    ours = await through_the_node(paused, payload, resuming(ACCEPT), tmp_path)

    assert ours["plan_due"] is True and ours["plan_held"] is None
    assert not ours.get("subtasks") and rules.resume_target(ours) == "plan"


async def test_a_round_the_node_does_not_hold_gives_today_s_patch(cfg, tmp_path):
    """`prepare` rechecked the plan, but the state the node holds is of another round."""
    paused = await paused_by_the_plan(tmp_path)
    r = resume.prepare("GH-12", paused, "checkpoint", cfg, resuming(ACCEPT))
    held = paused["plan_held"]
    other = {**paused, "plan_held": {**held, "round": held["round"] + 1}}
    assert not drawing_answer.plan_stands_pre(other, r.drawing_answers, r.decision)

    ours = await through_the_node(other, r.answer(), resuming(ACCEPT), tmp_path)
    assert ours["plan_due"] is True and ours["plan_held"] is None and not ours.get("subtasks")


async def test_an_owner_label_that_promotes_a_light_task_gives_today_s_patch_plus_the_promotion(
    cfg, tmp_path
):
    paused = await paused_by_the_plan(tmp_path, base=LIGHT)
    r = resume.prepare("GH-12", paused, "checkpoint", cfg, resuming(ACCEPT))
    assert r.plan_recheck is True  # prepare does not read the labels: the node refuses

    ports = resuming(ACCEPT)
    ports.tracker.states[12] = IssueState("OPEN", "", (), None, labels=("path:full",))
    ours = await through_the_node(paused, r.answer(), ports, tmp_path)

    today = drawing_answer.resume_patch(
        paused, r.decision, None, r.drawing_answers, at=AT, plan_recheck=False
    )
    assert ours["plan_due"] is True and ours["plan_held"] is None and not ours.get("subtasks")
    assert ours["path"] == "full" and ours["promotion"]["from_role"] == "owner"
    assert ours["status"] == apply(paused, today)["status"]
