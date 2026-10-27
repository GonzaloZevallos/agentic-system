"""The technical stage as a loop through the compiled graph (GH-364): a subtask starts as
soon as its dependencies are approved and a delivery is reviewed as soon as it is made,
whatever the siblings are doing. The roles block on an event per subtask (`BlockingRunner`),
so each test says which role of which subtask is still running while another one starts."""

import asyncio
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.domain.roles import FINDINGS
from agentic.core.process.domain.task import WAITING_ON_HUMAN
from tests.core.process.adapters.langgraph.test_graph import (
    TASK,
    Fakes,
    MemoryWorkspace,
    compiled,
    saved,
    script_for,
    technical,
)
from tests.fakes import BlockingRunner, delivered, planned, reviewed

pytestmark = pytest.mark.anyio


@pytest.fixture
def config(tmp_path):
    return {"configurable": {"product_dir": str(tmp_path), "thread_id": "GH-1"}}


def subtask(name: str, *depends_on: str) -> dict:
    return {
        "id": name,
        "title": name.title(),
        "brief": "b",
        "seams": ["x"],
        "depends_on": list(depends_on),
    }


def blocking(*others: dict, **queues) -> tuple[BlockingRunner, Fakes]:
    """A runner with the definition stage scripted and the technical stage of `api` plus
    `others`, each approved first time unless `queues` says otherwise."""
    base = script_for(**technical(tech_lead=[planned(*others)]))
    names = ["api", *(o["id"] for o in others)]
    scripted = {
        **base.queues,
        **{f"developer__{n}": [delivered(n)] for n in names},
        **{f"tech_lead_review__{n}": [reviewed("approved")] for n in names},
        "tech_lead_review": [reviewed("approved")],  # the integration, on the task branch
        **queues,
    }
    fakes = Fakes()
    for n in names:
        fakes.workspace.moved.add(MemoryWorkspace.ROOT / f"GH-1.{n}")
    return BlockingRunner(**scripted), fakes


def start(runner: BlockingRunner, fakes: Fakes, config):
    graph = compiled(runner, fakes=fakes)
    return asyncio.create_task(graph.ainvoke(TASK, config=config))


async def test_a_dependent_starts_when_its_dependency_is_approved_while_a_sibling_builds(config):
    runner, fakes = blocking(subtask("domain"), subtask("ui", "api"))
    runner.hold("developer__domain")
    run = start(runner, fakes, config)

    await runner.started("developer__ui")  # api approved, domain still in develop
    assert "developer__domain" in runner.began
    assert "developer__domain" not in runner.finished
    runner.release("developer__domain")
    final = await asyncio.wait_for(run, 20)

    assert final["status"] == "ready_for_review"
    assert {s["status"] for s in final["subtasks"].values()} == {"approved"}


async def test_a_dependent_starts_while_a_sibling_is_in_review(config):
    runner, fakes = blocking(subtask("domain"), subtask("ui", "api"))
    runner.hold("tech_lead_review__domain")
    run = start(runner, fakes, config)

    await runner.started("developer__ui")
    await runner.started("tech_lead_review__domain")  # delivered, being reviewed meanwhile
    assert "tech_lead_review__domain" not in runner.finished
    runner.release("tech_lead_review__domain")
    final = await asyncio.wait_for(run, 20)

    assert final["status"] == "ready_for_review"


async def test_a_delivery_is_reviewed_while_its_sibling_is_still_building(config):
    runner, fakes = blocking(subtask("domain"))
    runner.hold("developer__domain")
    run = start(runner, fakes, config)

    await runner.started("tech_lead_review__api")
    assert "developer__domain" in runner.began
    assert "developer__domain" not in runner.finished
    runner.release("developer__domain")
    final = await asyncio.wait_for(run, 20)

    assert final["status"] == "ready_for_review"


async def test_a_returned_subtask_restarts_after_the_turn_wrote_the_findings(config, tmp_path):
    runner, fakes = blocking(
        subtask("domain"),
        developer__domain=[delivered("domain"), delivered("domain again")],
        tech_lead_review__domain=[
            reviewed("returned", "no test at the seam"),
            reviewed("approved"),
        ],
    )
    seen: list[str] = []

    def on_call(key: str) -> None:
        if key == "developer__domain" and runner.began.count(key) == 2:
            seen.append(
                saved(FilesystemTaskDocuments(tmp_path)).get("sections", {}).get(FINDINGS, "")
            )

    runner.on_call = on_call
    final = await asyncio.wait_for(start(runner, fakes, config), 30)

    assert final["status"] == "ready_for_review"
    assert runner.began.count("developer__domain") == 2
    # The second build began after the turn folded the return's findings into the table.
    assert len(seen) == 1 and "no test at the seam" in seen[0]
    assert final["changes_requested"] == {"Tech Lead→Developer:domain": 1}


async def test_an_escalation_stops_new_starts_drains_the_siblings_and_tells_once(config):
    runner, fakes = blocking(subtask("domain"), subtask("ui", "api"))
    fakes.workspace.dirty.add(MemoryWorkspace.ROOT / "GH-1.domain")
    runner.queues["developer__domain"] = [delivered(), delivered()]
    runner.hold("tech_lead_review__api")
    run = start(runner, fakes, config)

    await runner.started("tech_lead_review__api")
    documents = FilesystemTaskDocuments(Path(config["configurable"]["product_dir"]))
    while not saved(documents).get("escalated"):  # domain failed twice
        await asyncio.sleep(0.01)
    for _ in range(50):  # let its branch end and the loop fold the escalation in
        await asyncio.sleep(0)
    runner.release("tech_lead_review__api")  # the sibling in flight is drained, not killed
    final = await asyncio.wait_for(run, 20)

    assert final["status"] == WAITING_ON_HUMAN
    assert final["escalated"].startswith("Developer failed 2 times")
    assert final["subtasks"]["api"]["status"] == "approved"  # the drained review counted
    assert "developer__ui" not in runner.began  # api's dependent never started
    assert final["subtasks"]["ui"]["status"] == "pending"
    assert len(fakes.tracker.comments) == 1  # the human is told once


async def test_the_bound_on_parallel_subtasks_is_never_exceeded(config):
    config["configurable"]["max_parallel_subtasks"] = 2
    runner, fakes = blocking(subtask("b"), subtask("c"), subtask("d"))
    final = await asyncio.wait_for(start(runner, fakes, config), 30)

    assert final["status"] == "ready_for_review"
    assert runner.peak == 2  # parallel, and never more than the bound


async def test_a_delivered_review_takes_its_role_slot_without_waiting_under_the_default_bound(
    config,
):
    # Two role slots and no explicit bound: two subtasks in flight, so a review never
    # waits behind a third Developer.
    config["configurable"]["max_concurrent_roles"] = 2
    runner, fakes = blocking(subtask("b"), subtask("c"))
    runner.hold("developer__b")
    run = start(runner, fakes, config)

    await runner.started("developer__b")
    await runner.started("tech_lead_review__api")  # api delivered, b building: 2 slots, both used
    assert "developer__c" not in runner.began  # the bound of two holds the third back
    runner.release("developer__b")
    final = await asyncio.wait_for(run, 30)

    assert final["status"] == "ready_for_review"
    assert runner.peak == 2
