"""GH-178: `agentic merge` over a real git repository -- `GitWorkspace` makes the throwaway
worktree and merges the upstream into it, `ShellCommands` runs the checks there,
`GitStack` reads them at the task's base -- with the `Tracker` fake and the real task
document. A fake cannot show that the checks see the head merged with master, or that a
refusal writes nothing the closure could mistake for intent."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import anyio
import pytest

from agentic import cli
from agentic.core.knowledge.adapters.git import GitStack
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.adapters.shell.commands import ShellCommands
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain.task import PullRequest, PullRequestCommit
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryTracker, memory_ports

GREEN = "test -f feature.txt && test -f upstream.txt"
URL = "https://github.com/o/r/pull/9"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class MergingTracker(MemoryTracker):
    """`gh pr merge` for real enough: once merged, the pull request reads MERGED."""

    def merge(self, number: int, head_sha: str) -> str:
        method = super().merge(number, head_sha)
        self.prs[URL] = dataclasses.replace(
            self.prs[URL], state="MERGED", merged_at="2026-10-06T00:00:00Z"
        )
        return method


class World:
    def __init__(self, tmp_path: Path, checks: str):
        self.origin = tmp_path / "origin.git"
        self.product = tmp_path / "product"
        git("init", "-q", "--bare", "-b", "master", str(self.origin), cwd=tmp_path)
        git("clone", "-q", str(self.origin), str(self.product), cwd=tmp_path)
        (self.product / ".agentic").mkdir()
        (self.product / ".agentic/stack.yaml").write_text(
            f"product: demo\nchecks:\n  tests: {checks!r}\n"
        )
        git("add", "-A", cwd=self.product)
        git("commit", "-q", "-m", "v0", cwd=self.product)
        git("push", "-q", "-u", "origin", "HEAD:master", cwd=self.product)
        git("branch", "-q", "--set-upstream-to=origin/master", cwd=self.product)
        self.cfg = Configuration(product_dir=self.product, run_registry_dir=tmp_path / "registry")
        self.tracker = MergingTracker()
        self.ports = dataclasses.replace(
            memory_ports(),
            workspace=GitWorkspace(self.product, self.cfg.git_identity),
            stack=GitStack(self.product),
            commands=ShellCommands(),
            documents=FilesystemTaskDocuments(self.product),
            tracker=self.tracker,
        )
        checkout = self.ports.workspace.task_checkout("GH-9")
        (checkout.path / "feature.txt").write_text("feature\n")
        git("add", "-A", cwd=checkout.path)
        git("commit", "-q", "-m", "feature", cwd=checkout.path)
        self.head = git("rev-parse", "HEAD", cwd=checkout.path)
        self.ports.workspace.discard(checkout.path)
        self.advance_master()
        self.deliver()

    def advance_master(self) -> str:
        (self.product / "upstream.txt").write_text(f"{len(list(self.product.iterdir()))}\n")
        git("add", "upstream.txt", cwd=self.product)
        git("commit", "-q", "-m", "master moves", cwd=self.product)
        git("push", "-q", "origin", "HEAD:master", cwd=self.product)
        return git("rev-parse", "HEAD", cwd=self.product)

    def deliver(self, **more: object) -> None:
        self.tracker.prs[URL] = PullRequest(
            state="OPEN",
            created_at="2026-10-05T00:00:00Z",
            merged_at=None,
            commits=(PullRequestCommit((self.cfg.git_author_email,), "feature", 1, 0, 1),),
            head_oid=self.head,
        )
        self.ports.documents.save(
            {
                "id": "GH-9",
                "title": "t",
                "description": "d",
                "status": "ready_for_review",
                "pull_request": URL,
                "subtasks": {
                    "only": {"id": "only", "status": "approved", "reviewed_head": self.head}
                },
                **more,
            }
        )

    def merge(self) -> None:
        anyio.run(cli._merge, "GH-9", 9, self.cfg, self.ports)

    def doc(self) -> dict:
        return self.ports.documents.load("GH-9") or {}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path, GREEN)


def test_a_green_head_merged_with_master_is_merged_and_closed_as_auto_merged(world):
    world.merge()

    doc = world.doc()
    master = git("rev-parse", "origin/master", cwd=world.product)
    assert doc["merge_checks"]["passed"] is True
    assert doc["merge_checks"]["head"] == world.head and doc["merge_checks"]["master"] == master
    assert world.tracker.merge_calls == [(9, world.head)]
    assert doc["auto_merge_head"] == world.head and doc["auto_merge_master"] == master
    assert doc["auto_merge_method"] == "squash" and doc["auto_merged_at"]
    assert doc["delivery_outcome"] == "auto_merged"
    assert [text for _, text in world.tracker.comments] == [f"Auto-merged: {world.head} via squash"]
    # The checks ran in the throwaway worktree: gone now, and the product tree untouched.
    assert not (world.product / ".agentic/worktrees/merge-GH-9").exists()
    assert not (world.product / "feature.txt").exists()


def test_a_red_head_is_refused_once_and_writes_no_intent(tmp_path):
    world = World(tmp_path, "exit 1")

    world.merge()
    world.merge()

    doc = world.doc()
    assert doc["merge_checks"]["passed"] is False
    assert "auto_merge_head" not in doc and world.tracker.merge_calls == []
    assert [text.splitlines()[0] for _, text in world.tracker.comments] == [
        f"Auto-merge refused: {world.head} checks_failed"
    ]
    assert doc["merge_attempts"] == {"head": world.head, "count": 1}  # the second ran nothing


def test_a_leftover_merge_worktree_does_not_break_entry(world):
    leftover = world.product / ".agentic/worktrees/merge-GH-9"
    leftover.mkdir(parents=True)
    (leftover / "stale.txt").write_text("from a crashed run")

    world.merge()

    assert world.tracker.merge_calls == [(9, world.head)]
    assert not leftover.exists()


def test_master_moving_after_the_checks_refuses_and_writes_nothing(world, monkeypatch):
    real = world.ports.workspace
    calls = []

    class Moving:
        def __getattr__(self, name):
            return getattr(real, name)

        def upstream(self):
            calls.append(1)
            if len(calls) == 2:  # the last look, after the checks
                return Upstream("origin/master", "f" * 40, None)
            return real.upstream()

    world.ports = dataclasses.replace(world.ports, workspace=Moving())
    world.merge()

    doc = world.doc()
    assert world.tracker.merge_calls == [] and "auto_merge_head" not in doc
    assert doc["merge_checks"]["passed"] is True
    assert world.tracker.comments == []


def test_a_merge_that_fails_with_the_pull_request_open_withdraws_the_intent(world):
    world.tracker.merge = lambda number, head: (_ for _ in ()).throw(ValueError("head moved"))  # type: ignore[method-assign]

    world.merge()

    doc = world.doc()
    assert doc["auto_merge_head"] == ""
    # The human merges that very head untouched later: the human's verdict, `merged`.
    world.tracker.prs[URL] = dataclasses.replace(
        world.tracker.prs[URL], state="MERGED", merged_at="2026-10-06T01:00:00Z"
    )
    result = cli._close_document("GH-9", world.cfg, world.ports)
    assert result.outcome == "merged"


def test_a_merge_that_fails_but_landed_keeps_the_intent_and_closes_as_auto_merged(world):
    def landed(number: int, head: str) -> str:
        world.tracker.prs[URL] = dataclasses.replace(
            world.tracker.prs[URL], state="MERGED", merged_at="2026-10-06T00:00:00Z"
        )
        raise ValueError("gh timed out after merging")

    world.tracker.merge = landed  # type: ignore[method-assign]

    world.merge()

    assert world.doc()["auto_merge_head"] == world.head
    assert world.doc()["delivery_outcome"] == "auto_merged"


def test_an_issue_in_the_eval_set_on_upstream_alone_is_never_merged(world):
    (world.product / ".agentic/eval-set.yaml").write_text("issues: [9, 10]\nsmoke: 9\n")
    git("add", ".agentic/eval-set.yaml", cwd=world.product)
    git("commit", "-q", "-m", "eval set", cwd=world.product)
    git("push", "-q", "origin", "HEAD:master", cwd=world.product)
    # Present upstream only: the product's own checkout has it unmerged-looking too, so
    # drop it locally and keep the pushed commit.
    git("reset", "-q", "--hard", "HEAD~1", cwd=world.product)

    world.merge()

    assert world.tracker.merge_calls == [] and world.tracker.comments == []
    assert "merge_checks" not in world.doc()


def test_the_eval_set_helper_reads_upstream_and_refuses_what_it_cannot_read(world):
    upstream = world.ports.workspace.upstream()
    assert cli._upstream_eval_set(world.ports, upstream) == frozenset()

    (world.product / ".agentic/eval-set.yaml").write_text("issues: [: broken\n")
    git("add", ".agentic/eval-set.yaml", cwd=world.product)
    git("commit", "-q", "-m", "broken", cwd=world.product)
    git("push", "-q", "origin", "HEAD:master", cwd=world.product)
    assert cli._upstream_eval_set(world.ports, world.ports.workspace.upstream()) is None

    assert cli._upstream_eval_set(world.ports, Upstream("HEAD", "a" * 40, "fell back")) is None
