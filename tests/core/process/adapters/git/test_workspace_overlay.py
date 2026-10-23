"""`GitWorkspace.overlay`: a path as an older commit holds it, copied into a checkout of
another commit, and nothing written anywhere else (GH-318)."""

import os
import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.git.workspace import GitError


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
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "v1", cwd=repo)
    return repo


def commit(repo: Path, message: str) -> str:
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def workspace(product: Path) -> GitWorkspace:
    return GitWorkspace(product, {})


def test_a_path_of_another_commit_lands_in_a_detached_checkout_and_nothing_else_moves(
    product: Path, workspace: GitWorkspace
):
    old = git("rev-parse", "HEAD", cwd=product)
    (product / "scripts").mkdir()
    script = product / "scripts" / "measure.sh"
    script.write_text("#!/bin/sh\necho new\n")
    script.chmod(0o755)
    (product / "scripts" / "seed.sh").write_text("seed\n")
    (product / "app.py").write_text("v2\n")
    head = commit(product, "scripts")
    checkout = workspace.detached_checkout("perf-x", old)
    try:
        assert not (checkout / "scripts").exists()
        workspace.overlay(checkout, head, ["scripts"])
        assert (checkout / "scripts" / "measure.sh").read_text() == "#!/bin/sh\necho new\n"
        assert os.access(checkout / "scripts" / "measure.sh", os.X_OK)
        assert (checkout / "scripts" / "seed.sh").read_text() == "seed\n"
        assert (checkout / "app.py").read_text() == "v1\n"  # only the named paths
        assert git("rev-parse", "HEAD", cwd=checkout) == old  # not committed, not moved
        assert not (product / "perf-x").exists()
        assert git("status", "--porcelain", cwd=product) == ""  # the product's tree is untouched
    finally:
        workspace.remove_checkout("perf-x")


def test_a_single_file_path_is_copied_too(product: Path, workspace: GitWorkspace):
    old = git("rev-parse", "HEAD", cwd=product)
    (product / "tool.sh").write_text("t\n")
    head = commit(product, "tool")
    checkout = workspace.detached_checkout("perf-y", old)
    try:
        workspace.overlay(checkout, head, ["tool.sh"])
        assert (checkout / "tool.sh").read_text() == "t\n"
    finally:
        workspace.remove_checkout("perf-y")


def test_a_path_the_commit_lacks_raises_git_error(product: Path, workspace: GitWorkspace):
    old = git("rev-parse", "HEAD", cwd=product)
    checkout = workspace.detached_checkout("perf-z", old)
    try:
        with pytest.raises(GitError):
            workspace.overlay(checkout, old, ["no/such/dir"])
        with pytest.raises(GitError):
            workspace.overlay(checkout, "0" * 40, ["app.py"])
    finally:
        workspace.remove_checkout("perf-z")


def test_a_path_that_leaves_the_checkout_raises_and_writes_nothing_outside(
    product: Path, workspace: GitWorkspace, tmp_path: Path
):
    old = git("rev-parse", "HEAD", cwd=product)
    checkout = workspace.detached_checkout("perf-w", old)
    try:
        for bad in ("../outside", "/etc/passwd", "a/../../outside", ""):
            with pytest.raises(GitError):
                workspace.overlay(checkout, old, [bad])
        with pytest.raises(GitError):
            workspace.overlay(checkout, old, [])  # nothing to copy is a mistake, not a no-op
        assert not (checkout.parent / "outside").exists()
    finally:
        workspace.remove_checkout("perf-w")


def test_a_link_pointing_outside_the_checkout_is_refused(product: Path, workspace: GitWorkspace):
    old = git("rev-parse", "HEAD", cwd=product)
    (product / "evil").symlink_to("../../outside")
    head = commit(product, "link")
    checkout = workspace.detached_checkout("perf-v", old)
    try:
        with pytest.raises(GitError):
            workspace.overlay(checkout, head, ["evil"])
    finally:
        workspace.remove_checkout("perf-v")
