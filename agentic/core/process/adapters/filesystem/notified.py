"""`FilesystemNotified`: the email notifier's memory of what it settled, one JSON file at a path
the caller built (GH-1056). It never derives a name from a product: a later part builds the path.

The file is written whole and atomically (a temp file beside it, flushed and fsynced, then
`os.replace`), so a crash never leaves half of it. A file that is missing, unreadable or partly
malformed loads as the empty record, as a whole. Known limits (ADR-0106): a corrupt file loses up
to 7 days of pending events. Unknown extra fields are ignored. A key is written with its product
as the key has it, with no escaping; the record holds no free text.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ...domain.notify import DigestKey, EventKey, NotifiedRecord, NotifyKey, Outcome

_EMPTY = NotifiedRecord(None, None, None, {})
_OUTCOMES: dict[str, Outcome] = {
    "sent": "sent",
    "superseded": "superseded",
    "covered": "covered",
    "unbuildable": "unbuildable",
}


class FilesystemNotified:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> NotifiedRecord:
        """The saved record; the empty one when the file is missing, unreadable or malformed."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _EMPTY
        try:
            return _record_of(raw)
        except (KeyError, TypeError, ValueError, AttributeError):
            return _EMPTY

    def save(self, record: NotifiedRecord) -> None:
        """Writes `record` whole: a temp file beside the target, fsynced, then `os.replace`."""
        body = {
            "started_at": _stamp(record.started_at),
            "failed_at": _stamp(record.failed_at),
            "activity_read_at": _stamp(record.activity_read_at),
            "keys": [_key_json(key, outcome) for key, outcome in record.keys.items()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(json.dumps(body))
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(temp).unlink(missing_ok=True)
            raise


def _stamp(instant: datetime | None) -> str | None:
    return instant.isoformat() if instant is not None else None


def _key_json(key: NotifyKey, outcome: Outcome) -> dict[str, Any]:
    if isinstance(key, EventKey):
        return {
            "kind": key.kind,
            "product": key.product,
            "issue": key.issue,
            "instant": key.instant.isoformat(),
            "outcome": outcome,
        }
    return {
        "kind": "digest",
        "product": key.product,
        "local_date": key.local_date.isoformat(),
        "outcome": outcome,
    }


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("not a string")
    return value


def _instant(value: object) -> datetime:
    parsed = datetime.fromisoformat(_text(value))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("not an aware UTC instant")
    return parsed.astimezone(UTC)


def _optional_instant(value: object) -> datetime | None:
    return None if value is None else _instant(value)


def _outcome(value: object) -> Outcome:
    if not isinstance(value, str) or value not in _OUTCOMES:
        raise ValueError(f"unknown outcome: {value!r}")
    return _OUTCOMES[value]


def _key_of(raw: object) -> tuple[NotifyKey, Outcome]:
    if not isinstance(raw, dict):
        raise TypeError("a key is not an object")
    kind, product, outcome = raw["kind"], _text(raw["product"]), _outcome(raw["outcome"])
    if kind == "waiting" or kind == "release":
        issue = raw["issue"]
        if isinstance(issue, bool) or not isinstance(issue, int):
            raise TypeError("issue")
        return EventKey(kind, product, issue, _instant(raw["instant"])), outcome
    if kind == "digest":
        return DigestKey(product, date.fromisoformat(_text(raw["local_date"]))), outcome
    raise ValueError(f"unknown kind: {kind!r}")


def _record_of(raw: object) -> NotifiedRecord:
    if not isinstance(raw, dict):
        raise TypeError("the record is not an object")
    items = raw["keys"]
    if not isinstance(items, list):
        raise TypeError("keys")
    keys: dict[NotifyKey, Outcome] = {}
    for item in items:
        key, outcome = _key_of(item)
        if key in keys:
            raise ValueError("duplicate key")
        keys[key] = outcome
    return NotifiedRecord(
        started_at=_optional_instant(raw["started_at"]),
        failed_at=_optional_instant(raw["failed_at"]),
        activity_read_at=_optional_instant(raw["activity_read_at"]),
        keys=keys,
    )
