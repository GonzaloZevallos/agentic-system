"""The setup through the real adapters: git makes the worktrees, the shell runs the
commands, and the stack comes from a real commit. The rest of the suite uses fakes, and
a fake cannot show that the commands land in the checkout git just made."""

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.knowledge.adapters.git import GitStack
from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.adapters.shell.commands import ShellCommands
from agentic.core.process.application.checkouts import SetupFailed, subtask_checkout
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

STACK = "setup:\n  mark: touch prepared.txt\nchecks:\n  tests: pytest\n"
STATE = {"id": "GH-37", "subtasks": {"api": {"id": "api"}, "domain": {"id": "domain"}}}


def git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def product(tmp_path):
    (tmp_path / ".agentic").mkdir()
    (tmp_path / ".agentic/stack.yaml").write_text(STACK)
    git("init", "-q", "-b", "master", ".", cwd=tmp_path)
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "v0", cwd=tmp_path)
    return tmp_path


@pytest.fixture
def real(product):
    cfg = Configuration(product_dir=product)
    ports = replace(
        memory_ports(),
        workspace=GitWorkspace(product, cfg.git_identity),
        stack=GitStack(product),
        commands=ShellCommands(),
    )
    return cfg, ports


def test_the_setup_runs_in_the_worktree_git_just_made_and_only_once(real, product):
    cfg, ports = real
    api = subtask_checkout(STATE, "api", cfg, ports)
    domain = subtask_checkout(STATE, "domain", cfg, ports)

    # Each worktree prepared in itself, and the product's own checkout untouched.
    assert (api.path / "prepared.txt").exists()
    assert (domain.path / "prepared.txt").exists()
    assert not (product / "prepared.txt").exists()

    (api.path / "prepared.txt").unlink()
    assert not subtask_checkout(STATE, "api", cfg, ports).created
    assert not (api.path / "prepared.txt").exists()  # asking again prepares nothing


def test_a_setup_that_fails_carries_the_command_and_what_it_printed(real, product):
    cfg, ports = real
    (product / ".agentic/stack.yaml").write_text("setup:\n  mark: exit 3\nchecks:\n  t: pytest\n")
    git("commit", "-qam", "break the setup", cwd=product)

    with pytest.raises(SetupFailed) as failure:
        subtask_checkout(STATE, "api", cfg, ports)
    assert failure.value.step == "mark" and failure.value.command == "exit 3"


def test_the_setup_comes_from_the_tasks_base_not_from_the_branch(real, product):
    """A task that edits the stack does not change the bar it is measured against: the
    branch is born here, and the commit after it must not reach the sister worktree."""
    cfg, ports = real
    subtask_checkout(STATE, "api", cfg, ports)  # the task branch is born at this commit
    (product / ".agentic/stack.yaml").write_text(
        "setup:\n  mark: touch later.txt\nchecks:\n  t: p\n"
    )
    git("commit", "-qam", "change the setup", cwd=product)

    domain = subtask_checkout(STATE, "domain", cfg, ports)
    assert (domain.path / "prepared.txt").exists()
    assert not (domain.path / "later.txt").exists()
