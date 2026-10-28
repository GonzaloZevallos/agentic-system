"""The archiving verbs added to `Workspace` and `TaskDocuments` (ADR-0011, sixth
addendum), exercised through the in-memory fakes: `Ports(workspace=MemoryWorkspace(),
documents=MemoryDocuments())` is the shape `application/archive.py` (a later subtask)
builds on, so this is where a wrong signature would first show."""

from pathlib import Path

import pytest

from agentic.core.process.application.ports import GitError
from tests.fakes import MemoryDocuments, MemoryTracker, MemoryWorkspace, memory_ports


def test_ports_accepts_the_fakes_with_the_new_verbs():
    ports = memory_ports(workspace=MemoryWorkspace(), documents=MemoryDocuments())
    assert isinstance(ports.workspace, MemoryWorkspace)
    assert isinstance(ports.documents, MemoryDocuments)


def test_branches_answers_only_names_starting_with_the_given_prefix():
    workspace = MemoryWorkspace()
    workspace.existing_branches.update(
        {"agentic/GH-1": ("h", "b"), "agentic/GH-12": ("h", "b"), "main": ("h", "b")}
    )
    assert workspace.branches("agentic/GH-1") == ["agentic/GH-1", "agentic/GH-12"]
    assert workspace.branches("agentic/GH-12") == ["agentic/GH-12"]


def test_rename_branch_moves_the_recorded_head_and_base_and_is_recorded():
    workspace = MemoryWorkspace()
    workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")
    workspace.rename_branch("agentic/GH-1", "archive/run1-abc/agentic/GH-1")
    assert "agentic/GH-1" not in workspace.existing_branches
    assert workspace.existing_branches["archive/run1-abc/agentic/GH-1"] == ("h1", "b0")
    assert workspace.renamed == [("agentic/GH-1", "archive/run1-abc/agentic/GH-1")]


def test_create_branch_copies_a_head_in_and_is_idempotent_at_the_same_sha():
    workspace = MemoryWorkspace()
    workspace.existing_branches["archive/run1-abc/agentic/GH-1"] = ("h1", "b0")
    workspace.create_branch("agentic/GH-1", "h1")
    workspace.create_branch("agentic/GH-1", "h1")
    assert workspace.branch_head("agentic/GH-1") == "h1"
    assert "archive/run1-abc/agentic/GH-1" in workspace.existing_branches  # not renamed
    assert workspace.created == [("agentic/GH-1", "h1"), ("agentic/GH-1", "h1")]


def test_create_branch_raises_when_the_ref_points_elsewhere_and_leaves_it():
    workspace = MemoryWorkspace()
    workspace.existing_branches["agentic/GH-1"] = ("h1", "b0")
    with pytest.raises(GitError):
        workspace.create_branch("agentic/GH-1", "h2")
    assert workspace.branch_head("agentic/GH-1") == "h1"


def test_reopen_pull_request_records_a_number_once_and_raises_when_scripted_to_refuse():
    tracker = MemoryTracker()
    tracker.reopen_pull_request(7)
    tracker.reopen_pull_request(7)
    assert tracker.reopened_pull_requests == [7]
    tracker.reopen_error = "gh pr reopen failed: merged"
    with pytest.raises(ValueError, match="merged"):
        tracker.reopen_pull_request(8)
    assert tracker.reopened_pull_requests == [7]


def test_is_ignored_answers_false_by_default_and_records_the_call():
    workspace = MemoryWorkspace()
    root = Path("/memory/product")
    assert workspace.is_ignored(root, ".agentic/tasks/GH-1.md") is False
    assert workspace.ignored_checks == [(root, ".agentic/tasks/GH-1.md")]


def test_is_ignored_answers_what_the_test_declared_ignored():
    workspace = MemoryWorkspace()
    workspace.ignored.add(".agentic/tasks/GH-1.md")
    assert workspace.is_ignored(Path("/memory/product"), ".agentic/tasks/GH-1.md") is True


def test_write_into_answers_none_when_the_task_has_no_document():
    documents = MemoryDocuments()
    assert documents.write_into("GH-1", Path("/memory/worktrees/GH-1")) is None
    assert documents.written_into == [("GH-1", Path("/memory/worktrees/GH-1"))]


def test_write_into_answers_the_path_once_the_task_has_a_document():
    documents = MemoryDocuments()
    documents.save({"id": "GH-1"})
    root = Path("/memory/worktrees/GH-1")
    assert documents.write_into("GH-1", root) == root / ".agentic" / "tasks" / "GH-1.md"


def test_full_head_is_a_look_like_head_that_is_stable_on_an_unmoved_path():
    workspace = MemoryWorkspace()
    path = Path("/memory/worktrees/GH-1")
    assert workspace.full_head(path) == workspace.head(path) == "base"
    assert workspace.full_head(path) == "base"


def test_full_head_on_a_moved_path_advances_the_counter_head_shares():
    workspace = MemoryWorkspace()
    path = Path("/memory/worktrees/GH-1")
    workspace.moved.add(path)
    assert workspace.full_head(path) == "head1"
    assert workspace.full_head(path) == "head2"
    assert workspace.head(path) == "head3"


def test_the_double_reads_an_unscripted_conflict_probe_as_unread_and_records_every_ask():
    workspace = MemoryWorkspace()
    workspace.conflict_answers[("h1", "m")] = True
    assert workspace.merge_conflicts("h1", "m") is True
    assert workspace.merge_conflicts("h2", "m") is None
    assert workspace.conflict_probes == [("h1", "m"), ("h2", "m")]
