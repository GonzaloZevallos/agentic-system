"""`GitWorkspace.numstat` against a real temporary repository: rename detection, a binary
file as 0/0 and the three-dot base, so what the upstream gained never enters the count (GH-592)."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application.ports import GitError
from agentic.core.process.domain.size_budget import NumstatRow
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity
BODY = "".join(f"line {n}\n" for n in range(40))


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
    (repo / "old.py").write_text(BODY)
    (repo / "keep.py").write_text("one\ntwo\n")
    commit(repo, "v1")
    return repo


def numstat(product: Path, base: str, head: str) -> dict[str, tuple[int, int]]:
    rows = GitWorkspace(product, IDENTITY).numstat(base, head)
    assert all(isinstance(row, NumstatRow) for row in rows)
    return {row.path: (row.added, row.deleted) for row in rows}


def test_an_added_file_counts_its_lines(product):
    base = git("rev-parse", "HEAD", cwd=product)
    (product / "new.py").write_text("a\nb\nc\n")
    head = commit(product, "add")
    assert numstat(product, base, head) == {"new.py": (3, 0)}


def test_a_pure_rename_counts_zero_under_the_new_path(product):
    base = git("rev-parse", "HEAD", cwd=product)
    (product / "pkg").mkdir()
    git("mv", "old.py", "pkg/moved.py", cwd=product)
    head = commit(product, "move")
    assert numstat(product, base, head) == {"pkg/moved.py": (0, 0)}


def test_a_rename_with_edits_counts_only_the_edited_lines(product):
    base = git("rev-parse", "HEAD", cwd=product)
    git("mv", "old.py", "renamed.py", cwd=product)
    (product / "renamed.py").write_text(BODY + "extra\n")
    head = commit(product, "move and edit")
    assert numstat(product, base, head) == {"renamed.py": (1, 0)}


def test_a_binary_file_counts_zero(product):
    base = git("rev-parse", "HEAD", cwd=product)
    (product / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    head = commit(product, "binary")
    assert numstat(product, base, head) == {"blob.bin": (0, 0)}


def test_a_path_with_spaces_and_unicode_comes_back_as_it_is(product):
    base = git("rev-parse", "HEAD", cwd=product)
    (product / "mi archivo ñ.py").write_text("a\n")
    head = commit(product, "odd name")
    assert numstat(product, base, head) == {"mi archivo ñ.py": (1, 0)}


def test_what_the_upstream_gained_after_the_branch_point_is_not_counted(product):
    git("checkout", "-q", "-b", "task", cwd=product)
    (product / "mine.py").write_text("a\nb\n")
    head = commit(product, "mine")
    git("checkout", "-q", "master", cwd=product)
    (product / "theirs.py").write_text(BODY)
    upstream = commit(product, "upstream moved")
    assert numstat(product, upstream, head) == {"mine.py": (2, 0)}


def test_an_unknown_revision_raises_a_git_error(product):
    with pytest.raises(GitError):
        GitWorkspace(product, IDENTITY).numstat("nope", "HEAD")
