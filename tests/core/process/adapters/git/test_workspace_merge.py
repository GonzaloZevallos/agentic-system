"""GH-492: `Workspace.merge` answers a closed result on a real repository -- `None` for a clean
merge, `MergeConflict` when the merge left unmerged paths, `MergeFailed` for any other
refusal -- and `merge_conflicts` (merge-tree) reads the same pair the same way."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application.ports import MergeConflict, MergeFailed
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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    git("init", "-q", "-b", "master", cwd=root)
    (root / "shared.txt").write_text("base\n")
    (root / "other.txt").write_text("base\n")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "v0", cwd=root)
    git("branch", "clean", cwd=root)
    git("checkout", "-q", "-b", "conflicting", cwd=root)
    (root / "shared.txt").write_text("head\n")
    git("commit", "-qam", "head edit", cwd=root)
    git("checkout", "-q", "clean", cwd=root)
    (root / "added.txt").write_text("added\n")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "clean edit", cwd=root)
    git("checkout", "-q", "master", cwd=root)
    (root / "shared.txt").write_text("master\n")
    git("commit", "-qam", "master edit", cwd=root)
    return root


@pytest.fixture
def workspace(repo: Path) -> GitWorkspace:
    return GitWorkspace(repo, IDENTITY)


def test_a_clean_merge_answers_none_and_commits(workspace, repo):
    assert workspace.merge(repo, "clean") is None
    assert (repo / "added.txt").exists()


def test_a_message_is_the_merge_commit_message(workspace, repo):
    assert workspace.merge(repo, "clean", "fresh run") is None
    assert git("log", "-1", "--format=%s", cwd=repo) == "fresh run"


def test_a_conflicting_edit_of_a_tracked_file_names_the_path(workspace, repo):
    result = workspace.merge(repo, "conflicting")
    assert isinstance(result, MergeConflict)
    assert result.paths == ("shared.txt",)
    assert "CONFLICT" in result.reason


def test_a_conflict_is_undone_so_the_checkout_stays_clean(workspace, repo):
    workspace.merge(repo, "conflicting")
    assert git("status", "--porcelain", cwd=repo) == ""
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_a_branch_that_does_not_exist_is_a_failure_not_a_conflict(workspace, repo):
    result = workspace.merge(repo, "no-such-branch")
    assert isinstance(result, MergeFailed)
    assert result.reason == "merge: no-such-branch - not something we can merge"


def test_a_refusal_without_unmerged_paths_is_a_failure(workspace, repo):
    # An untracked file the merge would overwrite: git refuses, no path is left unmerged.
    git("checkout", "-q", "-B", "side", "master~1", cwd=repo)
    (repo / "added.txt").write_text("mine\n")
    result = workspace.merge(repo, "clean")
    assert isinstance(result, MergeFailed)
    assert "added.txt" in result.reason
    assert (repo / "added.txt").read_text() == "mine\n"


def test_merge_tree_and_merge_agree_on_the_conflicting_pair(workspace, repo):
    head, master = git("rev-parse", "conflicting", cwd=repo), git("rev-parse", "master", cwd=repo)
    assert workspace.merge_conflicts(head, master) is True
    assert isinstance(workspace.merge(repo, "conflicting"), MergeConflict)


def test_merge_tree_and_merge_agree_on_the_clean_pair(workspace, repo):
    head, master = git("rev-parse", "clean", cwd=repo), git("rev-parse", "master", cwd=repo)
    assert workspace.merge_conflicts(head, master) is False
    assert workspace.merge(repo, "clean") is None
