"""The revision verbs the merge verb reads a pull request with (GH-229): merge-base, the
files under a directory at a revision, the lines a branch added, and landing a commit made
in a throwaway worktree on a branch under a lease. Real git: a bare upstream and clones."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.git.workspace import GitError, LeaseLost
from agentic.core.process.domain.added_lines import AddedLines
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


def write(root: Path, relative: str, text: str | bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        target.write_bytes(text)
    else:
        target.write_text(text, encoding="utf-8")


def commit(root: Path, message: str) -> str:
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", message, cwd=root)
    return git("rev-parse", "HEAD", cwd=root)


@pytest.fixture
def clone(tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "--bare", "-q", "-b", "master", str(origin), cwd=tmp_path)
    repo = tmp_path / "clone"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    git("remote", "add", "origin", str(origin), cwd=repo)
    write(repo, "README.md", "v0\n")
    commit(repo, "v0")
    git("push", "-q", "origin", "master", cwd=repo)
    return repo


# -- merge_base ------------------------------------------------------------------


def test_merge_base_is_the_full_sha_where_two_lines_of_history_met(clone):
    ws = GitWorkspace(clone, IDENTITY)
    fork = git("rev-parse", "HEAD", cwd=clone)
    git("checkout", "-q", "-b", "topic", cwd=clone)
    write(clone, "a.txt", "a\n")
    topic = commit(clone, "topic")
    git("checkout", "-q", "master", cwd=clone)
    write(clone, "b.txt", "b\n")
    master = commit(clone, "master")

    assert ws.merge_base(master, topic) == fork
    assert ws.merge_base(topic, master) == fork


def test_merge_base_across_a_merge_commit_is_the_newest_common_ancestor(clone):
    """The branch took master in: the base moves up to the master commit it merged."""
    ws = GitWorkspace(clone, IDENTITY)
    git("checkout", "-q", "-b", "topic", cwd=clone)
    write(clone, "a.txt", "a\n")
    commit(clone, "topic")
    git("checkout", "-q", "master", cwd=clone)
    write(clone, "b.txt", "b\n")
    merged_in = commit(clone, "master 1")
    git("checkout", "-q", "topic", cwd=clone)
    git("merge", "-q", "--no-edit", "master", cwd=clone)
    merge_commit = git("rev-parse", "HEAD", cwd=clone)
    git("checkout", "-q", "master", cwd=clone)
    write(clone, "c.txt", "c\n")
    newer = commit(clone, "master 2")

    assert ws.merge_base(newer, merge_commit) == merged_in


def test_merge_base_of_unrelated_histories_raises_git_error(clone):
    ws = GitWorkspace(clone, IDENTITY)
    master = git("rev-parse", "HEAD", cwd=clone)
    git("checkout", "-q", "--orphan", "island", cwd=clone)
    git("rm", "-rfq", ".", cwd=clone)
    write(clone, "z.txt", "z\n")
    island = commit(clone, "island")

    with pytest.raises(GitError):
        ws.merge_base(master, island)


# -- paths_at --------------------------------------------------------------------


def test_paths_at_lists_only_the_blobs_directly_under_the_directory_sorted(clone):
    ws = GitWorkspace(clone, IDENTITY)
    write(clone, "docs/adr/0002-b.md", "b\n")
    write(clone, "docs/adr/0001-a.md", "a\n")
    write(clone, "docs/adr/sub/0003-c.md", "c\n")
    write(clone, "docs/other.md", "o\n")
    rev = commit(clone, "adrs")

    assert ws.paths_at(rev, "docs/adr") == ("docs/adr/0001-a.md", "docs/adr/0002-b.md")


def test_paths_at_is_empty_where_the_directory_is_absent_and_reads_the_revision_asked(clone):
    ws = GitWorkspace(clone, IDENTITY)
    before = git("rev-parse", "HEAD", cwd=clone)
    write(clone, "docs/adr/0001-a.md", "a\n")
    after = commit(clone, "adr")

    assert ws.paths_at(before, "docs/adr") == ()
    assert ws.paths_at(after, "docs/adr") == ("docs/adr/0001-a.md",)


def test_paths_at_keeps_names_with_spaces_and_non_ascii_whole(clone):
    ws = GitWorkspace(clone, IDENTITY)
    write(clone, "docs/adr/0001-año nuevo.md", "a\n")
    rev = commit(clone, "adr")

    assert ws.paths_at(rev, "docs/adr") == ("docs/adr/0001-año nuevo.md",)


def test_paths_at_an_unknown_revision_raises_git_error(clone):
    with pytest.raises(GitError):
        GitWorkspace(clone, IDENTITY).paths_at("0" * 40, "docs/adr")


# -- added_lines -----------------------------------------------------------------


def test_added_lines_reads_an_add_an_edit_and_the_whole_new_files(clone):
    ws = GitWorkspace(clone, IDENTITY)
    write(clone, "keep.txt", "one\ntwo\nthree\nfour\n")
    base = commit(clone, "keep")
    write(clone, "keep.txt", "one\nTWO\nthree\nfour\nfive\n")
    write(clone, "docs/adr/0009-new.md", "# ADR-0009\n\nbody\n")
    write(clone, "empty.txt", "")
    head = commit(clone, "change")

    found = ws.added_lines(base, head)

    assert found.lines["keep.txt"] == ((2, "TWO"), (5, "five"))
    assert found.lines["docs/adr/0009-new.md"] == ((1, "# ADR-0009"), (2, ""), (3, "body"))
    assert found.whole == frozenset({"docs/adr/0009-new.md", "empty.txt"})
    assert found.lines.get("empty.txt", ()) == ()


def test_added_lines_shows_a_rename_as_a_deleted_file_and_a_whole_new_one(clone):
    ws = GitWorkspace(clone, IDENTITY)
    write(clone, "docs/adr/0005-old.md", "# ADR-0005\n\nsame\n")
    base = commit(clone, "adr")
    git("mv", "docs/adr/0005-old.md", "docs/adr/0006-old.md", cwd=clone)
    head = commit(clone, "rename")

    found = ws.added_lines(base, head)

    assert found.whole == frozenset({"docs/adr/0006-old.md"})
    assert found.lines["docs/adr/0006-old.md"] == ((1, "# ADR-0005"), (2, ""), (3, "same"))
    assert "docs/adr/0005-old.md" not in found.lines


def test_added_lines_skips_a_binary_file_and_a_deleted_one_adds_nothing(clone):
    ws = GitWorkspace(clone, IDENTITY)
    write(clone, "gone.txt", "x\n")
    base = commit(clone, "gone")
    git("rm", "-q", "gone.txt", cwd=clone)
    write(clone, "blob.bin", b"\x00\x01\x02\xff")
    write(clone, "after.txt", "ok\n")
    head = commit(clone, "binary")

    found = ws.added_lines(base, head)

    assert set(found.lines) == {"after.txt"}
    assert found.whole == frozenset({"after.txt"})


def test_added_lines_keeps_odd_paths_and_decodes_utf8_with_replacement(clone):
    ws = GitWorkspace(clone, IDENTITY)
    base = git("rev-parse", "HEAD", cwd=clone)
    write(clone, "dir with space/año.md", "ñandú\n".encode() + b"bad \xff byte\n")
    head = commit(clone, "odd")

    found = ws.added_lines(base, head)

    assert found.lines["dir with space/año.md"] == ((1, "ñandú"), (2, "bad � byte"))


def test_added_lines_keeps_text_that_looks_like_a_diff_header_and_odd_separators(clone):
    ws = GitWorkspace(clone, IDENTITY)
    base = git("rev-parse", "HEAD", cwd=clone)
    write(clone, "note.txt", "diff --git a/x b/x\n+++ b/y\nsplit here\n@@ -1 +1 @@\n")
    head = commit(clone, "tricky")

    found = ws.added_lines(base, head)

    assert found.lines["note.txt"] == (
        (1, "diff --git a/x b/x"),
        (2, "+++ b/y"),
        (3, "split here"),
        (4, "@@ -1 +1 @@"),
    )


def test_added_lines_of_identical_revisions_is_empty(clone):
    head = git("rev-parse", "HEAD", cwd=clone)

    assert GitWorkspace(clone, IDENTITY).added_lines(head, head) == AddedLines({}, frozenset())


def test_added_lines_of_an_unknown_revision_raises_git_error(clone):
    head = git("rev-parse", "HEAD", cwd=clone)

    with pytest.raises(GitError):
        GitWorkspace(clone, IDENTITY).added_lines("0" * 40, head)


# -- push_commit -----------------------------------------------------------------


def _remote_tip(clone: Path, branch: str) -> str:
    return git("ls-remote", "origin", f"refs/heads/{branch}", cwd=clone).split()[0]


def test_push_commit_lands_a_worktree_commit_as_the_system_under_the_observed_lease(clone):
    ws = GitWorkspace(clone, IDENTITY)
    task = ws.task_checkout("GH-9")
    write(task.path, "a.txt", "a\n")
    ws.commit_all(task.path, "task work")
    observed = ws.full_head(task.path)
    ws.push("agentic/GH-9")
    assert _remote_tip(clone, "agentic/GH-9") == observed

    scratch = ws.detached_checkout("GH-9.renumber", observed)
    write(scratch, "a.txt", "renumbered\n")
    ws.commit_all(scratch, "renumber")
    renumbered = ws.full_head(scratch)

    ws.push_commit(scratch, renumbered, "agentic/GH-9", lease=observed)

    assert _remote_tip(clone, "agentic/GH-9") == renumbered
    assert git("log", "-1", "--format=%an <%ae>", renumbered, cwd=clone) == (
        f"{IDENTITY['GIT_AUTHOR_NAME']} <{IDENTITY['GIT_AUTHOR_EMAIL']}>"
    )
    # The local task branch was never moved by it.
    assert git("rev-parse", "agentic/GH-9", cwd=clone) == observed
    ws.remove_checkout("GH-9.renumber")


def test_push_commit_loses_the_lease_when_someone_pushed_after_the_observation(clone, tmp_path):
    ws = GitWorkspace(clone, IDENTITY)
    task = ws.task_checkout("GH-9")
    write(task.path, "a.txt", "a\n")
    ws.commit_all(task.path, "task work")
    observed = ws.full_head(task.path)
    ws.push("agentic/GH-9")

    other = tmp_path / "other"
    git("clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
    git("checkout", "-q", "agentic/GH-9", cwd=other)
    write(other, "human.txt", "late\n")
    pushed_by_human = commit(other, "human")
    git("push", "-q", "origin", "agentic/GH-9", cwd=other)

    scratch = ws.detached_checkout("GH-9.renumber", observed)
    write(scratch, "a.txt", "renumbered\n")
    ws.commit_all(scratch, "renumber")
    renumbered = ws.full_head(scratch)

    with pytest.raises(LeaseLost):
        ws.push_commit(scratch, renumbered, "agentic/GH-9", lease=observed)

    assert _remote_tip(clone, "agentic/GH-9") == pushed_by_human
    ws.remove_checkout("GH-9.renumber")


def test_a_push_that_fails_for_another_reason_is_a_git_error_and_not_a_lost_lease(clone):
    ws = GitWorkspace(clone, IDENTITY)
    head = git("rev-parse", "HEAD", cwd=clone)
    git("remote", "set-url", "origin", str(clone.parent / "nowhere.git"), cwd=clone)

    with pytest.raises(GitError) as failure:
        ws.push_commit(clone, head, "agentic/GH-9", lease=head)

    assert not isinstance(failure.value, LeaseLost)


# -- the in-memory double ----------------------------------------------------------


def test_the_memory_workspace_answers_scripted_revisions_and_leases_like_git():
    from tests.fakes import MemoryWorkspace

    ws = MemoryWorkspace()
    ws.merge_bases[("m", "h")] = "mb"
    ws.tree_paths[("mb", "docs/adr")] = ("docs/adr/0001-a.md",)
    ws.added[("mb", "h")] = AddedLines({"f": ((1, "x"),)}, frozenset({"f"}))
    ws.remote_tips["agentic/T"] = "old"

    assert ws.merge_base("m", "h") == "mb"
    assert ws.paths_at("mb", "docs/adr") == ("docs/adr/0001-a.md",)
    assert ws.paths_at("m", "docs/adr") == ()
    assert ws.added_lines("mb", "h").whole == frozenset({"f"})
    assert ws.added_lines("x", "y") == AddedLines({}, frozenset())
    with pytest.raises(GitError):
        ws.merge_base("h", "m")
    with pytest.raises(LeaseLost):
        ws.push_commit(Path("/w"), "new", "agentic/T", lease="stale")
    assert ws.remote_tips["agentic/T"] == "old"
    ws.push_commit(Path("/w"), "new", "agentic/T", lease="old")
    assert ws.remote_tips["agentic/T"] == "new"
