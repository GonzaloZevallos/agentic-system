"""The git adapter against a real temporary repository with a bare remote."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.shared.configuration import Configuration

# The identity as the composition root hands it in.
IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity


def git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def product(tmp_path):
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-q", str(remote), cwd=tmp_path)
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / "README.md").write_text("v0\n")
    git("add", "-A", cwd=repo)
    # The product's first commit needs an identity; the adapter's commits must not.
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "v0", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    return repo


def test_task_checkout_is_a_worktree_on_its_own_branch_and_idempotent(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    assert task.branch == "agentic/GH-12"
    assert task.path == product / ".agentic/worktrees/GH-12"
    assert (task.path / "README.md").read_text() == "v0\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=task.path) == "agentic/GH-12"
    assert task.base == git("rev-parse", "--short", "HEAD", cwd=product)
    assert ws.task_checkout("GH-12") == task
    # The product's own checkout is untouched, and the worktrees never show up in it.
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=product) == "master"
    assert git("status", "--porcelain", cwd=product) == ""


def test_subtask_branches_off_the_task_branch_as_it_is_now(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    (task.path / "CONTEXT.md").write_text("glossary\n")
    sha = ws.commit_all(task.path, "GH-12: glossary")
    assert sha == ws.head(task.path) and sha != task.base
    # Signed by the system, on a repo with no `user.*` configured at all, and the
    # identity beats whatever `user.*` the product's config carries.
    git("config", "user.name", "human", cwd=product)
    git("config", "user.email", "human@example.com", cwd=product)
    (task.path / "ADR.md").write_text("adr\n")
    sha = ws.commit_all(task.path, "GH-12: adr")
    author = git("log", "-1", "--format=%an <%ae>", cwd=task.path)
    assert author == "agentic-system <agentic-system@users.noreply.github.com>"
    assert git("log", "-1", "--format=%cn <%ce>", cwd=task.path) == author
    assert ws.commit_all(task.path, "nothing") is None

    sub = ws.subtask_checkout("GH-12", "api")
    assert sub.branch == "agentic/GH-12.api"
    assert sub.path == product / ".agentic/worktrees/GH-12.api"
    assert (sub.path / "CONTEXT.md").read_text() == "glossary\n"  # from the task branch
    assert sub.base == sha
    # A later commit on the subtask moves its head; the base stays what the review diffs from.
    (sub.path / "api.py").write_text("x\n")
    ws.commit_all(sub.path, "api")
    assert ws.subtask_checkout("GH-12", "api").base == sha
    assert ws.head(sub.path) != sha

    # A subtask that depends on `api` gets its code by merging the branch; twice is a no-op.
    dep = ws.subtask_checkout("GH-12", "ui")
    ws.merge(dep.path, sub.branch)
    ws.merge(dep.path, sub.branch)
    assert (dep.path / "api.py").read_text() == "x\n"


def test_the_identity_is_the_environment_the_configuration_builds(product):
    identity = {
        "GIT_AUTHOR_NAME": "bot",
        "GIT_AUTHOR_EMAIL": "b@x",
        "GIT_COMMITTER_NAME": "bot",
        "GIT_COMMITTER_EMAIL": "b@x",
    }
    ws = GitWorkspace(product, identity)
    task = ws.task_checkout("GH-12")
    (task.path / "x").write_text("x\n")
    ws.commit_all(task.path, "x")
    assert git("log", "-1", "--format=%an <%ae>", cwd=task.path) == "bot <b@x>"


def test_push_and_cleanup_keep_the_branches(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    ws.subtask_checkout("GH-12", "api")
    ws.subtask_checkout("GH-13", "other")
    ws.push(task.branch)
    assert git("branch", "-r", cwd=product) == "origin/agentic/GH-12"

    ws.cleanup("GH-12")
    root = product / ".agentic/worktrees"
    assert sorted(p.name for p in root.iterdir()) == ["GH-13", "GH-13.other"]
    branches = git("branch", "--format=%(refname:short)", cwd=product).splitlines()
    assert {"agentic/GH-12", "agentic/GH-12.api", "agentic/GH-13"} <= set(branches)
    ws.cleanup("GH-99")  # nothing to do, no error
