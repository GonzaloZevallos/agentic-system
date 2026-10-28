"""`tests.conftest.git_ceiling`: the value that keeps git from finding the repository a
temporary directory happens to sit in (the delivery gate's `TMPDIR` is under a checkout)."""

import subprocess
from pathlib import Path

from agentic.core.process.adapters.git import GitWorkspace
from tests.conftest import git_ceiling

IDENTITY = {"GIT_AUTHOR_NAME": "t"}


def test_the_ceiling_hides_the_repository_a_base_directory_sits_in(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    base = repo / ".agentic" / "tmp" / "pytest-of-x" / "pytest-0"
    plain = base / "test_one"
    plain.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    workspace = GitWorkspace(tmp_path, IDENTITY)

    monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)
    assert workspace.repository_at(plain) == (repo / ".git").resolve()

    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", git_ceiling(base, None))
    assert workspace.repository_at(plain) is None


def test_the_ceiling_keeps_what_was_already_set():
    value = git_ceiling(Path("/a/b/c"), "/x")
    assert value.split(":") == ["/x", "/a/b"]
