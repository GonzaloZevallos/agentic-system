"""`GitWorkspace.changed_lines` against a real temporary repository: the lines added plus the
lines deleted between two shas, a binary file counted as 0 (GH-473)."""

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
    (repo / "app.py").write_text("one\ntwo\nthree\n")
    commit(repo, "v1")
    return repo


def workspace(product: Path) -> GitWorkspace:
    return GitWorkspace(product, IDENTITY)


def test_added_and_deleted_lines_are_both_counted(product):
    base = git("rev-parse", "HEAD", cwd=product)
    (product / "app.py").write_text("one\nTWO\nthree\nfour\n")  # 2 added, 1 deleted
    (product / "new.py").write_text("a\nb\n")  # 2 added
    head = commit(product, "v2")
    assert workspace(product).changed_lines(base, head) == 5


def test_a_binary_file_counts_as_zero(product):
    base = git("rev-parse", "HEAD", cwd=product)
    (product / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    (product / "app.py").write_text("one\ntwo\nthree\nfour\n")
    head = commit(product, "v2")
    assert workspace(product).changed_lines(base, head) == 1


def test_the_same_sha_is_zero_lines(product):
    head = git("rev-parse", "HEAD", cwd=product)
    assert workspace(product).changed_lines(head, head) == 0


def test_an_unknown_sha_raises(product):
    head = git("rev-parse", "HEAD", cwd=product)
    with pytest.raises(GitError):
        workspace(product).changed_lines("0" * 40, head)
