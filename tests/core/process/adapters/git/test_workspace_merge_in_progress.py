"""GH-392: `merge_in_progress` says whether a checkout holds an unfinished merge or conflicts,
on a real repository: a resume must not commit what a human is in the middle of resolving."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application.ports import GitError
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity


def git(*args: str, cwd: Path, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    git("init", "-q", "-b", "master", cwd=root)
    (root / "shared.txt").write_text("base\n")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "v0", cwd=root)
    git("checkout", "-q", "-b", "other", cwd=root)
    (root / "shared.txt").write_text("other\n")
    git("commit", "-qam", "other", cwd=root)
    git("checkout", "-q", "master", cwd=root)
    (root / "shared.txt").write_text("master\n")
    git("commit", "-qam", "master", cwd=root)
    return root


@pytest.fixture
def workspace(repo: Path) -> GitWorkspace:
    return GitWorkspace(repo, IDENTITY)


def test_a_clean_checkout_holds_no_merge(workspace, repo):
    assert workspace.merge_in_progress(repo) is False


def test_uncommitted_edits_alone_are_not_a_merge(workspace, repo):
    (repo / "new.txt").write_text("x\n")
    (repo / "shared.txt").write_text("edited\n")
    assert workspace.merge_in_progress(repo) is False


def test_a_conflicted_merge_holds_one(workspace, repo):
    git("merge", "other", cwd=repo, check=False)
    assert (repo / ".git" / "MERGE_HEAD").exists()
    assert workspace.merge_in_progress(repo) is True


def test_a_conflict_entry_without_merge_head_still_counts(workspace, repo):
    git("merge", "other", cwd=repo, check=False)
    (repo / ".git" / "MERGE_HEAD").unlink()
    assert "UU shared.txt" in git("status", "--porcelain", cwd=repo)
    assert workspace.merge_in_progress(repo) is True


def test_a_merge_stopped_before_its_commit_holds_one(workspace, repo):
    git("checkout", "-q", "-b", "side", "master~1", cwd=repo)
    (repo / "extra.txt").write_text("e\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "extra", cwd=repo)
    git("merge", "--no-commit", "--no-ff", "other", cwd=repo, check=False)
    assert (repo / ".git" / "MERGE_HEAD").exists()
    assert workspace.merge_in_progress(repo) is True


def test_a_checkout_git_cannot_read_raises_git_error(workspace, repo, tmp_path, monkeypatch):
    # Git answers neither way: not "a merge" and not "no merge", so the caller must not guess.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-such-git-dir"))
    with pytest.raises(GitError):
        workspace.merge_in_progress(repo)
