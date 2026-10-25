import os
import threading
import time
from pathlib import Path

import pytest

from agentic import composition
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.filesystem import documents as filesystem_documents
from agentic.core.process.adapters.filesystem.documents import system_commit
from agentic.core.process.domain import document


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


def test_record_closure_after_save_can_rewrite_the_landing_list(tmp_path):
    """GH-242, the literal `_reopened` crash sequence: `save` a state that carries a
    `landing` list, then `record_closure` a longer one."""
    documents = FilesystemTaskDocuments(tmp_path)
    entry = {"head": "a" * 40, "step": "re_review", "refusal": "head_not_reviewed", "at": "t"}
    state = {"id": "T-1", "title": "t", "description": "d", "landing": [entry]}
    documents.save(state)
    longer = [{**entry, "cost_base": 2.5}, {**entry, "step": "rerun"}]

    documents.record_closure("T-1", {"landing": longer})

    loaded = documents.load("T-1")
    assert loaded["landing"] == longer


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


# --- the parse cache (ADR-0063) ---------------------------------------------------------

HOUR_NS = 3_600 * 1_000_000_000


def _aged():
    """A clock an hour ahead of the filesystem's: every file written now is old enough to
    be cached, the way a closed document is for a poller that has run for a while."""
    return time.time_ns() + HOUR_NS


@pytest.fixture
def parses(monkeypatch):
    """Every `document.parse` call, in order, by the source path it was handed."""
    calls: list[object] = []
    real = document.parse

    def spy(raw, source="document"):
        calls.append(source)
        return real(raw, source)

    monkeypatch.setattr(document, "parse", spy)
    return calls


def _doc(task_id="T-1", description="d", **more):
    return {"id": task_id, "title": "t", "description": description, **more}


def _touch_later():
    time.sleep(0.02)  # past the filesystem's coarse timestamp tick


def test_an_unchanged_aged_document_is_parsed_once(tmp_path, parses):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    documents.save(_doc())

    first = documents.load("T-1")
    second = documents.load("T-1")

    assert first == second and len(parses) == 1


def test_a_rewrite_of_the_same_size_with_the_mtime_restored_is_seen(tmp_path, parses):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    path = documents.save(_doc(description="aaaa"))
    assert documents.load("T-1")["description"] == "aaaa"
    before = path.stat()
    _touch_later()

    path.write_text(path.read_text(encoding="utf-8").replace("aaaa", "bbbb"), encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    assert path.stat().st_size == before.st_size
    assert documents.load("T-1")["description"] == "bbbb"
    assert len(parses) == 2


def test_record_closure_after_a_cached_load_is_visible(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    documents.save(_doc())
    assert "closed_at" not in documents.load("T-1")
    _touch_later()

    documents.record_closure("T-1", {"closed_at": "2026-09-21T10:00:00Z"})

    assert documents.load("T-1")["closed_at"] == "2026-09-21T10:00:00Z"


def test_a_file_inside_the_racy_window_is_parsed_on_every_load(tmp_path, parses):
    path = FilesystemTaskDocuments(tmp_path).save(_doc())
    ctime = path.stat().st_ctime_ns
    documents = FilesystemTaskDocuments(tmp_path, clock=lambda: ctime + 1_000_000_000)

    documents.load("T-1")
    documents.load("T-1")

    assert len(parses) == 2


def test_a_file_exactly_at_the_window_is_cached(tmp_path, parses):
    path = FilesystemTaskDocuments(tmp_path).save(_doc())
    ctime = path.stat().st_ctime_ns
    documents = FilesystemTaskDocuments(tmp_path, clock=lambda: ctime + 2_000_000_000)

    documents.load("T-1")
    documents.load("T-1")

    assert len(parses) == 1


def test_a_ctime_in_the_future_is_never_cached(tmp_path, parses):
    path = FilesystemTaskDocuments(tmp_path).save(_doc())
    ctime = path.stat().st_ctime_ns
    documents = FilesystemTaskDocuments(tmp_path, clock=lambda: ctime - HOUR_NS)

    documents.load("T-1")
    documents.load("T-1")

    assert len(parses) == 2


def test_mutating_what_load_returned_changes_nothing_for_the_next_load(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    entry = {"head": "a" * 40, "step": "re_review", "refusal": "head_not_reviewed", "at": "t"}
    documents.save(_doc(sections={"PjM": "ok"}, landing=[entry]))

    for _ in range(2):  # the miss that stores, then the hit that copies
        loaded = documents.load("T-1")
        loaded["sections"]["PjM"] = "mutated"
        loaded["landing"][0]["step"] = "mutated"
        loaded["landing"].append({})
        loaded["extra"] = 1
        assert documents.load("T-1")["sections"] == {"PjM": "ok"}
        assert documents.load("T-1")["landing"] == [entry]
        assert "extra" not in documents.load("T-1")


def test_a_missing_file_is_none_until_it_is_written(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)

    assert documents.load("T-1") is None
    documents.save(_doc())

    assert documents.load("T-1")["id"] == "T-1"


def test_a_malformed_file_raises_on_every_load(tmp_path, parses):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    path = documents.path_for("T-1")
    path.parent.mkdir(parents=True)
    path.write_text("no frontmatter here", encoding="utf-8")

    for _ in range(2):
        with pytest.raises(ValueError, match="missing frontmatter"):
            documents.load("T-1")

    assert len(parses) == 2


def test_a_document_over_the_remaining_budget_is_returned_but_not_admitted(
    tmp_path, parses, monkeypatch
):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    big = documents.save(_doc("BIG", description="x" * 2000))
    small = documents.save(_doc("SMALL"))
    monkeypatch.setattr(filesystem_documents, "BUDGET", small.stat().st_size + 10)

    assert documents.load("SMALL")["id"] == "SMALL"
    assert documents.load("BIG")["description"] == "x" * 2000
    assert documents.load("BIG")["description"] == "x" * 2000
    assert documents.load("SMALL")["id"] == "SMALL"

    # BIG parsed on both loads (never admitted) and never evicted SMALL, parsed once.
    assert [Path(str(source)).stem for source in parses] == ["SMALL", "BIG", "BIG"]
    assert big.stat().st_size > small.stat().st_size + 10


def test_a_deleted_document_is_pruned_by_ids_and_gives_its_bytes_back(tmp_path, parses):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    gone = documents.save(_doc("GONE"))
    documents.save(_doc("KEPT"))
    documents.load("GONE")
    documents.load("KEPT")
    admitted = documents._admitted
    size = gone.stat().st_size

    gone.unlink()

    assert documents.ids() == ["KEPT"]
    assert documents._admitted == admitted - size
    assert set(documents._entries) == {"KEPT"}


def test_load_of_a_vanished_file_drops_its_entry(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    path = documents.save(_doc())
    documents.load("T-1")
    path.unlink()

    assert documents.load("T-1") is None
    assert documents._entries == {} and documents._admitted == 0


def test_eight_threads_saving_loading_and_listing_see_their_own_writes(tmp_path):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    errors: list[BaseException] = []

    def work(n: int) -> None:
        try:
            for i in range(25):
                # Another size each time: the aged clock bypasses the racy window, so
                # the key alone must tell the rewrites apart.
                text = "y" * (i + 1)
                documents.save(_doc(f"T-{n}", description=text))
                assert documents.load(f"T-{n}")["description"] == text
                assert f"T-{n}" in documents.ids()
                try:
                    documents.load(f"T-{(n + 1) % 8}")
                except ValueError:
                    pass  # `save` truncates in place: a neighbour caught mid-write, as before
        except BaseException as exc:  # noqa: BLE001 - reported on the main thread
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert documents._admitted == sum(entry.size for entry in documents._entries.values())


class _OsProxy:
    """The `os` the adapter sees, with `fstat` interposed for one descriptor: the second
    `fstat` of the file under test fires `between` first. The global `os.fstat` is never
    touched."""

    def __init__(self, target: Path, between):
        self._target = str(target)
        self._between = between
        self._fd: int | None = None
        self.calls = 0
        self.fired = False

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, *args, **kwargs):
        fd = os.open(path, *args, **kwargs)
        if str(path) == self._target and self._fd is None:
            self._fd = fd
        return fd

    def fstat(self, fd):
        if fd == self._fd:
            self.calls += 1
            if self.calls == 2 and not self.fired:
                self.fired = True
                self._between()
        return os.fstat(fd)


def test_a_write_between_the_two_fstats_is_not_cached(tmp_path, monkeypatch, parses):
    documents = FilesystemTaskDocuments(tmp_path, clock=_aged)
    path = documents.save(_doc(description="old"))

    def rewrite():
        path.write_text(path.read_text(encoding="utf-8").replace("old", "newer"), encoding="utf-8")

    proxy = _OsProxy(path, rewrite)
    monkeypatch.setattr(filesystem_documents, "os", proxy)

    documents.load("T-1")  # raced: must not be stored
    assert proxy.fired
    assert documents._entries == {}
    assert documents.load("T-1")["description"] == "newer"
    assert documents.load("T-1")["description"] == "newer"
    assert len(parses) == 2  # the raced read, then one that did store


def test_composition_builds_the_adapter_without_a_clock():
    source = Path(composition.__file__).read_text(encoding="utf-8")
    call = source[source.index("FilesystemTaskDocuments(") :]
    call = call[: call.index("),\n")]

    assert "clock" not in call
