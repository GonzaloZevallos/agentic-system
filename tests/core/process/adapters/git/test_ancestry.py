"""`GitAncestry.contains` on a real temporary git repository (GH-1067, invariant 3)."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git.ancestry import GitAncestry


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, str]:
    """Three commits on main, a side-branch commit off the first; HEAD on main."""
    _git(tmp_path, "init", "-q", "-b", "main")
    shas: dict[str, str] = {}
    for name in ("first", "second", "third"):
        (tmp_path / "f.txt").write_text(name)
        _git(tmp_path, "add", "f.txt")
        _git(tmp_path, "commit", "-q", "-m", name)
        shas[name] = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", "-b", "side", shas["first"])
    (tmp_path / "g.txt").write_text("side")
    _git(tmp_path, "add", "g.txt")
    _git(tmp_path, "commit", "-q", "-m", "side")
    shas["side"] = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", "main")
    shas["path"] = str(tmp_path)
    return shas


def test_an_ancestor_of_head_is_contained(repo):
    ancestry = GitAncestry(Path(repo["path"]))

    assert ancestry.contains(repo["first"]) is True
    assert ancestry.contains(repo["third"]) is True
    assert ancestry.contains(repo["second"][:7]) is True


def test_a_commit_off_head_is_present_but_not_contained(repo):
    assert GitAncestry(Path(repo["path"])).contains(repo["side"]) is False


def test_an_unknown_well_formed_sha_is_unknown(repo):
    assert (
        GitAncestry(Path(repo["path"])).contains("0123456789abcdef0123456789abcdef01234567") is None
    )


def test_a_directory_that_is_not_a_repository_is_unknown(tmp_path):
    assert GitAncestry(tmp_path).contains("a" * 40) is None


@pytest.mark.parametrize("sha", ["-x", "HEAD~1", "", "ABCDEF1234", "abc12", "a" * 41, "abc123g"])
def test_a_malformed_sha_is_unknown_and_runs_nothing(tmp_path, sha):
    calls: list[list[str]] = []

    def run(argv, **_):
        calls.append(argv)
        raise AssertionError("must not run")

    assert GitAncestry(tmp_path, run=run).contains(sha) is None
    assert calls == []


def test_a_timeout_or_a_missing_git_is_unknown(tmp_path):
    def timing_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    def no_git(argv, **_):
        raise FileNotFoundError("git")

    assert GitAncestry(tmp_path, run=timing_out).contains("a" * 40) is None
    assert GitAncestry(tmp_path, run=no_git).contains("a" * 40) is None


def test_an_exit_other_than_0_or_1_from_the_ancestry_check_is_unknown(tmp_path):
    seen: list[list[str]] = []

    def run(argv, **kwargs):
        seen.append(argv)
        code = 0 if argv[1] == "cat-file" else 128
        return subprocess.CompletedProcess(argv, code, "", "")

    assert GitAncestry(tmp_path, run=run).contains("a" * 40) is None
    assert [call[1] for call in seen] == ["cat-file", "merge-base"]


def test_it_only_reads(repo):
    path = Path(repo["path"])
    before = _git(path, "for-each-ref") + _git(path, "status", "--porcelain")

    GitAncestry(path).contains(repo["side"])

    assert _git(path, "for-each-ref") + _git(path, "status", "--porcelain") == before
