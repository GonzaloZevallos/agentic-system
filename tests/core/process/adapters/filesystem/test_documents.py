from pathlib import Path

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


def test_a_custom_subdir_is_isolated_from_the_default_tasks_corpus(tmp_path):
    """The stack review's own corpus (`.agentic/stack-reviews/`, ADR-0034) lives beside
    `.agentic/tasks/`, never inside it: a document saved under one `subdir` must not
    show up in `ids()` of a documents port scoped to the other, which is what keeps
    `close --all`, `eval score` and `_bucket_documents` blind to it."""
    tasks = FilesystemTaskDocuments(tmp_path)
    reviews = FilesystemTaskDocuments(tmp_path, subdir=Path(".agentic/stack-reviews"))
    tasks.save({"id": "T-1", "title": "t", "description": "d"})
    reviews.save({"id": "stack-2026-09-24", "title": "s", "description": "d"})

    assert tasks.ids() == ["T-1"]
    assert reviews.ids() == ["stack-2026-09-24"]
    expected = tmp_path / ".agentic/stack-reviews/stack-2026-09-24.md"
    assert reviews.path_for("stack-2026-09-24") == expected
    assert tasks.load("stack-2026-09-24") is None
    assert reviews.load("T-1") is None


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


def test_ids_touched_since_returns_only_documents_whose_mtime_is_in_range(tmp_path):
    import os
    from datetime import UTC, datetime

    documents = FilesystemTaskDocuments(tmp_path)
    for task_id in ("T-old", "T-new", "T-edge"):
        documents.save({"id": task_id, "title": "t", "description": "d"})
    since = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    stamp = since.timestamp()
    os.utime(documents.path_for("T-old"), (stamp - 3600, stamp - 3600))
    os.utime(documents.path_for("T-new"), (stamp + 60, stamp + 60))
    os.utime(documents.path_for("T-edge"), (stamp, stamp))
    assert documents.ids_touched_since(since) == ["T-edge", "T-new"]


def test_ids_touched_since_never_parses_a_document_outside_the_range(tmp_path):
    import os
    from datetime import UTC, datetime

    documents = FilesystemTaskDocuments(tmp_path)
    garbage = documents.path_for("T-garbage")
    garbage.parent.mkdir(parents=True)
    garbage.write_text("\x00 not a document", encoding="utf-8")
    since = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    os.utime(garbage, (since.timestamp() - 10, since.timestamp() - 10))
    assert documents.ids_touched_since(since) == []


def test_ids_touched_since_stays_inside_the_documents_subdir_and_with_no_dir(tmp_path):
    from datetime import UTC, datetime

    tasks = FilesystemTaskDocuments(tmp_path)
    reviews = FilesystemTaskDocuments(tmp_path, subdir=Path(".agentic/stack-reviews"))
    long_ago = datetime(2000, 1, 1, tzinfo=UTC)
    assert tasks.ids_touched_since(long_ago) == []
    reviews.save({"id": "stack-1", "title": "s", "description": "d"})
    assert tasks.ids_touched_since(long_ago) == []
    assert reviews.ids_touched_since(long_ago) == ["stack-1"]


def test_load_reads_the_product_copy_not_the_one_a_delivery_committed_on_the_branch(tmp_path):
    """GH-203: a bounded delivery commits a snapshot of the document on the task branch.
    The resume reads the product checkout's own copy, which has the status and the later
    fields, never the branch's."""
    product = tmp_path / "product"
    branch = tmp_path / "product/.agentic/worktrees/GH-1"
    state = {"id": "GH-1", "title": "t", "description": "d", "status": "integrating"}
    FilesystemTaskDocuments(product).save(state)
    FilesystemTaskDocuments(branch).save({**state, "status": "branch snapshot"})

    loaded = FilesystemTaskDocuments(product).load("GH-1")
    assert loaded is not None and loaded["status"] == "integrating"
