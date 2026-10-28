"""`GitWorkspace.introduced_paths` and `fetched_upstream`, against real temporary
repositories: the paths a delivery adds or modifies that the fetched upstream does not
already hold the same way (GH-420)."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity
DOC = ".agentic/tasks/X.md"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def write(repo: Path, relative: str, text: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(repo: Path, message: str) -> str:
    git("add", "-A", "-f", cwd=repo)
    git("commit", "-q", "--allow-empty", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def product(tmp_path: Path) -> Path:
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    write(repo, "app.py", "v1\n")
    commit(repo, "v1")
    return repo


def test_a_commit_that_only_untracks_task_documents_introduces_nothing(product):
    write(product, DOC, "tracked\n")
    base = commit(product, "tracks a document")
    git("rm", "-q", "--cached", DOC, cwd=product)
    head = commit(product, "untrack it")
    ws = GitWorkspace(product, IDENTITY)
    assert (product / DOC).exists()
    assert ws.introduced_paths(product, base, head, base) == ()
    assert ws.introduced_paths(product, base, head, None) == ()


def test_a_merge_of_an_upstream_that_tracks_a_document_introduces_nothing(product):
    base = git("rev-parse", "HEAD", cwd=product)
    git("checkout", "-q", "-b", "upstream", cwd=product)
    write(product, DOC, "from master\n")
    tip = commit(product, "master tracks it")
    git("checkout", "-q", "master", cwd=product)
    write(product, "feature.py", "x\n")
    commit(product, "feature")
    git("merge", "-q", "--no-edit", tip, cwd=product)
    head = git("rev-parse", "HEAD", cwd=product)
    ws = GitWorkspace(product, IDENTITY)
    assert ws.introduced_paths(product, base, head, tip) == ("feature.py",)
    # Without an upstream to compare with, nothing is dropped.
    assert ws.introduced_paths(product, base, head, None) == (DOC, "feature.py")


def test_an_added_task_document_is_introduced(product):
    base = git("rev-parse", "HEAD", cwd=product)
    write(product, DOC, "mine\n")
    head = commit(product, "task document")
    ws = GitWorkspace(product, IDENTITY)
    assert ws.introduced_paths(product, base, head, base) == (DOC,)
    assert ws.introduced_paths(product, base, head, None) == (DOC,)


def test_a_modified_inherited_document_is_introduced(product):
    write(product, DOC, "v1\n")
    upstream = commit(product, "master tracks it")
    git("checkout", "-q", "-b", "work", cwd=product)
    write(product, DOC, "v2\n")
    head = commit(product, "edit it")
    ws = GitWorkspace(product, IDENTITY)
    assert ws.introduced_paths(product, upstream, head, upstream) == (DOC,)


def test_a_rename_is_a_delete_and_an_add_and_only_the_add_counts(product):
    write(product, "old/name.md", "same\n")
    base = commit(product, "old")
    (product / ".agentic" / "tasks").mkdir(parents=True)
    git("mv", "old/name.md", ".agentic/tasks/new.md", cwd=product)
    head = commit(product, "rename into the tasks directory")
    ws = GitWorkspace(product, IDENTITY)
    assert ws.introduced_paths(product, base, head, base) == (".agentic/tasks/new.md",)


def test_names_come_back_unquoted(product):
    base = git("rev-parse", "HEAD", cwd=product)
    write(product, ".agentic/tasks/ñandú.md", "x\n")
    head = commit(product, "non-ascii")
    ws = GitWorkspace(product, IDENTITY)
    assert ws.introduced_paths(product, base, head, base) == (".agentic/tasks/ñandú.md",)


def test_fetched_upstream_reads_the_tracking_ref_without_fetching(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    git("init", "-q", "-b", "master", cwd=remote)
    write(remote, "app.py", "v1\n")
    first = commit(remote, "v1")
    clone = tmp_path / "clone"
    git("clone", "-q", str(remote), str(clone), cwd=tmp_path)
    ws = GitWorkspace(clone, IDENTITY)
    assert ws.fetched_upstream() == first
    # The remote moves; nothing is fetched, so the answer is the ref as it stands.
    write(remote, "app.py", "v2\n")
    commit(remote, "v2")
    assert ws.fetched_upstream() == first


def test_fetched_upstream_is_none_without_a_tracking_ref(product):
    assert GitWorkspace(product, IDENTITY).fetched_upstream() is None
