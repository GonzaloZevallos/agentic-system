"""GH-297, subtask `slice-git`: the verbs that build a slice of approved units on master and
that find the integrator's own work. Run on a real temporary repository: master moves
meanwhile, units branch off the first commit, and a checkout is a detached worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.shared.configuration import Configuration

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
    """`v0` with `shared.txt`; units `api` (a.txt), `ui` (b.txt, built on `api`), `left` and
    `right` (both rewrite `shared.txt`, so they conflict with each other); master then takes
    `m.txt`, so no merge is a fast-forward."""

    def __init__(self, root: Path) -> None:
        self.repo = root / "product"
        self.repo.mkdir()
        git("init", "-q", "-b", "master", cwd=self.repo)
        (self.repo / "shared.txt").write_text("base\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "v0", cwd=self.repo)
        self.v0 = self.rev("master")
        self.unit("api", "a.txt", "api\n", start="master")
        self.unit("ui", "b.txt", "ui\n", start="api")
        self.unit("left", "shared.txt", "left\n", start="master")
        self.unit("right", "shared.txt", "right\n", start="master")
        (self.repo / "m.txt").write_text("master\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "master moves", cwd=self.repo)
        self.master = self.rev("master")
        self.workspace = GitWorkspace(self.repo, IDENTITY)
        self.checkout = self.workspace.detached_checkout("slice-1", self.master)

    def rev(self, ref: str) -> str:
        return git("rev-parse", ref, cwd=self.repo)

    def unit(self, name: str, filename: str, text: str, *, start: str) -> None:
        git("checkout", "-q", "-b", name, start, cwd=self.repo)
        (self.repo / filename).write_text(text)
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", name, cwd=self.repo)
        git("checkout", "-q", "master", cwd=self.repo)

    def parents_of_merges(self, ref: str) -> list[list[str]]:
        out = git(
            "log", "--first-parent", "--merges", "--reverse", "--format=%P", ref, cwd=self.repo
        )
        return [line.split() for line in out.splitlines()]

    def worktrees(self) -> list[str]:
        return git("worktree", "list", "--porcelain", cwd=self.repo).count("worktree ")


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_a_clean_slice_is_a_branch_from_master_whose_head_contains_every_head(world):
    head = world.workspace.build_slice(
        world.checkout, world.master, [world.rev("api"), world.rev("left")], "agentic/GH-9.slice-1"
    )
    assert head == world.rev("agentic/GH-9.slice-1")
    for contained in (world.master, world.rev("api"), world.rev("left")):
        assert git("merge-base", "--is-ancestor", contained, head, cwd=world.repo) == ""
    assert (world.checkout / "m.txt").is_file() and (world.checkout / "a.txt").is_file()
    assert world.workspace.is_clean(world.checkout)


def test_dependent_units_are_merged_in_the_order_given(world):
    head = world.workspace.build_slice(
        world.checkout, world.master, [world.rev("api"), world.rev("ui")], "agentic/GH-9.slice-1"
    )
    assert head is not None
    assert [p[1] for p in world.parents_of_merges(head)] == [world.rev("api"), world.rev("ui")]


def test_a_conflicting_slice_returns_none_and_leaves_the_repo_clean(world):
    heads = [world.rev("left"), world.rev("right")]
    before = world.workspace.full_head(world.checkout)
    assert (
        world.workspace.build_slice(world.checkout, world.master, heads, "agentic/GH-9.s") is None
    )
    assert world.workspace.merge_head(world.checkout) is None
    assert world.workspace.is_clean(world.checkout)
    assert world.workspace.full_head(world.checkout) == before
    assert world.workspace.branch_head("agentic/GH-9.s") is None


def test_a_conflicting_slice_resets_a_branch_left_by_an_earlier_round(world):
    ok = world.workspace.build_slice(
        world.checkout, world.master, [world.rev("api")], "agentic/GH-9.s"
    )
    assert ok is not None
    heads = [world.rev("left"), world.rev("right")]
    assert (
        world.workspace.build_slice(world.checkout, world.master, heads, "agentic/GH-9.s") is None
    )
    assert world.workspace.branch_head("agentic/GH-9.s") is None


def _task_branch_with_integration(world: World, *, edit: str) -> str:
    """A task branch off v0 that merged `api` and `ui`'s sibling, then did the integrator's
    own work inside the merge commit: `glue.txt` (new) and an edit of `edit`."""
    git("checkout", "-q", "-b", "task", world.v0, cwd=world.repo)
    git("merge", "-q", "--no-ff", "--no-commit", "api", cwd=world.repo)
    (world.repo / "glue.txt").write_text("glue\n")
    (world.repo / edit).write_text("edited by the integrator\n")
    git("add", "-A", cwd=world.repo)
    git("commit", "-q", "-m", "integrate", cwd=world.repo)
    head = world.rev("task")
    git("checkout", "-q", "master", cwd=world.repo)
    return head


def test_integration_paths_reports_what_was_resolved_inside_the_merge_commit(world):
    task_head = _task_branch_with_integration(world, edit="a.txt")
    paths = world.workspace.integration_paths(
        world.checkout, [world.rev("api")], task_head, master=world.master
    )
    assert paths == frozenset({"glue.txt", "a.txt"})
    assert world.worktrees() == 2  # the product's and the test's own: the temporary one is gone


def test_integration_paths_is_empty_when_the_heads_merge_to_the_task_tree(world):
    git("checkout", "-q", "-b", "task", world.v0, cwd=world.repo)
    git("merge", "-q", "--no-ff", "-m", "units", "api", cwd=world.repo)
    task_head = world.rev("task")
    git("checkout", "-q", "master", cwd=world.repo)
    paths = world.workspace.integration_paths(
        world.checkout, [world.rev("api")], task_head, master=world.master
    )
    assert paths == frozenset()


def test_integration_paths_defaults_master_to_the_fetched_upstream_of_the_product(world):
    task_head = _task_branch_with_integration(world, edit="a.txt")
    paths = world.workspace.integration_paths(world.checkout, [world.rev("api")], task_head)
    assert paths == frozenset({"glue.txt", "a.txt"})


def test_integration_paths_is_none_on_a_conflict_and_cleans_its_worktree(world):
    task_head = world.rev("left")
    heads = [world.rev("left"), world.rev("right")]
    paths = world.workspace.integration_paths(world.checkout, heads, task_head, master=world.master)
    assert paths is None
    assert world.worktrees() == 2
    assert not list((world.repo / ".agentic/worktrees").glob("integration-*"))
