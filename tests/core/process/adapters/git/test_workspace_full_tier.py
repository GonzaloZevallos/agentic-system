"""`GitWorkspace.differs_outside` and `GitWorkspace.restore_clean` on a real temporary
repository (GH-358): the two verbs under the full tier's guard and its clean checkout."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def product(tmp_path: Path) -> Path:
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / "app.py").write_text("v1\n")
    (repo / "tasks").mkdir()
    (repo / "tasks" / "GH-1.md").write_text("doc v1\n")
    (repo / ".gitignore").write_text("ignored.log\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "v1", cwd=repo)
    return repo


@pytest.fixture
def workspace(product: Path) -> GitWorkspace:
    return GitWorkspace(product, {})


def commit(repo: Path, message: str) -> str:
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


def test_a_head_that_differs_only_in_the_excluded_path_does_not_differ_outside(
    product: Path, workspace: GitWorkspace
):
    verified = git("rev-parse", "HEAD", cwd=product)
    (product / "tasks" / "GH-1.md").write_text("doc v2\n")
    head = commit(product, "document")
    assert not workspace.differs_outside(product, verified, head, "tasks/GH-1.md")


def test_a_head_whose_code_differs_does_differ_outside_even_with_the_document(
    product: Path, workspace: GitWorkspace
):
    verified = git("rev-parse", "HEAD", cwd=product)
    (product / "app.py").write_text("v2\n")
    (product / "tasks" / "GH-1.md").write_text("doc v2\n")
    head = commit(product, "code and document")
    assert workspace.differs_outside(product, verified, head, "tasks/GH-1.md")


def test_the_same_commit_does_not_differ_outside(product: Path, workspace: GitWorkspace):
    head = git("rev-parse", "HEAD", cwd=product)
    assert not workspace.differs_outside(product, head, head, "tasks/GH-1.md")


def test_restore_clean_drops_tracked_edits_and_untracked_files_but_keeps_ignored_ones(
    product: Path, workspace: GitWorkspace
):
    (product / "app.py").write_text("edited by a check\n")
    (product / "scratch.txt").write_text("untracked\n")
    (product / "out").mkdir()
    (product / "out" / "report.xml").write_text("<r/>")
    (product / "ignored.log").write_text("cache\n")
    assert not workspace.is_clean(product)
    workspace.restore_clean(product)
    assert workspace.is_clean(product)
    assert (product / "app.py").read_text() == "v1\n"
    assert not (product / "scratch.txt").exists()
    assert not (product / "out").exists()
    # Never `-x`: the environment an ignored file belongs to survives.
    assert (product / "ignored.log").read_text() == "cache\n"
