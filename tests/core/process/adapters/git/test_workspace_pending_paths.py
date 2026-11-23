"""`GitWorkspace.pending_paths` against a real temporary repository (GH-1093): what a working
tree has pending against HEAD, read from the unstripped NUL-separated `git status`."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.git.workspace import parse_pending_paths
from agentic.core.process.application.ports import GitError
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(repo: Path, message: str) -> None:
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)


@pytest.fixture
def product(tmp_path: Path) -> Path:
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "a.py").write_text("a\n")
    (repo / "b.py").write_text("b\n")
    (repo / "old_name.py").write_text("one\ntwo\nthree\nfour\n")
    (repo / "old name.py").write_text("one\ntwo\nthree\nfour\nfive\n")
    commit(repo, "v1")
    return repo


def pending(product: Path) -> tuple[str, ...]:
    return GitWorkspace(product, IDENTITY).pending_paths(product)


def test_a_clean_tree_has_nothing_pending(product):
    assert pending(product) == ()


def test_an_unstaged_edit_alone_keeps_its_leading_space_and_is_read(product):
    # ` M a.py`: the record a stripping read would turn into a malformed one.
    (product / "a.py").write_text("a2\n")

    assert pending(product) == ("a.py",)


def test_an_untracked_file_inside_an_untracked_directory_is_named_by_file(product):
    (product / "new" / "deep").mkdir(parents=True)
    (product / "new" / "deep" / "c.py").write_text("c\n")

    assert pending(product) == ("new/deep/c.py",)


def test_a_staged_add_is_pending(product):
    (product / "c.py").write_text("c\n")
    git("add", "c.py", cwd=product)

    assert pending(product) == ("c.py",)


def test_a_staged_and_an_unstaged_delete_are_pending(product):
    git("rm", "-q", "a.py", cwd=product)
    (product / "b.py").unlink()

    assert set(pending(product)) == {"a.py", "b.py"}


def test_a_staged_rename_gives_the_new_path_then_the_old_one(product):
    git("mv", "old_name.py", "new_name.py", cwd=product)

    assert pending(product) == ("new_name.py", "old_name.py")


def test_a_rename_with_a_space_in_a_name_gives_both_names_raw(product):
    git("mv", "old name.py", "new name.py", cwd=product)

    assert pending(product) == ("new name.py", "old name.py")


def test_a_name_with_a_newline_comes_back_raw(product):
    (product / "line\nbreak.py").write_text("x\n")

    assert pending(product) == ("line\nbreak.py",)


def test_an_ignored_file_does_not_appear(product):
    (product / "debug.log").write_text("noise\n")

    assert pending(product) == ()


def test_a_directory_that_is_not_a_repository_raises(tmp_path):
    bare = tmp_path / "plain"
    bare.mkdir()

    with pytest.raises(GitError):
        GitWorkspace(bare, IDENTITY).pending_paths(bare)


@pytest.mark.parametrize(
    "output",
    [
        "M\0",  # too short
        "M a.py\0",  # no space at index 2
        " M \0",  # empty path
        "R  new.py\0",  # a rename with no original record
        "R  new.py\0\0",  # a rename with an empty original
    ],
)
def test_a_malformed_record_raises(output):
    with pytest.raises(GitError):
        parse_pending_paths(output)


def test_a_repeated_path_is_named_once_in_first_seen_order():
    assert parse_pending_paths("R  b.py\0a.py\0 M a.py\0") == ("b.py", "a.py")


@pytest.mark.parametrize("fixture", ["clean", "ignored", "untracked", "edit", "rename"])
def test_pending_is_empty_exactly_when_the_tree_is_clean(product, fixture):
    if fixture == "ignored":
        (product / "x.log").write_text("x\n")
    elif fixture == "untracked":
        (product / "d").mkdir()
        (product / "d" / "f.py").write_text("f\n")
    elif fixture == "edit":
        (product / "a.py").write_text("a2\n")
    elif fixture == "rename":
        git("mv", "old_name.py", "renamed.py", cwd=product)

    workspace = GitWorkspace(product, IDENTITY)

    assert (workspace.pending_paths(product) == ()) == workspace.is_clean(product)
