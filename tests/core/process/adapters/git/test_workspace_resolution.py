"""`merge_resolution` and `git_version` of the git adapter against a real repository
(GH-589, ADR-0089)."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application.ports import GitError, RemergeDiffUnsupported
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity


def git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "product"
    root.mkdir()
    git("init", "-q", "-b", "master", cwd=root)
    commit(root, "a.txt", "one\ntwo\nthree\n", "base")
    return root


def workspace(repo: Path) -> GitWorkspace:
    return GitWorkspace(repo, IDENTITY)


def test_a_clean_merge_has_no_resolution(repo):
    git("checkout", "-q", "-b", "task", cwd=repo)
    commit(repo, "own.txt", "mine\n", "own")
    git("checkout", "-q", "master", cwd=repo)
    commit(repo, "other.txt", "theirs\n", "master moves")
    git("checkout", "-q", "task", cwd=repo)
    git("merge", "-q", "--no-ff", "-m", "merge master", "master", cwd=repo)
    assert workspace(repo).merge_resolution(git("rev-parse", "HEAD", cwd=repo)) == ""


def test_a_conflict_resolved_by_hand_returns_its_resolution_hunk(repo):
    git("checkout", "-q", "-b", "task", cwd=repo)
    commit(repo, "a.txt", "one\nmine\nthree\n", "own edit")
    git("checkout", "-q", "master", cwd=repo)
    commit(repo, "a.txt", "one\ntheirs\nthree\n", "master edit")
    git("checkout", "-q", "task", cwd=repo)
    subprocess.run(["git", "merge", "--no-ff", "-q", "master"], cwd=repo, capture_output=True)
    (repo / "a.txt").write_text("one\nresolved\nthree\n")
    git("add", "a.txt", cwd=repo)
    git("commit", "-q", "-m", "merge master", cwd=repo)
    resolution = workspace(repo).merge_resolution(git("rev-parse", "HEAD", cwd=repo))
    assert "+resolved" in resolution
    assert "a.txt" in resolution


def test_git_version_parses_the_local_git(repo):
    version = workspace(repo).git_version()
    assert version is not None
    assert version >= (2, 0)


def test_an_unknown_merge_is_a_git_error(repo):
    with pytest.raises(GitError):
        workspace(repo).merge_resolution("0" * 40)


@pytest.mark.parametrize("version", [(2, 35), None])
def test_an_old_or_unknown_git_is_refused_without_running_the_command(repo, monkeypatch, version):
    ws = workspace(repo)
    ran: list[tuple[str, ...]] = []
    monkeypatch.setattr(ws, "git_version", lambda: version)
    monkeypatch.setattr(ws, "_read_z", lambda *args: ran.append(args) or "")
    with pytest.raises(RemergeDiffUnsupported):
        ws.merge_resolution("HEAD")
    assert ran == []
