"""GH-824: `is_checkout` says whether a directory has a `.git` (file or directory), on the
real `GitWorkspace`, and never raises."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.shared.configuration import Configuration


@pytest.fixture
def workspace(tmp_path: Path) -> GitWorkspace:
    return GitWorkspace(tmp_path / "p", Configuration(product_dir=tmp_path / "p").git_identity)


def test_a_dot_git_file_or_directory_is_a_checkout(workspace: GitWorkspace, tmp_path: Path) -> None:
    linked, main = tmp_path / "linked", tmp_path / "main"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: /elsewhere\n")
    (main / ".git").mkdir(parents=True)
    assert workspace.is_checkout(linked) is True
    assert workspace.is_checkout(main) is True


def test_a_directory_without_dot_git_or_a_missing_one_is_not(
    workspace: GitWorkspace, tmp_path: Path
) -> None:
    (tmp_path / "bare").mkdir()
    assert workspace.is_checkout(tmp_path / "bare") is False
    assert workspace.is_checkout(tmp_path / "missing") is False


def test_an_unreadable_path_answers_false_instead_of_raising(
    workspace: GitWorkspace, tmp_path: Path
) -> None:
    assert workspace.is_checkout(tmp_path / ("x" * 300)) is False
