"""GH-1142: no git command acts from a task checkout whose `.git` is not a gitfile. Such a
command walks up to the product checkout, which is how GH-1107's integrate step left two
merges on the product's master."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.git.guard import ForeignCheckout, require_checkout
from agentic.core.process.application.ports import GitError
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
    (root / "a.txt").write_text("base\n")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "v0", cwd=root)
    return root


@pytest.fixture
def worktrees(repo: Path) -> Path:
    return repo / ".agentic" / "worktrees"


def refs_of(repo: Path) -> tuple[str, str, str]:
    return (
        git("rev-parse", "master", cwd=repo),
        git("rev-parse", "HEAD", cwd=repo),
        git("reflog", "show", "master", cwd=repo),
    )


def test_a_task_folder_without_git_is_refused(worktrees):
    (worktrees / "GH-1").mkdir(parents=True)
    with pytest.raises(ForeignCheckout) as caught:
        require_checkout(worktrees / "GH-1", worktrees)
    assert caught.value.reason == "missing"
    assert caught.value.root == (worktrees / "GH-1").resolve()
    assert "GH-1" in str(caught.value) and "missing" in str(caught.value)
    assert isinstance(caught.value, GitError)


@pytest.mark.parametrize("sub", ["GH-1", "GH-1.part", "merge-GH-1", "slice-GH-1"])
def test_a_subdirectory_of_a_broken_task_folder_is_refused_too(worktrees, sub):
    (worktrees / sub / "src").mkdir(parents=True)
    with pytest.raises(ForeignCheckout):
        require_checkout(worktrees / sub / "src", worktrees)


@pytest.mark.parametrize("contents", ["empty", "full"])
def test_a_git_directory_is_refused_as_not_a_file(worktrees, contents):
    folder = worktrees / "GH-1"
    (folder / ".git").mkdir(parents=True)
    if contents == "full":
        (folder / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
    with pytest.raises(ForeignCheckout) as caught:
        require_checkout(folder, worktrees)
    assert caught.value.reason == "not_a_file"


def test_a_folder_with_a_gitfile_passes(worktrees):
    (worktrees / "GH-1").mkdir(parents=True)
    (worktrees / "GH-1" / ".git").write_text("gitdir: elsewhere\n")
    require_checkout(worktrees / "GH-1", worktrees)
    require_checkout(worktrees / "GH-1" / "deep" / "er", worktrees)


def test_a_path_outside_the_worktrees_or_not_a_task_folder_is_never_denied(repo, worktrees):
    (worktrees / "integration-abc").mkdir(parents=True)
    require_checkout(worktrees / "integration-abc", worktrees)
    require_checkout(worktrees, worktrees)
    require_checkout(repo, worktrees)
    require_checkout(repo.parent, worktrees)


def test_a_symlinked_product_dir_passes(tmp_path, repo):
    link = tmp_path / "link"
    link.symlink_to(repo)
    (repo / ".agentic" / "worktrees" / "GH-1").mkdir(parents=True)
    (repo / ".agentic" / "worktrees" / "GH-1" / ".git").write_text("gitdir: x\n")
    require_checkout(repo / ".agentic" / "worktrees" / "GH-1", link / ".agentic" / "worktrees")
    require_checkout(link / ".agentic" / "worktrees" / "GH-1", repo / ".agentic" / "worktrees")
    require_checkout(link, link / ".agentic" / "worktrees")


def test_git_with_a_cwd_in_a_task_folder_without_git_raises_before_git_runs(repo, worktrees):
    workspace = GitWorkspace(repo, IDENTITY)
    before = refs_of(repo)
    (worktrees / "GH-2").mkdir(parents=True)
    with pytest.raises(ForeignCheckout):
        workspace.head(worktrees / "GH-2")
    (worktrees / "GH-3" / ".git").mkdir(parents=True)
    with pytest.raises(ForeignCheckout):
        workspace.head(worktrees / "GH-3")
    assert refs_of(repo) == before


def test_repository_at_a_task_folder_without_git_does_not_answer_the_products_repository(
    repo, worktrees
):
    workspace = GitWorkspace(repo, IDENTITY)
    assert workspace.repository_at(repo) is not None
    (worktrees / "GH-2").mkdir(parents=True)
    with pytest.raises(ForeignCheckout):
        workspace.repository_at(worktrees / "GH-2")


def lose_gitfile(folder: Path) -> None:
    (folder / ".git").unlink()


def test_a_worktree_that_lost_its_git_is_rebuilt_and_the_merge_never_touches_the_product(
    repo, capsys
):
    workspace = GitWorkspace(repo, IDENTITY)
    task = workspace.task_checkout("GH-7")
    part = workspace.subtask_checkout("GH-7", "part")
    (part.path / "part.txt").write_text("part\n")
    git("add", "-A", cwd=part.path)
    git("commit", "-qm", "part work", cwd=part.path)
    (task.path / "kept-here.txt").write_text("old, uncommitted\n")
    lose_gitfile(task.path)
    before = refs_of(repo)

    again = workspace.task_checkout("GH-7")
    assert again.created is True
    assert workspace.merge(again.path, part.branch) is None

    assert refs_of(repo) == before
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=again.path) == task.branch
    assert (again.path / "part.txt").exists()
    # Nothing is deleted: the old folder is moved under kept/, and it says so on stderr.
    moved = list((repo / ".agentic" / "kept" / "GH-7").glob("orphaned-GH-7-*"))
    assert len(moved) == 1
    assert (moved[0] / "kept-here.txt").read_text() == "old, uncommitted\n"
    assert f"agentic: moved invalid worktree {task.path} to {moved[0]}" in capsys.readouterr().err


def test_a_worktree_whose_git_is_a_directory_is_rebuilt(repo):
    workspace = GitWorkspace(repo, IDENTITY)
    task = workspace.task_checkout("GH-8")
    (task.path / ".git").unlink()
    (task.path / ".git").mkdir()
    before = refs_of(repo)
    again = workspace.task_checkout("GH-8")
    assert again.created is True
    assert (again.path / ".git").is_file()
    assert refs_of(repo) == before


def test_a_valid_worktree_is_not_rebuilt(repo):
    workspace = GitWorkspace(repo, IDENTITY)
    workspace.task_checkout("GH-9")
    assert workspace.task_checkout("GH-9").created is False
    assert not (repo / ".agentic" / "kept").exists()


def test_a_folder_whose_gitfile_git_cannot_read_is_rebuilt(repo):
    workspace = GitWorkspace(repo, IDENTITY)
    task = workspace.task_checkout("GH-10")
    (task.path / ".git").write_text("gitdir: /nowhere/at/all\n")
    again = workspace.task_checkout("GH-10")
    assert again.created is True
    assert git("rev-parse", "--show-toplevel", cwd=again.path) == str(again.path.resolve())


def test_when_the_move_is_impossible_the_merge_raises_and_the_product_is_unchanged(repo):
    workspace = GitWorkspace(repo, IDENTITY)
    task = workspace.task_checkout("GH-11")
    part = workspace.subtask_checkout("GH-11", "part")
    (part.path / "part.txt").write_text("part\n")
    git("add", "-A", cwd=part.path)
    git("commit", "-qm", "part work", cwd=part.path)
    lose_gitfile(task.path)
    (repo / ".agentic" / "kept").write_text("in the way\n")
    before = refs_of(repo)

    with pytest.raises(ForeignCheckout):
        workspace.task_checkout("GH-11")
    with pytest.raises(ForeignCheckout):
        workspace.merge(task.path, part.branch)

    assert refs_of(repo) == before
