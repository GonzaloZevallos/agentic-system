"""`FilesystemGithubCache`, over `Configuration.github_cache_dir`: one product's store of
GitHub REST responses, under `<root>/<product key>`, one `<key>.json` per request.

Nothing here decides whether an entry is usable: version, repo, field types, expiry and
storability are `domain.etag_cache`'s. The adapter reads and writes bytes and uses the
file's mtime as the clock of last use (a valid read renews it), so the cache is bounded
without a database. Directories are 0700 and files 0600: a body is the product repo's data,
readable only by this uid. A read never raises and never creates anything; a write raises
only `OSError` and never leaves a partial or `.tmp` file."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

from ...domain import etag_cache
from ...domain.etag_cache import CacheEntry

_KEY = re.compile(r"[0-9a-f]{64}")
_TMP_PREFIX = ".entry-"
_TMP_SUFFIX = ".tmp"


class FilesystemGithubCache:
    def __init__(self, root: Path, product_key: str, repo: str):
        self.root = root
        self.dir = root / product_key
        self.repo = repo

    def get(self, key: str, now: float) -> CacheEntry | None:
        if not _KEY.fullmatch(key):
            return None
        path = self.dir / f"{key}.json"
        try:
            if etag_cache.expired(path.stat().st_mtime, now):
                return None
            entry = etag_cache.parse(path.read_text(encoding="utf-8"), key, self.repo)
        except (OSError, ValueError):
            return None
        if entry is not None:
            # A use renews the entry; failing to is no reason to answer differently.
            with contextlib.suppress(OSError):
                os.utime(path, (now, now))
        return entry

    def put(self, entry: CacheEntry, now: float) -> None:
        key = entry.key
        if not _KEY.fullmatch(key):
            raise ValueError(f"not a cache key: {key!r}")
        if entry.repo != self.repo:
            raise ValueError(f"an entry of {entry.repo!r} does not belong to {self.repo!r}")
        if not etag_cache.storable(entry):
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        # The umask must not decide who can read a body: set it on every write.
        os.chmod(self.root, 0o700)
        os.chmod(self.dir, 0o700)
        handle, temporary = tempfile.mkstemp(dir=self.dir, prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(etag_cache.to_json(entry))
            # The file's own clock is the injected one, so expiry and pruning age it the
            # way `get` does and a test never has to wait.
            os.utime(temporary, (now, now))
            os.replace(temporary, self.dir / f"{key}.json")
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        self._prune(now)

    def _prune(self, now: float) -> None:
        """Expired entries go, then the oldest above `MAX_ENTRIES`, then the oldest until
        the files fit `TOTAL_MAX_BYTES`, then the `.tmp` a dead writer left. Linear in the
        directory listing, `stat` only, and only on writes."""
        live: list[tuple[float, str, int]] = []
        stale: list[Path] = []
        try:
            listing = list(self.dir.iterdir())
        except OSError:
            return
        for path in listing:
            try:
                info = path.stat()
            except OSError:
                continue
            if path.name.endswith(".json"):
                if etag_cache.expired(info.st_mtime, now):
                    _remove(path)
                else:
                    live.append((info.st_mtime, path.name, info.st_size))
            elif path.name.startswith(_TMP_PREFIX) and path.name.endswith(_TMP_SUFFIX):
                if now - info.st_mtime > etag_cache.TMP_STALE_S:
                    stale.append(path)
        live.sort()
        total = sum(size for _, _, size in live)
        while live and (len(live) > etag_cache.MAX_ENTRIES or total > etag_cache.TOTAL_MAX_BYTES):
            _, name, size = live.pop(0)
            total -= size
            _remove(self.dir / name)
        for path in stale:
            _remove(path)


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
