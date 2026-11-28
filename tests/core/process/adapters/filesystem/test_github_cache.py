"""`FilesystemGithubCache`: the per-product disk store of GitHub REST responses."""

from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.github_cache import FilesystemGithubCache
from agentic.core.process.domain import etag_cache
from agentic.core.process.domain.etag_cache import CacheEntry

REPO = "owner/name"
PRODUCT = "prod"
DAY = 86400.0
NOW = 10_000_000.0


def _key(n: int = 0) -> str:
    return etag_cache.entry_key(REPO, f"/repos/owner/name/issues/{n}", (), 1)


def _entry(n: int = 0, **over: object) -> CacheEntry:
    base = CacheEntry(
        v=etag_cache.VERSION,
        repo=REPO,
        key=_key(n),
        etag=f'W/"{n}"',
        last_modified=None,
        body=f'{{"n": {n}}}',
        fetched_at=NOW,
    )
    return dataclasses.replace(base, **over)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "github"


@pytest.fixture
def cache(root: Path) -> FilesystemGithubCache:
    return FilesystemGithubCache(root, PRODUCT, REPO)


def _path(root: Path, key: str) -> Path:
    return root / PRODUCT / f"{key}.json"


def _age(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _files(root: Path) -> list[str]:
    return sorted(p.name for p in (root / PRODUCT).iterdir())


def test_put_then_get_round_trips(cache):
    cache.put(_entry(1), NOW)
    assert cache.get(_key(1), NOW) == _entry(1)


def test_get_of_a_missing_key_is_a_miss(cache):
    cache.put(_entry(1), NOW)
    assert cache.get(_key(2), NOW) is None


def test_get_on_a_missing_directory_is_a_miss_and_creates_nothing(cache, root):
    assert cache.get(_key(1), NOW) is None
    assert not root.exists()


def test_a_key_that_is_not_64_hex_is_a_miss_on_get_and_misuse_on_put(cache, root):
    assert cache.get("../../etc/passwd", NOW) is None
    assert cache.get(_key(1).upper(), NOW) is None
    with pytest.raises(ValueError):
        cache.put(_entry(1, key="../x"), NOW)
    assert not root.exists()


def test_put_of_another_repo_is_misuse(cache, root):
    with pytest.raises(ValueError):
        cache.put(_entry(1, repo="other/name"), NOW)
    assert not root.exists()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b'{"v": 1, "repo": "owner/na',
        b"\xff\xfe\x00 not utf-8",
        b"[]",
    ],
)
def test_a_corrupt_file_is_a_miss(cache, root, content):
    cache.put(_entry(1), NOW)
    _path(root, _key(1)).write_bytes(content)
    assert cache.get(_key(1), NOW) is None


def test_an_unreadable_entry_is_a_miss(cache, root):
    cache.put(_entry(1), NOW)
    path = _path(root, _key(1))
    path.unlink()
    path.mkdir()  # a directory where the file should be: reading it is an OSError
    assert cache.get(_key(1), NOW) is None


def test_an_entry_of_another_repo_is_a_miss(cache, root):
    FilesystemGithubCache(root, PRODUCT, "other/name").put(_entry(1, repo="other/name"), NOW)
    assert cache.get(_key(1), NOW) is None


def test_an_entry_of_another_version_is_a_miss(cache, root):
    cache.put(_entry(1), NOW)
    path = _path(root, _key(1))
    path.write_text(path.read_text().replace('"v": 1', '"v": 2'))
    assert cache.get(_key(1), NOW) is None


def test_a_file_under_another_key_is_a_miss(cache, root):
    cache.put(_entry(1), NOW)
    _path(root, _key(1)).rename(_path(root, _key(2)))
    assert cache.get(_key(2), NOW) is None


def test_get_renews_the_mtime_so_a_read_entry_outlives_the_ttl(cache, root):
    cache.put(_entry(1), NOW)
    assert cache.get(_key(1), NOW + 6 * DAY) is not None
    assert cache.get(_key(1), NOW + 12 * DAY) is not None
    assert cache.get(_key(1), NOW + 14 * DAY) is not None
    assert _path(root, _key(1)).stat().st_mtime == NOW + 14 * DAY


def test_an_entry_unread_for_the_ttl_is_a_miss_and_is_left_untouched(cache, root):
    cache.put(_entry(1), NOW)
    assert cache.get(_key(1), NOW + etag_cache.READ_TTL_S) is None
    assert _path(root, _key(1)).stat().st_mtime == NOW


def test_get_answers_even_when_renewing_the_mtime_fails(cache, monkeypatch):
    cache.put(_entry(1), NOW)

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(os, "utime", refuse)
    assert cache.get(_key(1), NOW + DAY) == _entry(1)


def test_a_future_mtime_is_a_miss_and_is_pruned_on_the_next_put(cache, root):
    cache.put(_entry(1), NOW)
    _age(_path(root, _key(1)), NOW + DAY)
    assert cache.get(_key(1), NOW) is None
    cache.put(_entry(2), NOW)
    assert _files(root) == [f"{_key(2)}.json"]


def test_an_expired_entry_is_pruned_on_the_next_put(cache, root):
    cache.put(_entry(1), NOW)
    cache.put(_entry(2), NOW + etag_cache.READ_TTL_S - 1)
    assert sorted(_files(root)) == sorted([f"{_key(1)}.json", f"{_key(2)}.json"])
    cache.put(_entry(3), NOW + etag_cache.READ_TTL_S)
    assert sorted(_files(root)) == sorted([f"{_key(2)}.json", f"{_key(3)}.json"])


def test_pruning_by_count_removes_the_oldest_by_mtime(cache, root, monkeypatch):
    monkeypatch.setattr(etag_cache, "MAX_ENTRIES", 3)
    for n in range(4):
        cache.put(_entry(n), NOW + n)
    assert _files(root) == sorted(f"{_key(n)}.json" for n in (1, 2, 3))
    cache.put(_entry(9), NOW + 10)
    assert _files(root) == sorted(f"{_key(n)}.json" for n in (2, 3, 9))


def test_pruning_by_total_bytes_removes_the_oldest_by_mtime(cache, root, monkeypatch):
    size = len(etag_cache.to_json(_entry(1, body="x" * 1000)))
    monkeypatch.setattr(etag_cache, "TOTAL_MAX_BYTES", size * 2 + 10)
    for n in range(3):
        cache.put(_entry(n, body="x" * 1000), NOW + n)
    assert _files(root) == sorted(f"{_key(n)}.json" for n in (1, 2))


def test_a_stale_tmp_is_removed_and_a_fresh_one_kept(cache, root):
    cache.put(_entry(1), NOW)
    stale, fresh = root / PRODUCT / ".entry-stale.tmp", root / PRODUCT / ".entry-fresh.tmp"
    for path in (stale, fresh):
        path.write_text("partial")
    _age(stale, NOW - etag_cache.TMP_STALE_S - 1)
    _age(fresh, NOW - 5)
    cache.put(_entry(2), NOW)
    assert not stale.exists()
    assert fresh.exists()


def test_an_unstorable_entry_writes_nothing(cache, root):
    cache.put(_entry(1, etag=None, last_modified=None), NOW)
    cache.put(_entry(2, body="x" * (etag_cache.BODY_MAX_BYTES + 1)), NOW)
    assert not root.exists()


def test_directories_are_0700_and_files_0600_under_any_umask(cache, root):
    old = os.umask(0o000)
    try:
        product = root / PRODUCT
        product.mkdir(parents=True)
        product.chmod(0o755)
        root.chmod(0o755)
        cache.put(_entry(1), NOW)
        assert _mode(root) == 0o700
        assert _mode(product) == 0o700
        assert _mode(_path(root, _key(1))) == 0o600
    finally:
        os.umask(old)


def test_a_fresh_tree_is_0700_and_0600_too(cache, root):
    old = os.umask(0o000)
    try:
        cache.put(_entry(1), NOW)
    finally:
        os.umask(old)
    assert _mode(root) == 0o700
    assert _mode(root / PRODUCT) == 0o700
    assert _mode(_path(root, _key(1))) == 0o600


def test_put_does_not_touch_the_parent_of_the_root(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    parent.chmod(0o755)
    FilesystemGithubCache(parent / "github", PRODUCT, REPO).put(_entry(1), NOW)
    assert _mode(parent) == 0o755


def test_a_root_that_is_a_regular_file_raises_oserror_and_leaves_nothing(tmp_path):
    blocker = tmp_path / "github"
    blocker.write_text("not a directory")
    with pytest.raises(OSError):
        FilesystemGithubCache(blocker, PRODUCT, REPO).put(_entry(1), NOW)
    assert blocker.read_text() == "not a directory"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["github"]


def test_a_failing_replace_leaves_no_tmp_and_the_previous_entry_intact(cache, root, monkeypatch):
    cache.put(_entry(1), NOW)
    path = _path(root, _key(1))
    before = path.read_bytes()

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        cache.put(_entry(1, body='{"n": "new"}'), NOW + 1)
    assert path.read_bytes() == before
    assert _files(root) == [f"{_key(1)}.json"]


def test_a_failing_write_raises_oserror_not_another_error(cache, root, monkeypatch):
    cache.put(_entry(1), NOW)

    def refuse(*args: object, **kwargs: object) -> None:
        raise MemoryError

    monkeypatch.setattr(os, "fdopen", refuse)
    with pytest.raises(MemoryError):
        cache.put(_entry(2), NOW)
    assert _files(root) == [f"{_key(1)}.json"]
