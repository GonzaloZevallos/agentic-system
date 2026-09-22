from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.filesystem.documents import system_commit


def test_save_then_load_in_the_product_repo(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path)
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {"PjM": "ok"}}
    path = documents.save(state)
    assert path == tmp_path / ".agentic/tasks/T-1.md" == documents.path_for("T-1")
    loaded = documents.load("T-1")
    assert loaded["id"] == "T-1" and loaded["sections"] == {"PjM": "ok"}
    # The system's own commit goes into the frontmatter (ADR-0005).
    assert loaded["system_commit"] == system_commit()
    assert documents.load("T-2") is None  # no document yet: nothing to resume from


def test_system_commit_is_a_short_hash_or_none():
    commit = system_commit()
    assert commit is None or (len(commit) >= 7 and commit.isalnum())


def test_write_into_copies_the_document_without_touching_where_save_keeps_it(tmp_path):
    """ADR-0011, sixth addendum: archiving commits the document on the archived task
    branch's worktree, never wherever `save` itself writes it."""
    documents = FilesystemTaskDocuments(tmp_path)
    other_root = tmp_path / "worktree"
    other_root.mkdir()
    assert documents.write_into("T-1", other_root) is None  # nothing to write yet

    state = {"id": "T-1", "title": "t", "description": "d", "sections": {"PjM": "ok"}}
    documents.save(state)
    written = documents.write_into("T-1", other_root)
    assert written == other_root / ".agentic/tasks/T-1.md"
    assert written.read_text() == documents.path_for("T-1").read_text()
    # `save`'s own file is untouched: still where it always lived.
    assert documents.path_for("T-1") == tmp_path / ".agentic/tasks/T-1.md"
