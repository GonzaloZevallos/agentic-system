"""An evaluation task through the compiled graph with fake ports (GH-470, part 2): it ends
`evaluated`, with no tracker write and no push, even for a stack declaring `ci:`, and an
evaluation that escalates writes nothing either. The ports are wrapped by
`composition.evaluation_ports`, the one wiring `default_ports` applies to an evaluation root."""

from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import composition
from agentic.composition import default_ports, evaluation_ports
from agentic.core.process.adapters.evaluation import ClosedInbox
from agentic.core.process.adapters.langgraph.graph import build_graph
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.ports import AttemptFailed
from tests.core.process.adapters.langgraph.test_graph import (
    Fakes,
    MemoryWorkspace,
    _Rooted,
    script_for,
    technical,
)
from tests.fakes import MemoryCommands, MemorySampleApp, MemoryStack, MemoryTracker

pytestmark = pytest.mark.anyio

EVAL_ID = "EVAL-1-aaaaaaaa-default-1"
TASK = {
    "id": EVAL_ID,
    "title": "Export report",
    "description": "Users want CSV.",
    "evaluation": {"issue": 1, "base": "a" * 40, "ci": "local"},
}


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": EVAL_ID}}


def build(script, fakes):
    """The graph whose ports are the fakes, wrapped the way an evaluation root wraps them.
    Returns the graph and the ports each node was given."""
    fakes.workspace.moved.update(
        {MemoryWorkspace.ROOT / EVAL_ID, MemoryWorkspace.ROOT / f"{EVAL_ID}.api"}
    )
    built = []

    def ports_for(cfg):
        real = default_ports(cfg)
        knowledge = real.knowledge
        knowledge.product_dir = cfg.product_dir
        ports = evaluation_ports(
            replace(
                real,
                runner=script,
                workspace=fakes.workspace,
                tracker=fakes.tracker,
                knowledge=_Rooted(knowledge, cfg.product_dir),
                stack=MemoryStack(ci=("tests",)),
                commands=MemoryCommands(),
                sample_app=MemorySampleApp(),
                check_results=None,
                # A mirror that the wiring must take away.
                board=object(),  # type: ignore[arg-type]
            ),
            ClosedInbox(),
        )
        built.append(ports)
        return ports

    graph = build_graph(ports_for, NoTelemetry(), arm_for=composition._arm_for)
    return graph.compile(checkpointer=InMemorySaver()), built


def untouched(real_tracker, real_workspace):
    assert real_workspace.pushed == []
    assert (real_tracker.pull_requests, real_tracker.comments, real_tracker.labels) == ([], [], [])
    assert real_tracker.unlabeled == []
    assert real_tracker.opened_issues == []
    assert real_tracker.commented_issues == []


async def test_an_evaluation_task_ends_evaluated_with_zero_tracker_writes_and_zero_pushes(
    config,
):
    fakes = Fakes()
    real_tracker, real_workspace = MemoryTracker(), fakes.workspace
    fakes.tracker = real_tracker
    graph, built = build(script_for(**technical()), fakes)

    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "evaluated"
    untouched(real_tracker, real_workspace)
    assert built and all(ports.board is None for ports in built)


async def test_an_evaluation_task_that_escalates_also_writes_nothing(config):
    fakes = Fakes()
    real_tracker, real_workspace = MemoryTracker(), fakes.workspace
    fakes.tracker = real_tracker
    script = script_for(
        product_analyst=[AttemptFailed("no structured output"), AttemptFailed("empty markdown")]
    )
    graph, _ = build(script, fakes)

    final = await graph.ainvoke(TASK, config=config)

    assert final["status"] == "waiting_on_human"
    assert final["escalated"]
    untouched(real_tracker, real_workspace)
