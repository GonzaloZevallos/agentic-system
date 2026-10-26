"""The git adapter's tree reads and the setup-tree record, against a real temporary
repository (ADR-0065)."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity


def git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def product(tmp_path):
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / "README.md").write_text("v0\n")
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "v0", cwd=repo)
    return repo


def test_tree_is_head_caret_tree_and_follows_the_content_not_the_commit(product):
    ws = GitWorkspace(product, IDENTITY)
    assert ws.tree(product) == git("rev-parse", "HEAD^{tree}", cwd=product)
    before = ws.tree(product)
    # An empty commit moves HEAD and leaves the tree.
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "e",
        cwd=product)  # fmt: skip
    assert ws.tree(product) == before
    (product / "README.md").write_text("v1\n")
    ws.commit_all(product, "v1")
    assert ws.tree(product) != before


def test_the_setup_tree_record_round_trips_in_a_worktree(product):
    ws = GitWorkspace(product, IDENTITY)
    checkout = ws.task_checkout("GH-1")
    assert ws.setup_tree(checkout.path) is None
    ws.record_setup_tree(checkout.path)
    assert ws.setup_tree(checkout.path) == ws.tree(checkout.path)
    # The record lives in the worktree's git dir: never in the checkout's files.
    assert git("status", "--porcelain", cwd=checkout.path) == ""
    # And it belongs to that worktree alone.
    assert ws.setup_tree(product) is None


def test_the_record_keeps_the_tree_it_was_made_on_after_the_head_moves(product):
    ws = GitWorkspace(product, IDENTITY)
    checkout = ws.task_checkout("GH-1")
    ws.record_setup_tree(checkout.path)
    recorded = ws.tree(checkout.path)
    (checkout.path / "new.py").write_text("x = 1\n")
    ws.commit_all(checkout.path, "more")
    assert ws.setup_tree(checkout.path) == recorded
    assert ws.tree(checkout.path) != recorded


@pytest.mark.parametrize(
    "garbage",
    ["", "garbage", "-h", "--all", "0" * 40, "../../etc/passwd", "HEAD"],
)
def test_a_garbage_record_reads_as_none(product, garbage):
    ws = GitWorkspace(product, IDENTITY)
    ws.record_setup_tree(product)
    record = Path(git("rev-parse", "--git-path", "agentic-setup-tree", cwd=product))
    (product / record if not record.is_absolute() else record).write_text(garbage)
    assert ws.setup_tree(product) is None


def test_a_commit_id_in_the_record_is_not_a_tree(product):
    ws = GitWorkspace(product, IDENTITY)
    ws.record_setup_tree(product)
    record = Path(git("rev-parse", "--git-path", "agentic-setup-tree", cwd=product))
    path = product / record if not record.is_absolute() else record
    path.write_text(git("rev-parse", "HEAD", cwd=product))
    assert ws.setup_tree(product) is None
