"""`archive_previous_run` (ADR-0011, sixth addendum), over the fakes: the three cases
`rules.next_archive_run` names, a document that cannot be read, the document committed on
the archived ref (GH-420: never on a live branch, never through a checkout), and the damage
order (nothing removed before it is committed)."""

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
    assert ports.workspace.archived_documents == {}
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
    # The document goes onto the archived task ref, never into a checkout.
    assert ports.documents.written_into == []
    assert ports.workspace.ignored_checks == []
    assert ports.workspace.archived_documents == {
        ("archive/run1-75de807/agentic/GH-1", ".agentic/tasks/GH-1.md"): "---\nid: GH-1\n---\n"
    }
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
    # No document to commit and no worktree step: both already happened in the cut-short attempt.
    assert ports.workspace.archived_documents == {}
    assert ports.workspace.commits == []
    assert ports.workspace.cleaned == []
    assert ports.workspace.renamed == [
        ("agentic/GH-12.api", "archive/run4-unknown/agentic/GH-12.api")
    ]


def test_an_unreadable_document_falls_back_to_unknown_and_archiving_continues():
    class BrokenDocuments(MemoryDocuments):
        def load(self, task_id: str) -> dict | None:
            raise ValueError("missing frontmatter")

        def text(self, task_id: str) -> str | None:
            # A real adapter's `text` reads the file; only `load` parses the frontmatter,
            # and this fake's own `text` calls it, which a broken `load` would make fail for
            # a reason this use case never asks it to guard.
            return "raw\n"

    ports = memory_ports(documents=BrokenDocuments())
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")

    result = archive.archive_previous_run("GH-1", ports)

    assert result.prefix == "archive/run1-unknown"
    assert result.written is True
    assert ports.workspace.renamed == [("agentic/GH-1", "archive/run1-unknown/agentic/GH-1")]


def test_a_product_that_ignores_the_document_does_not_matter_it_is_committed_all_the_same():
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")
    ports.workspace.ignored.add(".agentic/tasks/GH-1.md")

    result = archive.archive_previous_run("GH-1", ports)

    assert result.document_kept is True
    assert ports.workspace.ignored_checks == []
    assert ("archive/run1-abc1234/agentic/GH-1", ".agentic/tasks/GH-1.md") in (
        ports.workspace.archived_documents
    )


def test_a_task_with_no_document_is_archived_and_said_to_be_without_it():
    ports = memory_ports()
    ports.workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")

    result = archive.archive_previous_run("GH-1", ports)

    assert result.written is True
    assert result.document_kept is False
    assert ports.workspace.archived_documents == {}
    assert ports.workspace.renamed == [("agentic/GH-1", "archive/run1-unknown/agentic/GH-1")]


def test_a_document_commit_that_fails_is_said_and_the_archive_goes_on_to_the_subtasks():
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})
    ports.workspace.existing_branches.update(
        {"agentic/GH-1": ("h1", "b0"), "agentic/GH-1.api": ("h2", "b1")}
    )
    ports.workspace.archived_document_error = "update-ref failed: moved"

    result = archive.archive_previous_run("GH-1", ports)

    assert result.document_kept is False
    assert result.written is True
    assert ("agentic/GH-1.api", "archive/run1-abc1234/agentic/GH-1.api") in ports.workspace.renamed


def test_the_document_is_committed_after_the_task_rename_and_before_the_subtask_renames():
    order: list[str] = []

    class Ordered(MemoryWorkspace):
        def rename_branch(self, old: str, new: str) -> None:
            order.append(f"rename:{old}")
            super().rename_branch(old, new)

        def commit_archived_document(self, ref, relative, text, message):
            order.append("document")
            return super().commit_archived_document(ref, relative, text, message)

    workspace = Ordered()
    workspace.existing_branches.update(
        {"agentic/GH-1": ("h1", "b0"), "agentic/GH-1.api": ("h2", "b1")}
    )
    ports = memory_ports(workspace=workspace)
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})

    archive.archive_previous_run("GH-1", ports)

    assert order == ["rename:agentic/GH-1", "document", "rename:agentic/GH-1.api"]


def test_an_archive_cut_after_the_task_rename_is_finished_by_its_retry_with_one_document_commit():
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})
    ports.workspace.existing_branches.update(
        {"agentic/GH-1": ("h1", "b0"), "agentic/GH-1.api": ("h2", "b1")}
    )
    # Cut after the task rename, before the document: the first attempt dies in the commit.
    ports.workspace.archived_document_error = "killed"
    first = archive.archive_previous_run("GH-1", ports)
    assert first.document_kept is False
    ports.workspace.archived_document_error = None
    ports.workspace.existing_branches["agentic/GH-1.api"] = ("h2", "b1")  # still live

    retried = archive.archive_previous_run("GH-1", ports)
    again = archive.archive_previous_run("GH-1", ports)

    ref = "archive/run1-abc1234/agentic/GH-1"
    assert retried.prefix == "archive/run1-abc1234"
    assert retried.written is False
    assert retried.document_kept is True
    assert ports.workspace.archived_document_commits == [
        (ref, ".agentic/tasks/GH-1.md", "Archive run 1 of GH-1: the task document")
    ]
    # Nothing live is left, so the third call has nothing to do.
    assert again == archive.ArchiveResult(prefix=None, written=False)


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


def test_a_slice_branch_is_archived_with_the_subtask_branches_and_gh_1_slices_are_not_gh_12s():
    ports = memory_ports()
    ports.documents.save({"id": "GH-1", "system_commit": "75de807"})
    ports.workspace.existing_branches.update(
        {
            "agentic/GH-1": ("h1", "b0"),
            "agentic/GH-1.slice-1": ("h2", "b1"),
            "agentic/GH-12.slice-1": ("h3", "b2"),
        }
    )

    archive.archive_previous_run("GH-1", ports)

    assert set(ports.workspace.renamed) == {
        ("agentic/GH-1", "archive/run1-75de807/agentic/GH-1"),
        ("agentic/GH-1.slice-1", "archive/run1-75de807/agentic/GH-1.slice-1"),
    }


def _real_product(tmp_path: Path):
    import subprocess

    from agentic.core.process.adapters.git import GitWorkspace
    from agentic.shared.configuration import Configuration

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    git("init", "-q", "-b", "master")
    (tmp_path / "README.md").write_text("v0\n")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "v0")
    workspace = GitWorkspace(tmp_path, Configuration(product_dir=tmp_path).git_identity)
    return workspace, git


def test_over_a_real_repo_the_document_lands_on_the_archived_ref_and_on_no_live_branch(tmp_path):
    workspace, git = _real_product(tmp_path)
    task = workspace.task_checkout("GH-1")
    (task.path / "code.py").write_text("x\n")

    class OnDisk(MemoryDocuments):
        def text(self, task_id: str) -> str | None:
            return "the record\n"

    ports = memory_ports(workspace=workspace, documents=OnDisk())
    ports.documents.save({"id": "GH-1", "system_commit": "abc1234"})
    # The document is where the system keeps it: on disk in the product, untracked.
    (tmp_path / ".agentic/tasks").mkdir(parents=True)
    (tmp_path / ".agentic/tasks/GH-1.md").write_text("the record\n")

    result = archive.archive_previous_run("GH-1", ports)

    ref = "archive/run1-abc1234/agentic/GH-1"
    assert result.document_kept is True
    assert git("show", f"{ref}:.agentic/tasks/GH-1.md") == "the record"
    assert git("show", "--name-only", "--format=", f"{ref}~1") == "code.py"
    assert git("branch", "--list", "agentic/*") == ""
    # No live branch carries the document, nor does the product's own index.
    assert git("log", "--all", "--not", ref, "--format=%s") == ""
    assert git("ls-files") == "README.md"
    # Retried with the task already archived: still exactly one document commit.
    assert git("rev-list", "--count", f"{ref}~1..{ref}") == "1"
    archive.archive_previous_run("GH-1", ports)
    assert git("rev-list", "--count", f"{ref}~1..{ref}") == "1"
