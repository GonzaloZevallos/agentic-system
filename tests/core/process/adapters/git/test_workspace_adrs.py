"""The two-phase reads of a branch's own ADRs, and `commit_paths` on a rename, against a
real repository (GH-229): master and a branch that both took ADR 0049, the branch having
merged master after master took it."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.git.workspace import GitError
from agentic.core.process.application.ports import AddedAdr, AddedLine
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity
AUTHOR = ("-c", "user.name=t", "-c", "user.email=t@t")


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str]) -> str:
    """Write `files` (path -> text), commit them all, answer the sha."""
    for name, text in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    git("add", "-A", cwd=repo)
    git(*AUTHOR, "commit", "-q", "-m", message, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


@dataclass(frozen=True)
class Collision:
    repo: Path
    master: str  # master's tip, M: it took ADR 0049 after the branch did
    head: str
    first: str  # the branch commit that added its 0049
    second: str  # the branch commit that cites it again, after the merge


@pytest.fixture
def collision(tmp_path: Path) -> Collision:
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    commit(repo, "v0", {"docs/adr/0001-a.md": "# 1\n"})
    git("checkout", "-q", "-b", "feature", cwd=repo)
    first = commit(
        repo,
        "branch adds its ADR",
        {
            "docs/adr/0049-mine.md": "# ADR-0049 mine\n",
            "docs/notes.md": "see ADR-0049 and ADR-0001\nplain line\n",
        },
    )
    git("checkout", "-q", "master", cwd=repo)
    commit(repo, "master takes 0049", {"docs/adr/0049-theirs.md": "# ADR-0049 theirs\n"})
    git("checkout", "-q", "feature", cwd=repo)
    git(*AUTHOR, "merge", "-q", "--no-ff", "-m", "merge master", "master", cwd=repo)
    second = commit(repo, "cite it again", {"docs/more.md": "ADR-0049 again\nADR-0001\n"})
    git("checkout", "-q", "master", cwd=repo)
    commit(repo, "master moves on", {"docs/master.md": "ADR-0049 on master only\n"})
    master = git("rev-parse", "HEAD", cwd=repo)
    return Collision(repo, master, git("rev-parse", "feature", cwd=repo), first, second)


def test_added_adrs_names_only_the_files_the_branch_own_commits_added(collision):
    ws = GitWorkspace(collision.repo, IDENTITY)

    added = ws.added_adrs(collision.master, collision.head)

    assert added == (AddedAdr("docs/adr/0049-mine.md", collision.first),)


def test_added_lines_are_the_branch_own_and_leave_out_what_master_brought(collision):
    ws = GitWorkspace(collision.repo, IDENTITY)

    lines = ws.added_lines(collision.master, collision.head, ["0049"])

    # Newest commit first, as `git log` lists them.
    assert lines == (
        AddedLine("docs/more.md", collision.second, "ADR-0049 again"),
        AddedLine("docs/adr/0049-mine.md", collision.first, "# ADR-0049 mine"),
        AddedLine("docs/notes.md", collision.first, "see ADR-0049 and ADR-0001"),
    )
    # Master's own 0049 lines (its ADR, and the one it added after) are not in the list.
    assert all("theirs" not in line.text and "master only" not in line.text for line in lines)


def test_added_lines_answers_a_line_once_when_it_holds_two_numbers(collision):
    ws = GitWorkspace(collision.repo, IDENTITY)

    lines = ws.added_lines(collision.master, collision.head, ["0049", "0001"])

    texts = [line.text for line in lines]
    assert texts.count("see ADR-0049 and ADR-0001") == 1
    assert texts.count("ADR-0001") == 1
    assert len(lines) == 4


def test_added_lines_attribute_a_line_to_its_own_commit_past_binary_and_plus_lines(tmp_path):
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    base = commit(repo, "v0", {"a.md": "x\n"})
    (repo / "blob.bin").write_bytes(b"\x00\x010049\x00")
    binary = commit(repo, "a binary file", {"docs/b.md": "++ 0049 is a plus line\n"})
    last = commit(repo, "the line", {"docs/c.md": "ADR-0049\n"})
    ws = GitWorkspace(repo, IDENTITY)

    lines = ws.added_lines(base, last, ["0049"])

    assert lines == (
        AddedLine("docs/c.md", last, "ADR-0049"),
        AddedLine("docs/b.md", binary, "++ 0049 is a plus line"),
    )


@pytest.mark.parametrize("number", ["49", "00490", "00 9", "００４９", "004x", "", "0049\n"])
def test_added_lines_rejects_anything_but_four_digits_before_git_runs(tmp_path, number):
    # No repository at all: git would fail on its own, so a `ValueError` came first.
    ws = GitWorkspace(tmp_path / "nowhere", IDENTITY)

    with pytest.raises(ValueError, match="four digits"):
        ws.added_lines("master", "head", ["0001", number])


def test_added_lines_of_no_numbers_asks_git_nothing(tmp_path):
    assert GitWorkspace(tmp_path / "nowhere", IDENTITY).added_lines("m", "h", []) == ()


def test_tree_names_are_base_names_per_commit(collision):
    ws = GitWorkspace(collision.repo, IDENTITY)

    assert ws.tree_names(collision.first, "docs/adr") == ("0001-a.md", "0049-mine.md")
    assert ws.tree_names(collision.head, "docs/adr") == (
        "0001-a.md",
        "0049-mine.md",
        "0049-theirs.md",
    )
    assert ws.tree_names(collision.master, "docs/adr") == ("0001-a.md", "0049-theirs.md")
    assert ws.tree_names(collision.master, "docs/missing") == ()


def test_tree_names_of_a_ref_git_does_not_know_raises(collision):
    with pytest.raises(GitError, match="ls-tree"):
        GitWorkspace(collision.repo, IDENTITY).tree_names("no-such-ref", "docs/adr")


def test_added_adrs_of_a_ref_git_does_not_know_raises(collision):
    with pytest.raises(GitError, match="log"):
        GitWorkspace(collision.repo, IDENTITY).added_adrs("no-such-ref", collision.head)


def test_added_adrs_is_empty_for_a_branch_that_added_none(collision):
    ws = GitWorkspace(collision.repo, IDENTITY)

    assert ws.added_adrs(collision.master, collision.master) == ()
    assert ws.added_adrs(collision.first, collision.first) == ()


def test_commit_paths_commits_a_rename_as_an_add_and_a_delete(collision):
    ws = GitWorkspace(collision.repo, IDENTITY)
    task = collision.repo / ".agentic/worktrees/GH-229"
    git("worktree", "add", "-q", str(task), "feature", cwd=collision.repo)
    (task / "docs/adr/0049-mine.md").rename(task / "docs/adr/0050-mine.md")
    (task / "docs/notes.md").write_text("see ADR-0050\n")
    (task / "stray.txt").write_text("untracked\n")

    sha = ws.commit_paths(
        task, ["docs/adr/0049-mine.md", "docs/adr/0050-mine.md", "docs/notes.md"], "renumber"
    )

    assert sha == ws.head(task)
    changes = git(
        "diff-tree", "--no-commit-id", "--name-status", "-r", "--no-renames", "HEAD", cwd=task
    )
    assert sorted(changes.splitlines()) == [
        "A\tdocs/adr/0050-mine.md",
        "D\tdocs/adr/0049-mine.md",
        "M\tdocs/notes.md",
    ]
    assert git("status", "--porcelain", cwd=task) == "?? stray.txt"
    # The rename is the branch's own ADR under its new number, added by that commit.
    added = ws.added_adrs(collision.master, "feature")
    assert AddedAdr("docs/adr/0050-mine.md", git("rev-parse", "HEAD", cwd=task)) in added


def test_a_head_that_is_already_local_is_made_local_without_a_fetch(
    collision: Collision, monkeypatch: pytest.MonkeyPatch
):
    """The property is which git commands the adapter issues, so calls are counted (the
    bounded runner does not raise on a failure)."""
    ws = GitWorkspace(collision.repo, IDENTITY)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(ws, "_run_bounded", lambda *args: calls.append(args))
    assert ws.fetch_commit(collision.head) is True
    assert calls == []


def test_a_head_that_is_absent_costs_exactly_one_bounded_fetch(
    collision: Collision, monkeypatch: pytest.MonkeyPatch
):
    ws = GitWorkspace(collision.repo, IDENTITY)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(ws, "_run_bounded", lambda *args: calls.append(args))
    absent = "f" * 40
    assert ws.fetch_commit(absent) is False
    assert calls == [("fetch", "--quiet", "origin", absent)]
