"""The checkpointer the CLI opens: a real SQLite file in the product (ADR-0003)."""

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agentic import composition
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
