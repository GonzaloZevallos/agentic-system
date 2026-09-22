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


def test_record_closure_writes_the_values_and_leaves_the_rest_of_the_document_untouched(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path)
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {"Developer": "Built."}}
    path = documents.save(state)
    before = path.read_text(encoding="utf-8")

    written = documents.record_closure("T-1", {"closed_at": "2026-09-21T10:00:00Z"})

    assert written == path
    after = path.read_text(encoding="utf-8")
    assert after != before
    loaded = documents.load("T-1")
    assert loaded["closed_at"] == "2026-09-21T10:00:00Z"
    # Nothing `save` itself would have rewritten changed: not a `save` round-trip.
    assert loaded["id"] == "T-1" and loaded["sections"] == {"Developer": "Built."}


def test_save_writes_system_version_when_a_producer_is_given(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path, system_version=lambda: "abc123+dirty")
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    documents.save(state)
    assert documents.load("T-1")["system_version"] == "abc123+dirty"


def test_save_without_a_producer_writes_no_system_version_key(tmp_path):
    """A bare-constructed adapter -- the graph regression's own -- produces today's
    document plus nothing."""
    documents = FilesystemTaskDocuments(tmp_path)
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    documents.save(state)
    assert "system_version" not in documents.load("T-1")


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
