"""An evaluation task through the compiled graph with fake ports (GH-470, part 2): it ends
`evaluated`, with no tracker write and no push, even for a stack declaring `ci:`."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic.core.process.adapters.evaluation import (
    EvaluationStack,
    EvaluationTracker,
    EvaluationWorkspace,
)
from tests.core.process.adapters.langgraph.test_graph import (
    Fakes,
    compiled,
    script_for,
    technical,
)
from tests.fakes import MemoryStack, MemoryTracker

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-1",
    "title": "Export report",
    "description": "Users want CSV.",
    "evaluation": {"issue": 1, "base": "a" * 40, "ci": "local"},
}


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


async def test_an_evaluation_task_ends_evaluated_with_zero_tracker_writes_and_zero_pushes(
    config,
):
    fakes = Fakes()
    real_tracker, real_workspace = MemoryTracker(), fakes.workspace
    stack = EvaluationStack(MemoryStack(ci=("tests",)))
    graph = compiled(script_for(**technical()), fakes=fakes, stack=stack, saver=InMemorySaver())
    # The ports are built per node, so wrapping after the graph is compiled still wires them.
    fakes.tracker = EvaluationTracker(real_tracker)
    fakes.workspace = EvaluationWorkspace(real_workspace)

    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "evaluated"
    assert real_workspace.pushed == []
    assert (real_tracker.pull_requests, real_tracker.comments, real_tracker.labels) == ([], [], [])
    assert real_tracker.opened_issues == []
