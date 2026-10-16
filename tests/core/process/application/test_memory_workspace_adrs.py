"""`MemoryWorkspace`'s double of the two-phase ADR reads (GH-229): a master tree a test
moves, the branch's own commits, and the pushes with their leases. The double answers
what the real adapter answers over the same history, and refuses what it refuses."""

import pytest

from agentic.core.process.application.ports import AddedAdr, AddedLine, GitError
from tests.fakes import MemoryWorkspace

MINE = "docs/adr/0049-mine.md"
THEIRS = "docs/adr/0049-theirs.md"


def test_added_adrs_are_the_branch_commits_adds_under_docs_adr_and_never_masters():
    workspace = MemoryWorkspace()
    workspace.advance_master(THEIRS)
    first = workspace.add_branch_commit(adds=(MINE, "docs/notes.md"))
    second = workspace.add_branch_commit(adds=("docs/adr/0050-other.md",))

    added = workspace.added_adrs(workspace.master_sha, "head")

    assert added == (AddedAdr(MINE, first), AddedAdr("docs/adr/0050-other.md", second))
    assert workspace.added_adrs_calls == [(workspace.master_sha, "head")]


def test_added_lines_hold_only_lines_with_a_number_once_and_record_the_ask():
    workspace = MemoryWorkspace()
    first = workspace.add_branch_commit(
        lines=(("docs/a.md", "ADR-0049 and ADR-0050"), ("docs/a.md", "nothing here"))
    )
    second = workspace.add_branch_commit(lines=(("docs/b.md", "0050"),))

    lines = workspace.added_lines("m", "h", ["0049", "0050"])

    assert lines == (
        AddedLine("docs/a.md", first, "ADR-0049 and ADR-0050"),
        AddedLine("docs/b.md", second, "0050"),
    )
    assert workspace.added_lines_calls == [("m", "h", ("0049", "0050"))]


@pytest.mark.parametrize("number", ["49", "00490", "004x", "", "０００４"])
def test_added_lines_rejects_what_the_real_one_rejects(number):
    with pytest.raises(ValueError, match="four digits"):
        MemoryWorkspace().added_lines("m", "h", [number])


def test_tree_names_are_masters_at_that_moment_and_the_branchs_up_to_that_commit():
    workspace = MemoryWorkspace()
    early = workspace.master_sha
    workspace.advance_master("docs/adr/0001-a.md")
    before_theirs = workspace.master_sha
    first = workspace.add_branch_commit(adds=(MINE, "docs/notes.md"))
    after_theirs = workspace.advance_master(THEIRS)
    second = workspace.add_branch_commit(adds=("docs/adr/0050-x.md",), master=after_theirs)

    assert workspace.master_sha == after_theirs != before_theirs != early
    assert workspace.tree_names(early, "docs/adr") == ()
    assert workspace.tree_names(before_theirs, "docs/adr") == ("0001-a.md",)
    assert workspace.tree_names(first, "docs/adr") == ("0001-a.md", "0049-mine.md")
    assert workspace.tree_names(second, "docs/adr") == (
        "0001-a.md",
        "0049-mine.md",
        "0049-theirs.md",
        "0050-x.md",
    )
    assert workspace.tree_names(second, "docs") == ("notes.md",)
    assert workspace.tree_names_calls[0] == (early, "docs/adr")


def test_tree_names_of_an_unknown_ref_raises_as_the_real_one_does():
    with pytest.raises(GitError):
        MemoryWorkspace().tree_names("nowhere", "docs/adr")


def test_pushes_are_kept_with_their_lease_and_a_lost_lease_records_none():
    workspace = MemoryWorkspace()
    workspace.remote_tips["agentic/GH-1"] = "old"

    workspace.push("agentic/GH-1", lease="old")
    workspace.push("agentic/GH-2")
    with pytest.raises(GitError):
        workspace.push("agentic/GH-1", lease="stale")

    assert workspace.pushes == [("agentic/GH-1", "old"), ("agentic/GH-2", None)]
    assert workspace.leases == ["old", None, "stale"]
