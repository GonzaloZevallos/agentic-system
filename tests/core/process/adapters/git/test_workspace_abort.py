"""GH-267, subtask `abort`: a landing step never leaves a merge in progress in the task
worktree. The incident (apostrophe GH-131) was `integrate._merge_approved` meeting the
conflicted master merge a `sync_master` left behind: git refused, `GitWorkspace.merge`
aborted, and the master merge was thrown away unseen. These tests run on a real repository,
a bare remote and a task worktree."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application import landing_sync
from agentic.core.process.application.integrate import _merge_approved
from agentic.core.process.application.landing_plan import LandingContext
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import landing
from agentic.core.process.domain.landing import LandingStep
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class World:
    """A product whose master and task branch both changed `shared.txt`, a task worktree,
    and an approved subtask branch the task branch has not merged yet."""

    def __init__(self, root: Path) -> None:
        self.repo = root / "product"
        self.repo.mkdir()
        git("init", "-q", "-b", "master", cwd=self.repo)
        (self.repo / "shared.txt").write_text("base\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "v0", cwd=self.repo)
        self.workspace = GitWorkspace(self.repo, IDENTITY)
        self.task = self.workspace.task_checkout("GH-9")
        (self.task.path / "shared.txt").write_text("task\n")
        git("commit", "-qam", "task", cwd=self.task.path)
        git("branch", "agentic/GH-9.api", "agentic/GH-9", cwd=self.repo)
        (self.repo / "shared.txt").write_text("master\n")
        git("commit", "-qam", "master", cwd=self.repo)
        self.master = git("rev-parse", "HEAD", cwd=self.repo)

    @property
    def merge_head(self) -> str | None:
        return self.workspace.merge_head(self.task.path)

    def conflicted_master_merge(self) -> None:
        """What `sync_master` used to leave behind: master merged, conflicts kept."""
        outcome = self.workspace.merge_upstream_into(self.task.path, self.master)
        assert outcome.conflicted == ("shared.txt",)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _approved_state(world: World) -> dict:
    return {
        "subtasks": {
            "api": {"id": "api", "status": "approved", "branch": "agentic/GH-9.api"},
        }
    }


def _ports(world: World):
    return replace(memory_ports(), workspace=world.workspace)


def test_a_merge_left_in_progress_is_thrown_away_by_the_fan_in_merge(world):
    """(i) The incident hazard, on the current code: the abort the fan-in does on its own
    refusal takes the master merge with it."""
    world.conflicted_master_merge()
    assert world.merge_head == world.master

    _merge_approved(_approved_state(world), world.task.path, _ports(world))

    assert world.merge_head is None  # the master merge is gone, and nobody was told


def test_a_cleared_sync_leaves_no_merge_and_the_head_where_it_was(world):
    """(ii) The fixed conflict path: the merge is aborted, the branch did not move."""
    head = world.workspace.full_head(world.task.path)
    with landing_sync.merges_cleared(
        world.workspace, world.task.path, lambda sha: sha == world.master
    ) as merges:
        merged = merges.merge_in(world.master)
        assert not merged.clean and world.merge_head == world.master

    assert world.merge_head is None
    assert world.workspace.full_head(world.task.path) == head
    assert world.workspace.is_clean(world.task.path)


def test_the_state_a_cleared_sync_leaves_merges_approved_branches_as_no_ops(world):
    """(iii) The fan-in finds a clean tree and reports no conflict."""
    with landing_sync.merges_cleared(world.workspace, world.task.path, lambda sha: True) as merges:
        merges.merge_in(world.master)

    conflicts = _merge_approved(_approved_state(world), world.task.path, _ports(world))

    assert conflicts == []


def test_a_stale_system_merge_is_cleared_before_the_pass_starts(world):
    """(iv) A SIGKILL between the merge and its abort left MERGE_HEAD: the next pass clears
    it on entry, then goes on."""
    world.conflicted_master_merge()
    assert world.merge_head == world.master

    with landing_sync.merges_cleared(
        world.workspace, world.task.path, lambda sha: sha == world.master
    ):
        assert world.merge_head is None


def test_a_merge_that_is_not_the_systems_is_left_alone(world):
    world.conflicted_master_merge()

    with landing_sync.merges_cleared(world.workspace, world.task.path, lambda sha: False):
        pass

    assert world.merge_head == world.master


def test_abort_without_a_merge_is_a_no_op_and_merge_head_answers_the_full_sha(world):
    """(v)"""
    assert world.merge_head is None
    world.workspace.abort_merge(world.task.path)
    assert world.merge_head is None

    world.conflicted_master_merge()
    assert world.merge_head is not None and len(world.merge_head) == 40
    world.workspace.abort_merge(world.task.path)
    assert world.merge_head is None


def test_merge_owner_recognises_the_systems_shas_and_rejects_a_stranger():
    entries = [{"head": "h" * 40, "step": "sync_master", "upstream": "u" * 40}]

    assert landing.merge_owner(None, "m" * 40, "x" * 40, entries) == "none"
    assert landing.merge_owner("m" * 40, "m" * 40, "x" * 40, entries) == "own"
    assert landing.merge_owner("x" * 40, "m" * 40, "x" * 40, entries) == "own"
    assert landing.merge_owner("u" * 40, "m" * 40, "x" * 40, entries) == "own"
    assert landing.merge_owner("h" * 40, "m" * 40, "x" * 40, entries) == "own"
    assert landing.merge_owner("f" * 40, "m" * 40, "x" * 40, entries) == "foreign"


def test_sync_master_on_a_conflict_leaves_the_tree_clean_and_the_fan_in_merge_finds_it_so(
    world, tmp_path
):
    """(ii) and (iii) at the declared seam: the step itself on a real worktree, then the
    fan-in merge the resume starts at."""
    ports = _ports(world)
    head = world.workspace.full_head(world.task.path)
    ports.documents.save(
        {
            "id": "GH-9",
            "status": "ready_for_review",
            "pull_request": "https://example.test/pr/9",
            "subtasks": {
                "integration": {"id": "integration", "status": "approved", "reviewed_head": head},
                "api": {"id": "api", "status": "approved", "branch": "agentic/GH-9.api"},
            },
            "landing": [{"head": head, "step": "sync_master", "refusal": "merge_conflict"}],
        }
    )
    context = LandingContext(
        "GH-9",
        Configuration(product_dir=world.repo),
        ports,
        LandingStep("sync_master", "merge_conflict"),
        head,
        Upstream("origin/master", world.master, None),
        None,
        lambda line: None,
    )

    landing_sync.sync_master(context)

    assert world.merge_head is None
    assert world.workspace.full_head(world.task.path) == head
    assert world.workspace.is_clean(world.task.path)
    document = ports.documents.load("GH-9")
    returned = [s for s in document["subtasks"].values() if s.get("status") == "returned"]
    assert returned and all(
        f"git merge {world.master}" in s["reason"] and "shared.txt" in s["reason"] for s in returned
    )
    assert _merge_approved(document, world.task.path, ports) == []
    assert world.merge_head is None
