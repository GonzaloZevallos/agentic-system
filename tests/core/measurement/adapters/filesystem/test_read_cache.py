import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.measurement.adapters.filesystem import read_cache
from agentic.core.measurement.adapters.filesystem.read_cache import FilesystemReadCache
from agentic.core.measurement.domain.issue_listing import ListedIssue, PageRecord

READ_AT = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
PAGES = {
    1: PageRecord(
        READ_AT,
        False,
        (
            ListedIssue(1, "2026-11-01T00:00:00Z", False, None, "closed"),
            ListedIssue(2, "2026-11-02T00:00:00Z", True, "2026-11-03T00:00:00Z", "closed"),
        ),
    ),
    2: PageRecord(READ_AT, True, (ListedIssue(3, "2026-11-04T00:00:00Z", True, None, "open"),)),
}


def cache(tmp_path: Path) -> FilesystemReadCache:
    return FilesystemReadCache(tmp_path / "root", "acme/widgets")


def test_the_pages_round_trip_through_the_file_named_by_the_repository(tmp_path):
    store = cache(tmp_path)
    store.save(PAGES)
    assert (tmp_path / "root" / "acme__widgets.json").is_file()
    assert store.load() == PAGES
    assert cache(tmp_path).load() == PAGES


def test_a_missing_file_is_a_miss(tmp_path):
    assert cache(tmp_path).load() == {}


def test_only_raw_items_page_times_and_open_are_stored(tmp_path):
    store = cache(tmp_path)
    store.save(PAGES)
    stored = json.loads((tmp_path / "root" / "acme__widgets.json").read_text())
    assert stored["v"] == 1
    assert set(stored) == {"v", "pages"}
    assert set(stored["pages"]["1"]) == {"read_at", "open", "items"}
    assert set(stored["pages"]["1"]["items"][0]) == {
        "number",
        "created_at",
        "is_pr",
        "pr_merged_at",
        "state",
    }


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "",
        "[]",
        '{"v": 2, "pages": {}}',
        '{"v": 1, "pages": []}',
        '{"v": 1, "pages": {"x": {}}}',
        '{"v": 1, "pages": {"1": {"read_at": "yesterday", "open": false, "items": []}}}',
        '{"v": 1, "pages": {"1": {"read_at": "2026-11-16T12:00:00+00:00", "open": 1,'
        ' "items": []}}}',
        '{"v": 1, "pages": {"1": {"read_at": "2026-11-16T12:00:00", "open": false, "items": []}}}',
        '{"v": 1, "pages": {"1": {"read_at": "2026-11-16T12:00:00+00:00", "open": false,'
        ' "items": [{"number": "1"}]}}}',
    ],
)
def test_a_corrupt_or_wrong_version_file_is_a_miss_and_is_overwritten(tmp_path, text):
    store = cache(tmp_path)
    path = tmp_path / "root" / "acme__widgets.json"
    path.parent.mkdir()
    path.write_text(text)
    assert store.load() == {}
    store.save(PAGES)
    assert store.load() == PAGES


def test_an_unreadable_file_is_a_miss_and_is_overwritten(tmp_path):
    path = tmp_path / "root" / "acme__widgets.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"v": 1, "pages": {}}))
    path.chmod(0o000)
    if os.access(path, os.R_OK):
        pytest.skip("this user reads any file, so none is unreadable")
    store = cache(tmp_path)
    assert store.load() == {}
    store.save(PAGES)
    assert store.load() == PAGES


def test_a_write_goes_through_a_pid_named_temp_file_and_leaves_none_behind(tmp_path, monkeypatch):
    renames = []
    real = os.replace
    monkeypatch.setattr(
        os, "replace", lambda a, b: (renames.append((Path(a), Path(b))), real(a, b))
    )
    cache(tmp_path).save(PAGES)
    target = tmp_path / "root" / "acme__widgets.json"
    assert renames == [(target.with_name(f"acme__widgets.json.{os.getpid()}.tmp"), target)]
    assert [p.name for p in (tmp_path / "root").iterdir()] == ["acme__widgets.json"]


def test_every_write_of_the_file_is_the_one_atomic_function(tmp_path, monkeypatch):
    written = []
    real = read_cache._write_atomic
    monkeypatch.setattr(read_cache, "_write_atomic", lambda p, t: (written.append(p), real(p, t)))
    store = cache(tmp_path)
    store.save(PAGES)
    store.save({})
    assert len(written) == 2


def test_a_directory_that_cannot_be_written_is_no_crash_and_the_next_load_is_a_miss(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    unwritable = FilesystemReadCache(blocker / "scores-reads", "o/r")
    unwritable.save({1: PageRecord(datetime(2026, 1, 1, tzinfo=UTC), False, ())})
    assert unwritable.load() == {}
