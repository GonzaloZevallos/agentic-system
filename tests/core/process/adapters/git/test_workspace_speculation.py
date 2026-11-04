"""GH-465: the head a review publishes ahead of `full_checks` is the head `full_checks` reads,
on a real repository. The final unit is reviewed in the task checkout; the fan-in merge of
the approved subtask branches, offered again by a later wave, must leave that head where it
was, or the speculated sha would never be the approved one and the run would be paid for in
vain. Three shapes: two subtask branches, a single subtask, an integrator commit on top."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application.integrate import _merge_approved
from agentic.core.process.domain import speculation
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity
TASK = "GH-9"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class World:
    def __init__(self, root: Path) -> None:
        self.repo = root / "product"
        self.repo.mkdir()
        git("init", "-q", "-b", "master", cwd=self.repo)
        (self.repo / "base.txt").write_text("base\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "v0", cwd=self.repo)
        self.workspace = GitWorkspace(self.repo, IDENTITY)
        self.task = self.workspace.task_checkout(TASK)
        self.ports = replace(memory_ports(), workspace=self.workspace)

    def subtask(self, name: str) -> dict:
        """A subtask branch with one commit of its own, approved."""
        made = self.workspace.subtask_checkout(TASK, name)
        (made.path / f"{name}.txt").write_text(f"{name}\n")
        git("add", "-A", cwd=made.path)
        git("commit", "-qm", name, cwd=made.path)
        return {"id": name, "status": "approved", "branch": made.branch}

    def state(self, *subtasks: dict) -> dict:
        return {"id": TASK, "subtasks": {s["id"]: s for s in subtasks}}

    def merged(self, state: dict) -> list[str]:
        return _merge_approved(state, self.task.path, self.ports)

    def head(self) -> str:
        return self.workspace.full_head(self.task.path)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _speculated(head: str) -> dict:
    return {"id": TASK, "subtasks": {}, "ci_speculation": {"head": head, "pushed_at": "x"}}


def test_two_approved_branches_merged_again_leave_the_head_where_it_was(world):
    state = world.state(world.subtask("api"), world.subtask("web"))
    assert world.merged(state) == []
    reviewed = world.head()
    assert len(reviewed) == 40
    assert (world.task.path / "api.txt").exists() and (world.task.path / "web.txt").exists()

    assert world.merged(state) == []

    assert world.head() == reviewed
    assert speculation.consumable(_speculated(reviewed), world.head())


def test_a_single_subtask_developed_in_the_task_checkout_keeps_its_head(world):
    (world.task.path / "api.txt").write_text("api\n")
    git("add", "-A", cwd=world.task.path)
    git("commit", "-qm", "api", cwd=world.task.path)
    reviewed = world.head()
    behind = {"id": "api", "status": "approved", "branch": "agentic/GH-9.api"}
    git("branch", "agentic/GH-9.api", "agentic/GH-9~1", cwd=world.repo)

    assert world.merged(world.state(behind)) == []

    assert world.head() == reviewed


def test_an_integrator_commit_on_top_survives_the_next_fan_in_merge(world):
    state = world.state(world.subtask("api"), world.subtask("web"))
    world.merged(state)
    (world.task.path / "glue.txt").write_text("glue\n")
    git("add", "-A", cwd=world.task.path)
    git("commit", "-qm", "integrator", cwd=world.task.path)
    reviewed = world.head()

    assert world.merged(state) == []

    assert world.head() == reviewed


def test_a_head_that_moved_after_the_push_is_not_consumable(world):
    state = world.state(world.subtask("api"))
    world.merged(state)
    pushed = world.head()
    (world.task.path / "late.txt").write_text("late\n")
    git("add", "-A", cwd=world.task.path)
    git("commit", "-qm", "late", cwd=world.task.path)

    assert not speculation.consumable(_speculated(pushed), world.head())
