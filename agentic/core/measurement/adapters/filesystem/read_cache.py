"""The scores backfill's read cache on disk: one JSON file per repository, the raw pages of
its issues listing and nothing derived from them."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ...domain.issue_listing import ListedIssue, PageRecord

_VERSION = 1


class FilesystemReadCache:
    """`root/<owner>__<repo>.json` for `repo` (`owner/repo`, a key `repo_key` made). The owner
    never holds `_`, so two repositories never share a name."""

    def __init__(self, root: Path, repo: str):
        self._path = root / f"{repo.replace('/', '__')}.json"

    def load(self) -> dict[int, PageRecord]:
        """The saved pages; a missing, unreadable, corrupt or wrong-version file is `{}`."""
        try:
            return _decode(json.loads(self._path.read_text(encoding="utf-8")))
        except (ValueError, OSError, TypeError, KeyError, AttributeError):
            return {}

    def save(self, pages: Mapping[int, PageRecord]) -> None:
        _write_atomic(self._path, json.dumps(_encode(pages), separators=(",", ":")))


def _write_atomic(path: Path, text: str) -> None:
    """The one write of the cache: a temp file named with this process's pid in the same
    directory, then `os.replace`, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _encode(pages: Mapping[int, PageRecord]) -> dict[str, Any]:
    return {
        "v": _VERSION,
        "pages": {
            str(number): {
                "read_at": record.read_at.isoformat(),
                "open": record.open,
                "items": [
                    {
                        "number": item.number,
                        "created_at": item.created_at,
                        "is_pr": item.is_pr,
                        "pr_merged_at": item.pr_merged_at,
                        "state": item.state,
                    }
                    for item in record.items
                ],
            }
            for number, record in pages.items()
        },
    }


def _decode(payload: Any) -> dict[int, PageRecord]:
    if payload["v"] != _VERSION or not isinstance(payload["pages"], dict):
        raise ValueError("not a cache of this version")
    return {_page_number(key): _record(entry) for key, entry in payload["pages"].items()}


def _page_number(key: str) -> int:
    number = int(key)
    if number < 1 or str(number) != key:
        raise ValueError("bad page number")
    return number


def _record(entry: Any) -> PageRecord:
    read_at, is_open = datetime.fromisoformat(entry["read_at"]), entry["open"]
    if read_at.tzinfo is None or type(is_open) is not bool or not isinstance(entry["items"], list):
        raise ValueError("bad page")
    return PageRecord(read_at, is_open, tuple(_item(raw) for raw in entry["items"]))


def _item(raw: Any) -> ListedIssue:
    number, created_at = raw["number"], raw["created_at"]
    is_pr, merged_at, state = raw["is_pr"], raw["pr_merged_at"], raw["state"]
    if (
        type(number) is not int
        or not isinstance(created_at, str)
        or type(is_pr) is not bool
        or not (merged_at is None or isinstance(merged_at, str))
        or state not in ("open", "closed")
    ):
        raise ValueError("bad item")
    return ListedIssue(number, created_at, is_pr, merged_at, state)
