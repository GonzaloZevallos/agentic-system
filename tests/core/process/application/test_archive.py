"""`archive_previous_run` (ADR-0011, sixth addendum), over the fakes: the three cases
`rules.next_archive_run` names, a document that cannot be read, a product that ignores
the document, and the damage order (nothing removed before it is committed)."""

from pathlib import Path

from agentic.core.process.application import archive
from tests.fakes import FakeReaper, MemoryDocuments, MemoryWorkspace, memory_ports


def test_nothing_to_archive_calls_no_mutating_verb():
    ports = memory_ports()
    result = archive.archive_previous_run("GH-1", ports)
    assert result == archive.ArchiveResult(prefix=None, written=False)
    assert ports.workspace.renamed == []
    assert ports.workspace.commits == []
    assert ports.workspace.cleaned == []
    assert ports.workspace.pruned == 0
    assert ports.workspace.ignored_checks == []
    assert ports.documents.written_into == []


def test_a_fresh_archive_commits_every_worktree_cleans_up_and_renames_both_branches():
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "system_commit": "75de807"})
    ports.workspace.existing_branches.update(
        {"agentic/GH-1": ("h1", "b0"), "agentic/GH-1.api": ("h2", "b1")}
    )
    ports.workspace.checkouts = [
        MemoryWorkspace.ROOT / "GH-1",
        MemoryWorkspace.ROOT / "GH-1.api",
    ]

    result = archive.archive_previous_run("GH-1", ports)

    assert result == archive.ArchiveResult(prefix="archive/run1-75de807", written=True)
    assert ports.documents.written_into == [("GH-1", MemoryWorkspace.ROOT / "GH-1")]
    assert ports.workspace.commits == [
        (MemoryWorkspace.ROOT / "GH-1", "Archive run 1 of GH-1: GH-1"),
        (MemoryWorkspace.ROOT / "GH-1.api", "Archive run 1 of GH-1: GH-1.api"),
    ]
    assert ports.workspace.cleaned == ["GH-1"]
    assert ports.workspace.pruned == 1
    assert set(ports.workspace.renamed) == {
        ("agentic/GH-1", "archive/run1-75de807/agentic/GH-1"),
        ("agentic/GH-1.api", "archive/run1-75de807/agentic/GH-1.api"),
    }
    assert ports.workspace.existing_branches["archive/run1-75de807/agentic/GH-1"] == ("h1", "b0")


def test_the_task_branch_is_renamed_before_the_subtask_branch():
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})
    ports.workspace.existing_branches.update(
        {"agentic/GH-1": ("h1", "b0"), "agentic/GH-1.api": ("h2", "b1")}
    )
    result = archive.archive_previous_run("GH-1", ports)
    assert result.written is True
    assert ports.workspace.renamed[0] == ("agentic/GH-1", "archive/run1-abc1234/agentic/GH-1")
    assert ports.workspace.renamed[1] == (
        "agentic/GH-1.api",
        "archive/run1-abc1234/agentic/GH-1.api",
    )


def test_a_cut_short_archive_reuses_its_n_and_only_renames_the_live_subtask_branch():
    # The task branch already renamed away, a subtask branch still live: what a
    # `GitError` after the task-branch rename but before the subtask one leaves behind.
    ports = memory_ports()
    ports.workspace.existing_branches.update(
        {
            "archive/run4-abc1234/agentic/GH-12": ("h0", "b0"),
            "agentic/GH-12.api": ("h2", "b1"),
        }
    )
    result = archive.archive_previous_run("GH-12", ports)
    # `N` is reused from the ref already there; the system commit comes fresh from
    # this call's own document read (there is none here, so `unknown`) -- the ADR
    # discards using the commit as a retry discriminator, so the two need not match.
    assert result == archive.ArchiveResult(prefix="archive/run4-unknown", written=False)
    # No document and no worktree step: both already happened in the cut-short attempt.
    assert ports.documents.written_into == []
    assert ports.workspace.commits == []
    assert ports.workspace.cleaned == []
    assert ports.workspace.renamed == [
        ("agentic/GH-12.api", "archive/run4-unknown/agentic/GH-12.api")
    ]


def test_an_unreadable_document_falls_back_to_unknown_and_archiving_continues():
    class BrokenDocuments(MemoryDocuments):
        def load(self, task_id: str) -> dict | None:
            raise ValueError("missing frontmatter")

        def write_into(self, task_id: str, root: Path) -> Path | None:
            # A real adapter's `write_into` does not re-parse the frontmatter to see
            # whether there is a document; only `load` does, and this fake's own
            # `write_into` otherwise calls it (`tests/fakes.py`), which a broken `load`
            # would make fail for a reason this use case never asks it to guard.
            self.written_into.append((task_id, root))
            return root / ".agentic" / "tasks" / f"{task_id}.md"

    ports = memory_ports(documents=BrokenDocuments())
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")

    result = archive.archive_previous_run("GH-1", ports)

    assert result.prefix == "archive/run1-unknown"
    assert result.written is True
    assert ports.workspace.renamed == [("agentic/GH-1", "archive/run1-unknown/agentic/GH-1")]


def test_a_document_the_product_ignores_is_still_archived_and_said_so():
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")
    ports.workspace.ignored.add(".agentic/tasks/GH-1.md")

    result = archive.archive_previous_run("GH-1", ports)

    assert result.prefix == "archive/run1-abc1234"
    assert result.written is True
    assert result.document_kept is False
    assert ports.workspace.renamed == [("agentic/GH-1", "archive/run1-abc1234/agentic/GH-1")]


def test_no_worktree_is_removed_before_its_tree_is_committed():
    order: list[str] = []

    class OrderedWorkspace(MemoryWorkspace):
        def commit_all(self, path: Path, message: str) -> str | None:
            order.append(f"commit:{path.name}")
            return super().commit_all(path, message)

        def cleanup(self, task_id: str) -> None:
            order.append("cleanup")
            super().cleanup(task_id)

    workspace = OrderedWorkspace()
    workspace.existing_branches.update(
        {"agentic/GH-1": ("h1", "b0"), "agentic/GH-1.api": ("h2", "b1")}
    )
    workspace.checkouts = [MemoryWorkspace.ROOT / "GH-1", MemoryWorkspace.ROOT / "GH-1.api"]
    ports = memory_ports(workspace=workspace)
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})

    archive.archive_previous_run("GH-1", ports)

    assert order == ["commit:GH-1", "commit:GH-1.api", "cleanup"]


def test_does_not_confuse_gh_1_with_gh_12():
    ports = memory_ports()
    ports.workspace.existing_branches["agentic/GH-12"] = ("h", "b")
    result = archive.archive_previous_run("GH-1", ports)
    assert result == archive.ArchiveResult(prefix=None, written=False)
    assert ports.workspace.renamed == []


def test_what_the_release_reaped_comes_back_in_the_result_and_nothing_otherwise():
    reaped = ({"pid": 7, "command": "uv run sample_app.py", "outcome": "kill"},)
    ports = memory_ports(reaper=FakeReaper(reaped))
    ports.documents.save({"id": "GH-1", "system_commit": "75de807"})
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")

    result = archive.archive_previous_run("GH-1", ports)

    assert result.reaped == reaped
    assert ports.workspace.cleaned == ["GH-1"]

    quiet = memory_ports()
    quiet.documents.save({"id": "GH-1", "system_commit": "75de807"})
    quiet.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")
    assert archive.archive_previous_run("GH-1", quiet).reaped == ()
