"""`GitEvalRoots` against a temporary git repository: the only part of the evaluation driver
that touches real git (GH-470)."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.measurement.adapters.git.eval_roots import GitEvalRoots
from agentic.core.process.adapters.git.workspace import GitWorkspace

ID = "EVAL-7-{sha8}-default-1"


def git(cwd: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "product"
    root.mkdir()
    git(root, "init", "-q", "-b", "master")
    (root / ".agentic").mkdir()
    (root / ".agentic/stack.yaml").write_text("product: p\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "with a stack")
    with_stack = git(root, "rev-parse", "HEAD")
    (root / ".agentic/stack.yaml").unlink()
    git(root, "commit", "-qam", "no stack")
    return root, with_stack, git(root, "rev-parse", "HEAD")


def test_resolves_and_stack_are_read_off_the_base_and_not_the_checkout(repo):
    root, with_stack, without = repo
    roots = GitEvalRoots(root)
    assert roots.resolves(with_stack) and roots.resolves(without)
    assert not roots.resolves("f" * 40)
    assert roots.has_stack(with_stack)
    assert not roots.has_stack(without)  # absent at that base, whatever else exists


def test_a_root_is_a_detached_worktree_at_the_base_and_leaves_master_clean(repo):
    root, with_stack, _ = repo
    roots = GitEvalRoots(root)
    eval_id = ID.format(sha8=with_stack[:8])
    created = roots.create(eval_id, with_stack)
    assert created == root / ".agentic/eval" / eval_id
    assert git(created, "rev-parse", "HEAD") == with_stack
    assert (created / ".agentic/stack.yaml").is_file()
    assert git(created, "branch", "--show-current") == ""  # detached
    assert roots.ids() == [eval_id]
    GitWorkspace(root, {}).exclude_system_files()
    assert git(root, "status", "--porcelain") == ""  # the root is the system's, not the product's


def test_a_task_branch_made_in_the_root_records_the_base_as_its_start(repo):
    root, with_stack, _ = repo
    roots = GitEvalRoots(root)
    eval_id = ID.format(sha8=with_stack[:8])
    created = roots.create(eval_id, with_stack)
    git(created, "checkout", "-q", "-b", f"agentic/{eval_id}")
    assert git(root, "merge-base", f"agentic/{eval_id}", with_stack) == with_stack


def test_removing_a_root_and_its_branches_leaves_the_others_alone(repo):
    root, with_stack, without = repo
    roots = GitEvalRoots(root)
    one, ten = ID.format(sha8=with_stack[:8]), f"EVAL-7-{with_stack[:8]}-default-10"
    created = roots.create(one, with_stack)
    git(created, "checkout", "-q", "-b", f"agentic/{one}")
    git(root, "branch", f"agentic/{one}.unit-a", with_stack)
    git(root, "branch", f"agentic/{ten}", with_stack)  # starts with the other id: not its branch
    git(root, "branch", "agentic/GH-7", with_stack)
    roots.remove(one)
    roots.delete_branches(one)
    assert roots.ids() == []
    assert not created.exists()
    assert "worktree" not in git(root, "worktree", "list").split("\n", 1)[-1]
    branches = git(root, "branch", "--format=%(refname:short)").split()
    assert sorted(branches) == sorted(["master", f"agentic/{ten}", "agentic/GH-7"])
    roots.remove(one)  # already gone: a no-op
