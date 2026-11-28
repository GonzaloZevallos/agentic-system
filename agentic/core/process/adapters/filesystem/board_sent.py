"""`FilesystemBoardSent`: the board mirror's memory of what it sent, one JSON file per
product under the machine state dir (GH-375, ADR-0073).

The file is written whole and atomically (a temp file beside it, then `os.replace`), so a
crash never leaves half of it. A file that cannot be read, or that does not hold the shape
this module writes, reads as an empty memory with `unreadable` set: the mirror logs one line
and sends everything again, which is idempotent. Deleting the file is the supported reset.
The file carries a format `version`: one that does not carry `VERSION` (an older shape, or none)
reads the same way, so a change of what the memory means resends everything once.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

from ...application.ports import SentEntry, SentMemory
from ...domain.board import FIELDS, Field

VERSION = 2
"""The shape of the file: 2 added `hecho_until` and `unread`, and the `motivo` field."""


class FilesystemBoardSent:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> SentMemory:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return SentMemory()
        except (OSError, ValueError):
            return SentMemory(unreadable=True)
        try:
            return _memory_of(raw)
        except (KeyError, TypeError, ValueError, AttributeError):
            return SentMemory(unreadable=True)

    def save(self, memory: SentMemory) -> None:
        record = {
            "version": VERSION,
            "project_id": memory.project_id,
            "failed_at": memory.failed_at.isoformat() if memory.failed_at else None,
            "issues": {
                str(number): {
                    "fingerprint": e.fingerprint,
                    "present": sorted(e.present),
                    "hecho_until": e.hecho_until.isoformat() if e.hecho_until else None,
                    "unread": e.unread,
                }
                for number, e in memory.issues.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(json.dumps(record))
            os.replace(temp, self.path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


def _field(name: object) -> Field:
    for known in FIELDS:
        if name == known:
            return known
    raise ValueError(f"not a board field: {name!r}")


def _memory_of(raw: object) -> SentMemory:
    if not isinstance(raw, dict):
        raise TypeError("the memory is not an object")
    if raw["version"] != VERSION:
        raise ValueError("an older memory")
    project_id, failed_at, issues = raw["project_id"], raw["failed_at"], raw["issues"]
    if project_id is not None and not isinstance(project_id, str):
        raise TypeError("project_id")
    entries = {
        int(number): SentEntry(
            fingerprint=_text(entry["fingerprint"]),
            present=frozenset(_field(name) for name in entry["present"]),
            hecho_until=_day(entry["hecho_until"]),
            unread=_count(entry["unread"]),
        )
        for number, entry in issues.items()
    }
    return SentMemory(
        project_id=project_id,
        failed_at=datetime.fromisoformat(failed_at) if failed_at is not None else None,
        issues=entries,
    )


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("fingerprint")
    return value


def _day(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("hecho_until")
    return date.fromisoformat(value)


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError("unread")
    return value
