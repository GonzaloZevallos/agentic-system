"""GH-603: the git operations that keep the owned docs paths out of a branch in `validate` mode
(`owned_drift`, `restore_owned`, `settle_owned`, `merge_settling`, `ancestry`), against real
repositories. The adapter receives path lists the domain already decided and never filters
them by glob: every path here is a literal, every pathspec an explicit `:(glob)` one."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application.ports import GitError, MergeConflict, MergeFailed
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity
SPECS = (":(glob)docs/gen/**",)
MESSAGE = "Restore the generated docs to master's side"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(root: Path, message: str) -> str:
    git("add", "-A", cwd=root)
    git("commit", "-qm", message, cwd=root)
    return git("rev-parse", "HEAD", cwd=root)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    git("init", "-q", "-b", "master", cwd=root)
    write(root, "docs/gen/a.md", "base\n")
    write(root, "app/code.py", "base\n")
    commit(root, "base")
    return root


def workspace(root: Path) -> GitWorkspace:
    return GitWorkspace(root, IDENTITY)


def unmerged(root: Path) -> list[str]:
    return git("diff", "--name-only", "--diff-filter=U", cwd=root).splitlines()


def merge_head(root: Path) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=root, capture_output=True
        ).returncode
        == 0
    )


def conflict_on(root: Path, relative: str, *, mixed: bool = False) -> tuple[str, str]:
    """`master` and a `feature` branch both rewrite `relative` (and `app/code.py` when
    `mixed`); the checkout ends on `feature`. Returns `(base, master_tip)`."""
    base = git("rev-parse", "HEAD", cwd=root)
    git("checkout", "-q", "-b", "feature", cwd=root)
    write(root, relative, "feature\n")
    if mixed:
        write(root, "app/code.py", "feature\n")
    commit(root, "feature")
    git("checkout", "-q", "master", cwd=root)
    write(root, relative, "master\n")
    if mixed:
        write(root, "app/code.py", "master\n")
    tip = commit(root, "master")
    git("checkout", "-q", "feature", cwd=root)
    return base, tip


def test_drift_ignores_a_local_master_that_is_behind_the_sha_it_is_given(repo):
    git("checkout", "-q", "-b", "feature", cwd=repo)
    write(repo, "app/code.py", "feature\n")
    commit(repo, "feature work")
    git("checkout", "-q", "master", cwd=repo)
    write(repo, "docs/gen/a.md", "regenerated\n")
    tip = commit(repo, "master regenerates")
    git("checkout", "-q", "feature", cwd=repo)
    git("merge", "-q", "--no-edit", tip, cwd=repo)
    # The local `master` falls behind the tip the system fetched: a name would read drift.
    git("branch", "-f", "master", "HEAD~2", cwd=repo)
    assert git("rev-parse", "master", cwd=repo) != tip

    drift = workspace(repo).owned_drift(repo, "HEAD", tip, SPECS)

    assert drift == ()


def test_drift_lists_the_owned_files_the_branch_committed(repo):
    tip = git("rev-parse", "HEAD", cwd=repo)
    git("checkout", "-q", "-b", "feature", cwd=repo)
    write(repo, "docs/gen/a.md", "committed\n")
    write(repo, "docs/gen/new.md", "new\n")
    write(repo, "app/code.py", "feature\n")
    commit(repo, "feature")

    drift = workspace(repo).owned_drift(repo, "HEAD", tip, SPECS)

    assert sorted(drift) == ["docs/gen/a.md", "docs/gen/new.md"]


def test_drift_with_no_pathspec_is_empty_rather_than_the_whole_diff(repo):
    tip = git("rev-parse", "HEAD", cwd=repo)
    write(repo, "app/code.py", "changed\n")
    commit(repo, "change")

    assert workspace(repo).owned_drift(repo, "HEAD", tip, ()) == ()


def test_drift_refuses_a_sha_the_repository_does_not_have(repo):
    with pytest.raises(GitError):
        workspace(repo).owned_drift(repo, "HEAD", "0" * 40, SPECS)


def test_drift_refuses_a_sha_with_no_merge_base(repo):
    git("checkout", "-q", "--orphan", "other", cwd=repo)
    write(repo, "x.txt", "x\n")
    orphan = commit(repo, "unrelated root")

    with pytest.raises(GitError):
        workspace(repo).owned_drift(repo, "master", orphan, SPECS)


def test_restore_puts_the_owned_files_back_as_the_source_has_them_and_commits(repo):
    source = git("rev-parse", "HEAD", cwd=repo)
    git("checkout", "-q", "-b", "feature", cwd=repo)
    write(repo, "docs/gen/a.md", "committed\n")
    write(repo, "docs/gen/new.md", "new\n")
    write(repo, "app/code.py", "feature\n")
    before = commit(repo, "feature")

    head = workspace(repo).restore_owned(
        repo, source, ("docs/gen/a.md", "docs/gen/new.md"), MESSAGE
    )

    assert head == git("rev-parse", "HEAD", cwd=repo) and head != before
    assert git("log", "-1", "--format=%s", cwd=repo) == MESSAGE
    assert (repo / "docs/gen/a.md").read_text() == "base\n"
    assert not (repo / "docs/gen/new.md").exists()
    assert (repo / "app/code.py").read_text() == "feature\n"
    assert git("status", "--porcelain", cwd=repo) == ""
    assert git("diff", "--name-only", before, head, cwd=repo).splitlines() == [
        "docs/gen/a.md",
        "docs/gen/new.md",
    ]


def test_restore_of_paths_that_already_match_commits_nothing(repo):
    source = git("rev-parse", "HEAD", cwd=repo)

    head = workspace(repo).restore_owned(repo, source, ("docs/gen/a.md",), MESSAGE)

    assert head == source
    assert git("rev-list", "--count", "HEAD", cwd=repo) == "1"


def test_a_failed_restore_leaves_the_original_head_and_a_clean_tree(repo):
    source = git("rev-parse", "HEAD", cwd=repo)
    write(repo, "docs/gen/a.md", "committed\n")
    before = commit(repo, "drift")

    # The checkout goes through; the commit is refused (an empty message): all is undone.
    with pytest.raises(GitError):
        workspace(repo).restore_owned(repo, source, ("docs/gen/a.md",), "")

    assert git("rev-parse", "HEAD", cwd=repo) == before
    assert (repo / "docs/gen/a.md").read_text() == "committed\n"
    assert git("status", "--porcelain", cwd=repo) == ""


def test_a_restore_from_an_unknown_source_fails_without_touching_the_tree(repo):
    write(repo, "docs/gen/a.md", "committed\n")
    before = commit(repo, "drift")

    with pytest.raises(GitError):
        workspace(repo).restore_owned(repo, "0" * 40, ("docs/gen/a.md",), MESSAGE)

    assert git("rev-parse", "HEAD", cwd=repo) == before
    assert git("status", "--porcelain", cwd=repo) == ""


def test_a_restore_on_a_dirty_tree_is_refused_and_keeps_the_edit(repo):
    source = git("rev-parse", "HEAD", cwd=repo)
    write(repo, "app/code.py", "uncommitted\n")

    with pytest.raises(GitError):
        workspace(repo).restore_owned(repo, source, ("docs/gen/a.md",), MESSAGE)

    assert (repo / "app/code.py").read_text() == "uncommitted\n"


def test_settle_takes_a_docs_only_conflict_from_the_source(repo):
    _, tip = conflict_on(repo, "docs/gen/a.md")
    outcome = workspace(repo).merge_keeping_conflicts(repo, tip)
    assert outcome.conflicted_paths == ("docs/gen/a.md",)

    workspace(repo).settle_owned(repo, ("docs/gen/a.md",), tip)

    assert unmerged(repo) == []
    assert (repo / "docs/gen/a.md").read_text() == "master\n"
    # The resolution is staged, as git add would leave it: the caller concludes the merge.
    assert git("diff", "--cached", "--name-only", cwd=repo) == "docs/gen/a.md"
    git("commit", "-q", "--no-edit", cwd=repo)
    assert git("status", "--porcelain", cwd=repo) == ""


def test_settle_leaves_a_path_outside_the_given_ones_conflicted(repo):
    _, tip = conflict_on(repo, "docs/gen/a.md", mixed=True)
    workspace(repo).merge_keeping_conflicts(repo, tip)

    workspace(repo).settle_owned(repo, ("docs/gen/a.md",), tip)

    assert unmerged(repo) == ["app/code.py"]
    assert "<<<<<<<" in (repo / "app/code.py").read_text()
    assert (repo / "docs/gen/a.md").read_text() == "master\n"


def test_settle_removes_a_file_the_source_does_not_have(repo):
    git("checkout", "-q", "-b", "feature", cwd=repo)
    write(repo, "docs/gen/a.md", "feature\n")
    commit(repo, "feature edits")
    git("checkout", "-q", "master", cwd=repo)
    git("rm", "-q", "docs/gen/a.md", cwd=repo)
    tip = commit(repo, "master deletes")
    git("checkout", "-q", "feature", cwd=repo)
    workspace(repo).merge_keeping_conflicts(repo, tip)
    assert unmerged(repo) == ["docs/gen/a.md"]

    workspace(repo).settle_owned(repo, ("docs/gen/a.md",), tip)

    assert unmerged(repo) == []
    assert not (repo / "docs/gen/a.md").exists()


def test_settle_takes_a_file_the_branch_deleted_from_the_source(repo):
    git("checkout", "-q", "-b", "feature", cwd=repo)
    git("rm", "-q", "docs/gen/a.md", cwd=repo)
    commit(repo, "feature deletes")
    git("checkout", "-q", "master", cwd=repo)
    write(repo, "docs/gen/a.md", "master\n")
    tip = commit(repo, "master edits")
    git("checkout", "-q", "feature", cwd=repo)
    workspace(repo).merge_keeping_conflicts(repo, tip)
    assert unmerged(repo) == ["docs/gen/a.md"]

    workspace(repo).settle_owned(repo, ("docs/gen/a.md",), tip)

    assert unmerged(repo) == []
    assert (repo / "docs/gen/a.md").read_text() == "master\n"


def test_settle_resolves_a_rename_on_both_sides_to_the_sources_name(repo):
    git("checkout", "-q", "-b", "feature", cwd=repo)
    git("mv", "docs/gen/a.md", "docs/gen/b.md", cwd=repo)
    commit(repo, "feature renames")
    git("checkout", "-q", "master", cwd=repo)
    git("mv", "docs/gen/a.md", "docs/gen/c.md", cwd=repo)
    tip = commit(repo, "master renames")
    git("checkout", "-q", "feature", cwd=repo)
    outcome = workspace(repo).merge_keeping_conflicts(repo, tip)
    conflicted = outcome.conflicted_paths
    assert conflicted

    workspace(repo).settle_owned(repo, conflicted, tip)

    assert unmerged(repo) == []
    assert (repo / "docs/gen/c.md").exists()
    assert not (repo / "docs/gen/a.md").exists() and not (repo / "docs/gen/b.md").exists()
    git("commit", "-q", "--no-edit", cwd=repo)
    assert git("status", "--porcelain", cwd=repo) == ""


def test_merge_settling_concludes_a_docs_only_conflict_with_a_merge_commit(repo):
    _, tip = conflict_on(repo, "docs/gen/a.md")
    own = git("rev-parse", "HEAD", cwd=repo)

    refusal = workspace(repo).merge_settling(repo, tip, ("docs/gen/a.md",), tip)

    assert refusal is None
    assert not merge_head(repo)
    assert git("rev-list", "--parents", "-n1", "HEAD", cwd=repo).split()[1:] == [own, tip]
    assert (repo / "docs/gen/a.md").read_text() == "master\n"
    assert git("status", "--porcelain", cwd=repo) == ""


def test_merge_settling_leaves_no_open_merge_when_something_else_conflicts(repo):
    _, tip = conflict_on(repo, "docs/gen/a.md", mixed=True)
    own = git("rev-parse", "HEAD", cwd=repo)

    refusal = workspace(repo).merge_settling(repo, tip, ("docs/gen/a.md",), tip)

    assert isinstance(refusal, MergeConflict)
    assert set(refusal.paths) == {"app/code.py", "docs/gen/a.md"}
    assert not merge_head(repo)
    assert git("rev-parse", "HEAD", cwd=repo) == own
    assert git("status", "--porcelain", cwd=repo) == ""


def test_merge_settling_leaves_no_open_merge_when_a_step_fails(repo):
    _, tip = conflict_on(repo, "docs/gen/a.md")
    own = git("rev-parse", "HEAD", cwd=repo)

    refusal = workspace(repo).merge_settling(repo, tip, ("docs/gen/a.md",), "0" * 40)

    assert isinstance(refusal, MergeFailed)
    assert not merge_head(repo)
    assert git("rev-parse", "HEAD", cwd=repo) == own
    assert git("status", "--porcelain", cwd=repo) == ""


def test_merge_settling_of_a_clean_merge_just_merges(repo):
    git("checkout", "-q", "-b", "feature", cwd=repo)
    write(repo, "app/code.py", "feature\n")
    commit(repo, "feature")
    git("checkout", "-q", "master", cwd=repo)
    write(repo, "docs/gen/a.md", "master\n")
    tip = commit(repo, "master")
    git("checkout", "-q", "feature", cwd=repo)

    assert workspace(repo).merge_settling(repo, tip, ("docs/gen/a.md",), tip) is None
    assert not merge_head(repo)


def test_ancestry_tells_which_of_two_commits_is_older(repo):
    older = git("rev-parse", "HEAD", cwd=repo)
    write(repo, "app/code.py", "next\n")
    newer = commit(repo, "next")
    git("checkout", "-q", "-b", "side", older, cwd=repo)
    write(repo, "app/side.py", "side\n")
    side = commit(repo, "side")
    ws = workspace(repo)

    assert ws.ancestry(repo, older, newer) == "ours-older"
    assert ws.ancestry(repo, newer, older) == "theirs-older"
    assert ws.ancestry(repo, newer, newer) == "same"
    assert ws.ancestry(repo, newer, side) == "diverged"
    with pytest.raises(GitError):
        ws.ancestry(repo, newer, "0" * 40)
