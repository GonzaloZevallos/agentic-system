"""The pure rule of the GitHub response cache: the key an entry lives under, the shape it
must have to be read back, when it has expired and what is worth storing. No I/O and no
clock; the adapter (`adapters/filesystem/github_cache.py`) brings both.

An entry is never an answer by itself: a reader always revalidates it against GitHub with
its `etag` or `last_modified`, so no `max-age` is honoured and the only thing that makes an
entry unusable is that it cannot be trusted to be what was written for this key and repo.
Everything else (torn, unreadable, other version, other repo, wrong types) is a full read.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

VERSION = 1
# An entry nobody read for this long is dead weight: expiry is measured from the file's
# mtime, which every valid read renews, so a hot entry never ages out.
READ_TTL_S = 7 * 86400
# An mtime this far past `now` is a clock step or a copied directory, not a use: it would
# otherwise outlive the TTL by however far ahead it is.
SKEW_S = 300
MAX_ENTRIES = 1000
BODY_MAX_BYTES = 4 * 1024 * 1024
TOTAL_MAX_BYTES = 64 * 1024 * 1024
TMP_STALE_S = 600


@dataclass(frozen=True)
class CacheEntry:
    """What GitHub answered for one request, with the validators to ask again.
    `fetched_at` is informational: expiry reads the file's mtime."""

    v: int
    repo: str
    key: str
    etag: str | None
    last_modified: str | None
    body: str
    fetched_at: float


def entry_key(repo: str, endpoint: str, query: tuple[tuple[str, str], ...], page: int) -> str:
    """The key of one request: the sha256 of its JSON, in the order and multiplicity the
    pairs come in and with the endpoint as given. The default `ensure_ascii` keeps the text
    pure ASCII, so a lone surrogate encodes instead of raising: total over any `str`."""
    document = json.dumps(
        [repo, endpoint, [list(pair) for pair in query], page], separators=(",", ":")
    )
    return hashlib.sha256(document.encode("ascii")).hexdigest()


def to_json(entry: CacheEntry) -> str:
    """The entry as the adapter stores it: the format lives only here."""
    return json.dumps(asdict(entry))


def parse(raw: str, key: str, repo: str) -> CacheEntry | None:
    """The entry `raw` holds for `key` and `repo`, or `None` for anything else. Never raises."""
    try:
        return _entry_of(json.loads(raw), key, repo)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return None


def _entry_of(data: object, key: str, repo: str) -> CacheEntry | None:
    if not isinstance(data, dict):
        return None
    v, found_repo, found_key = data.get("v"), data.get("repo"), data.get("key")
    if isinstance(v, bool) or not isinstance(v, int) or v != VERSION:
        return None
    if not (isinstance(found_repo, str) and found_repo == repo):
        return None
    if not (isinstance(found_key, str) and found_key == key):
        return None
    etag, last_modified, body = data.get("etag"), data.get("last_modified"), data.get("body")
    if not (etag is None or isinstance(etag, str)):
        return None
    if not (last_modified is None or isinstance(last_modified, str)):
        return None
    if etag is None and last_modified is None:
        return None
    if not isinstance(body, str):
        return None
    fetched_at = data.get("fetched_at")
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, int | float):
        return None
    if not math.isfinite(fetched_at):
        return None
    return CacheEntry(VERSION, repo, key, etag, last_modified, body, float(fetched_at))


def expired(last_used: float, now: float) -> bool:
    """Whether an entry last used at `last_used` (its mtime) is dead at `now`: unread for
    `READ_TTL_S`, or used further in the future than `SKEW_S` allows."""
    return now - last_used >= READ_TTL_S or last_used > now + SKEW_S


def storable(entry: CacheEntry) -> bool:
    """An entry worth keeping: something to revalidate with and a body within the limit."""
    if entry.etag is None and entry.last_modified is None:
        return False
    # `surrogatepass` cannot raise on a lone surrogate, which `str` allows.
    return len(entry.body.encode("utf-8", "surrogatepass")) <= BODY_MAX_BYTES
