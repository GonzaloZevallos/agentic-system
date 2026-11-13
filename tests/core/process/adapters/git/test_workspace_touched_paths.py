"""`GitWorkspace.touched_paths` against a real temporary repository (GH-591, GH-681): the paths a
head changed since it left the base (the three-dot diff), a rename as its old and its new path.
The fast lane reads a stale head's paths this way, so master's own movement must not show."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
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


def commit(repo: Path, message: str) -> str:
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def product(tmp_path: Path) -> Path:
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / "a.py").write_text("a\n")
    (repo / "b.py").write_text("b\n")
    (repo / "old_name.py").write_text("one\ntwo\nthree\nfour\n")
    commit(repo, "v1")
    return repo


def workspace(product: Path) -> GitWorkspace:
    return GitWorkspace(product, IDENTITY)


def test_a_head_that_holds_master_gives_what_it_touched(product):
    master = git("rev-parse", "HEAD", cwd=product)
    git("checkout", "-q", "-b", "task", cwd=product)
    (product / "a.py").write_text("a2\n")
    head = commit(product, "task")

    assert set(workspace(product).touched_paths(master, head)) == {"a.py"}


def test_a_stale_head_gives_its_own_paths_and_not_masters(product):
    base = git("rev-parse", "HEAD", cwd=product)
    git("checkout", "-q", "-b", "task", cwd=product)
    (product / "b.py").write_text("b2\n")
    head = commit(product, "task")
    git("checkout", "-q", "master", cwd=product)
    (product / "c.py").write_text("c\n")
    master = commit(product, "master moves")
    assert git("merge-base", master, head, cwd=product) == base  # the head lacks master

    touched = set(workspace(product).touched_paths(master, head))

    assert touched == {"b.py"}
    assert "c.py" not in touched


def test_a_rename_is_its_old_and_its_new_path(product):
    base = git("rev-parse", "HEAD", cwd=product)
    git("mv", "old_name.py", "new_name.py", cwd=product)
    head = commit(product, "rename")

    assert set(workspace(product).touched_paths(base, head)) == {"old_name.py", "new_name.py"}


def test_an_unknown_sha_raises(product):
    head = git("rev-parse", "HEAD", cwd=product)

    with pytest.raises(GitError):
        workspace(product).touched_paths("0" * 40, head)
