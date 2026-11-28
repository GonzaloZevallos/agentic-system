"""GH-860: `stack_facts` through the real git adapter, on a parent that landed (or did not) and
the child built on it. The facts are always read, never built by hand."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application import review_scope
from agentic.core.process.application.ports import IssueState
from agentic.core.process.application.stack_facts import StackMemo, stack_facts
from agentic.core.process.domain import stacking
from agentic.core.process.domain.task import StackParent
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(repo: Path, name: str, text: str, message: str) -> str:
    (repo / name).write_text(text)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


@dataclass
class _Tracker:
    state: IssueState

    def issue_state(self, number: int) -> IssueState:
        return self.state


@dataclass
class _Ports:
    workspace: GitWorkspace
    tracker: _Tracker
    stack: MemoryStack = field(default_factory=MemoryStack)


@dataclass
class Stack:
    """parent v1, a child branched from it, a parent fix, master receiving the parent's merge."""

    repo: Path
    child: Path
    ws: GitWorkspace
    before: str  # master before it took the parent
    parent_v1: str
    child_head: str
    merge: str  # master's merge commit of the parent
    squash: str  # a master that took the parent's work with a squash, on the side

    def facts(
        self, tracker: IssueState, tip: str, head: str, parent: str | None = None
    ) -> stacking.StackFacts:
        record = StackParent(
            issue=7, task_id="GH-7", head=parent or self.parent_v1, started_at="2026-11-16T10:00:00"
        )
        unread = stacking.facts_of({"stack_parent": record})
        assert unread is not None
        ports = cast(Any, _Ports(self.ws, _Tracker(tracker)))
        cfg = Configuration(product_dir=self.repo)
        return stack_facts(ports, cfg, unread, tip, head, StackMemo())

    def scope(self, reviewed: str, head: str) -> str:
        state: dict[str, Any] = {
            "id": "GH-7",
            "subtasks": {"a": {"reviewed_head": reviewed}},
            "landing": [{"upstream": self.merge}],
        }
        verdict, _ = review_scope.scope_of(
            state, "a", head, self.child, cast(Any, _Ports(self.ws, _Tracker(_issue("OPEN"))))
        )
        return verdict.scope


def _issue(state: Literal["OPEN", "CLOSED"], merge: str | None = None) -> IssueState:
    return IssueState(state, "COMPLETED", (), merge)


def _stack(tmp_path: Path, conflicting: bool) -> Stack:
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-q", str(remote), cwd=tmp_path)
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    before = commit(repo, "README.md", "v0\n", "v0")
    git("checkout", "-q", "-b", "parent", cwd=repo)
    parent_v1 = commit(repo, "parent.txt", "one\ntwo\n", "parent v1")
    git("checkout", "-q", "-b", "child", cwd=repo)
    if conflicting:
        child_head = commit(repo, "parent.txt", "one\nchild\n", "child edits parent.txt")
    else:
        child_head = commit(repo, "child.txt", "child\n", "child")
    git("checkout", "-q", "parent", cwd=repo)
    commit(repo, "parent.txt", "one\nfix\n", "parent fix")
    git("checkout", "-q", "master", cwd=repo)
    git("merge", "-q", "--no-ff", "-m", "merge parent", "parent", cwd=repo)
    merge = git("rev-parse", "HEAD", cwd=repo)
    git("checkout", "-q", "-b", "side", before, cwd=repo)
    git("merge", "-q", "--squash", "parent", cwd=repo)
    git("commit", "-q", "-m", "squash parent", cwd=repo)
    squash = git("rev-parse", "HEAD", cwd=repo)
    git("checkout", "-q", "master", cwd=repo)
    git("push", "-q", "origin", "master", cwd=repo)
    child = tmp_path / "child"
    git("worktree", "add", "-q", str(child), "child", cwd=repo)
    return Stack(
        repo, child, GitWorkspace(repo, IDENTITY), before, parent_v1, child_head, merge, squash
    )


def test_a_clean_sync_of_a_landed_parent_is_landed_and_carries_the_review(tmp_path):
    stack = _stack(tmp_path, conflicting=False)
    synced = stack.ws.merge_upstream_into(stack.child, stack.merge)
    assert synced.clean
    facts = stack.facts(_issue("CLOSED", stack.merge), stack.merge, synced.head)
    assert (facts.parent_in_upstream, facts.child_has_parent) == (True, True)
    assert stacking.base_landed(facts, False)
    assert stack.scope(stack.child_head, synced.head) == "carried"


def test_a_conflicting_sync_is_reported_and_a_resolution_is_reviewed_as_one(tmp_path):
    stack = _stack(tmp_path, conflicting=True)
    synced = stack.ws.merge_upstream_into(stack.child, stack.merge)
    assert not synced.clean and synced.conflicted == ("parent.txt",)
    (stack.child / "parent.txt").write_text("one\nresolved\n")
    git("add", "parent.txt", cwd=stack.child)
    git("commit", "-q", "-m", "merge master", cwd=stack.child)
    head = git("rev-parse", "HEAD", cwd=stack.child)
    facts = stack.facts(_issue("CLOSED", stack.merge), stack.merge, head)
    assert stacking.base_landed(facts, False)
    assert stack.scope(stack.child_head, head) == "resolution"


@pytest.mark.parametrize("closing", ["none", "squash"])
def test_an_orphaned_or_squashed_parent_is_never_landed_and_never_restack_due(tmp_path, closing):
    stack = _stack(tmp_path, conflicting=False)
    merge = None if closing == "none" else stack.squash
    facts = stack.facts(_issue("CLOSED", merge), stack.squash, stack.child_head)
    assert (facts.parent_in_upstream, facts.parent_state) == (False, "closed")
    assert not stacking.base_landed(facts, False)
    assert stacking.refusal(facts, False) == "stack_base_orphaned"
    assert not stacking.restack_due(facts, False)


def test_a_parent_head_nobody_has_is_unknown_and_unmerged(tmp_path):
    stack = _stack(tmp_path, conflicting=False)
    facts = stack.facts(_issue("OPEN"), stack.merge, stack.child_head, parent="9" * 40)
    assert (facts.parent_in_upstream, facts.child_has_parent) == (None, None)
    assert stacking.refusal(facts, False) == "stack_parent_unmerged"


def test_a_merged_parent_seen_through_a_stale_tip_is_never_orphaned(tmp_path):
    stack = _stack(tmp_path, conflicting=False)
    facts = stack.facts(_issue("CLOSED", stack.merge), stack.before, stack.child_head)
    assert facts.parent_in_upstream is False
    assert stacking.refusal(facts, False) == "stack_parent_unmerged"
