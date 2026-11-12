"""The Developer dispatch finishes a clean restart (GH-593): the archived attempt's branch is
renamed, the checkout is made anew, `restart_pending` is cleared, and the prompt is built from the
rewritten brief alone. Seam: `application.develop.develop` (the Developer dispatch node) over the
in-memory ports, with the patch a real review writes (`restart.restart_patch`)."""

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.domain import restart, rules
from agentic.core.process.domain.roles import pair_for
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryWorkspace, ScriptedRunner, delivered, memory_ports

pytestmark = pytest.mark.anyio

PAIR = pair_for("api")
ARCHIVED = "archive/restart1-abc1234/agentic/GH-9.api"
CFG = Configuration(product_dir=MemoryWorkspace.ROOT)


def subtask(name: str, **more) -> dict:
    return {
        "id": name,
        "title": name,
        "brief": f"build {name}",
        "seams": ["s"],
        "depends_on": [],
        "status": "returned",
        "branch": f"agentic/GH-9.{name}",
        "base": "base1",
        "reviewed_head": "r" * 40,
        "returned_by": "review",
        **more,
    }


def restarted(*, single: bool = False) -> dict:
    """A task whose `api` was just restarted by the review: the state a real review leaves."""
    before = {
        "id": "GH-9",
        "title": "T",
        "description": "d",
        "type": "feature",
        "system_commit": "abc1234",
        "sections": {
            "PM": "### Consolidated definition\n\nx",
            "Developer: api": "OLD DELIVERY of the archived attempt",
            "Tech Lead: review api": "OLD REVIEW of the archived attempt",
        },
        "runs": [
            {
                "role": "Developer",
                "section": "Developer: api",
                "version": 1,
                "session_id": "old-session",
                "cost_usd": 0.1,
            }
        ],
        "subtasks": {"api": subtask("api")}
        if single
        else {"api": subtask("api"), "db": subtask("db", status="approved")},
        "subtask_id": "api",
    }
    if single:
        before["subtasks"]["api"]["branch"] = "agentic/GH-9"
    patch = restart.restart_patch(before, "api", PAIR, "REWRITTEN BRIEF", "r" * 40)
    return apply(before, patch)


class Journal(MemoryWorkspace):
    """Records, in order, the verbs the dispatch calls on the workspace."""

    def __init__(self) -> None:
        super().__init__()
        self.order: list[str] = []

    def rename_branch(self, old: str, new: str) -> None:
        self.order.append(f"rename:{old}")
        super().rename_branch(old, new)

    def subtask_checkout(self, task_id: str, subtask_id: str):
        self.order.append(f"checkout:{subtask_id}")
        return super().subtask_checkout(task_id, subtask_id)


def ports_for(state: dict, runner: ScriptedRunner, branch: str = "agentic/GH-9.api"):
    workspace = Journal()
    workspace.existing_branches[branch] = ("h2", "base1")
    # The Developer's commit, simulated: the checkout the dispatch makes is the one that moves.
    workspace.moved.add(MemoryWorkspace.ROOT / branch.removeprefix("agentic/"))
    ports = memory_ports(runner, workspace=workspace)
    ports.documents.save(state)
    return ports


async def test_the_dispatch_archives_the_attempt_then_makes_the_checkout_and_clears_the_pending():
    state = restarted()
    runner = ScriptedRunner(developer=[delivered()])
    ports = ports_for(state, runner)

    patch = await develop(state, CFG, ports)

    assert ports.workspace.order == ["rename:agentic/GH-9.api", "checkout:api"]
    assert ports.workspace.renamed == [("agentic/GH-9.api", ARCHIVED)]
    assert patch["restart_pending"] is None
    assert patch["subtasks"]["api"]["status"] == "delivered"


async def test_the_restarted_developer_reads_only_the_rewritten_brief_and_resumes_no_session():
    state = restarted()
    assert rules.return_sections(state, "api") == ("Restart api",)
    runner = ScriptedRunner(developer=[delivered()])

    await develop(state, CFG, ports_for(state, runner))

    [call] = runner.calls
    assert "REWRITTEN BRIEF" in call["prompt"]
    assert "OLD DELIVERY" not in call["prompt"] and "OLD REVIEW" not in call["prompt"]
    assert not call.get("resume")


async def test_a_dispatch_after_the_rename_only_makes_the_checkout():
    state = restarted()
    runner = ScriptedRunner(developer=[delivered()])
    ports = ports_for(state, runner)
    ports.workspace.rename_branch("agentic/GH-9.api", ARCHIVED)  # cut right after the rename
    ports.workspace.order.clear()
    ports.workspace.renamed.clear()

    patch = await develop(state, CFG, ports)

    assert ports.workspace.order == ["checkout:api"]
    assert ports.workspace.renamed == []
    assert patch["restart_pending"] is None


async def test_a_dispatch_retried_after_the_branch_was_recreated_never_archives_the_new_one():
    state = restarted()
    runner = ScriptedRunner(developer=[delivered()])
    ports = ports_for(state, runner)
    ports.workspace.existing_branches[ARCHIVED] = ("h2", "base1")  # archived by the first try
    ports.workspace.existing_branches["agentic/GH-9.api"] = ("fresh", "base1")  # and recreated

    await develop(state, CFG, ports)

    assert ports.workspace.renamed == []


async def test_in_a_task_of_one_subtask_the_task_branch_is_the_one_archived_and_recreated():
    state = restarted(single=True)
    runner = ScriptedRunner(developer=[delivered()])
    ports = ports_for(state, runner, branch="agentic/GH-9")

    patch = await develop(state, CFG, ports)

    assert ports.workspace.renamed == [("agentic/GH-9", "archive/restart1-abc1234/agentic/GH-9")]
    assert patch["restart_pending"] is None


async def test_an_ordinary_dispatch_archives_nothing():
    state = restarted()
    state.pop("restart_pending")
    state["subtasks"]["api"] = subtask("api", status="pending")
    runner = ScriptedRunner(developer=[delivered()])
    ports = ports_for(state, runner)

    patch = await develop(state, CFG, ports)

    assert ports.workspace.renamed == [] and "restart_pending" not in patch
