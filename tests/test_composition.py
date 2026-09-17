"""The checkpointer the CLI opens: a real SQLite file in the product (ADR-0003)."""

import subprocess

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agentic import composition
from agentic.core.process.application import preflight
from agentic.core.process.application.preflight import RunsOnItsOwnCheckout
from agentic.core.process.domain.task import TaskState
from agentic.shared.configuration import Configuration

pytestmark = pytest.mark.anyio


async def test_a_pause_survives_the_process_in_the_products_state_file(tmp_path):
    # `TaskState` is plain JSON, so the saver keeps it whole; a second connection, as a
    # later `agentic resume` would open, finds the pause and resumes it.
    def pause(state: TaskState) -> TaskState:
        answer = interrupt({"reason": state.get("escalated")})
        return {"escalated": None, "sections": {"Human": answer["decision"]}}

    graph = StateGraph(TaskState)
    graph.add_node("pause", pause)
    graph.add_edge(START, "pause")
    graph.add_edge("pause", END)
    cfg = Configuration(product_dir=tmp_path)
    config = {"configurable": {"thread_id": "GH-1"}}
    state = {"id": "GH-1", "title": "t", "description": "d", "escalated": "cap", "cost_usd": 1.5}

    async with composition.checkpointer(cfg) as saver:
        await graph.compile(checkpointer=saver).ainvoke(state, config)
    assert cfg.state_file == tmp_path / ".agentic/state.sqlite" and cfg.state_file.is_file()
    async with composition.checkpointer(cfg) as saver:
        app = graph.compile(checkpointer=saver)
        assert [i.value for i in (await app.aget_state(config)).interrupts] == [{"reason": "cap"}]
        final = await app.ainvoke(Command(resume={"decision": "go"}), config)
    assert final["escalated"] is None and final["sections"] == {"Human": "go"}
    assert final["cost_usd"] == 1.5


def a_repository(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_the_ports_of_a_product_in_the_running_repository_are_refused(tmp_path, monkeypatch):
    """The guard cannot live in the CLI alone: `langgraph dev`, Studio and the LangGraph
    API import the wired graph and reach `default_ports` without ever passing there
    (issue #44). This is the door they all share."""
    repo = a_repository(tmp_path / "system")
    monkeypatch.setattr(preflight, "CHECKOUT", repo)
    with pytest.raises(RunsOnItsOwnCheckout):
        composition.default_ports(Configuration(product_dir=repo))


def test_the_ports_of_a_product_of_its_own_are_built(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    assert composition.default_ports(Configuration(product_dir=product)).documents.product_dir
