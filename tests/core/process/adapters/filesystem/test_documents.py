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
