"""`Heartbeats` over one directory (GH-735): one file `<root>/heartbeats/<key>.json` per
poller holding `{checkout, at, phase, interval_s}`. This module is the only reader and writer
of that directory. A key names ONE poller of ONE product (`<product>-<LOCK_KEY>`); no code
builds a key from `poll.LOCK_KEY` alone, because two products share that lock key.

The files can be written by any local process, so the reader trusts nothing in them: a wrong
type, an unknown phase, a relative checkout, a naive `at` or an `interval_s` that is not a
finite number in (0, `HEARTBEAT_INTERVAL_MAX_S`] reads as `Unreadable`, and nothing read from
a file raises out of `read_all`. Every write replaces the file whole: temp file, fsync,
`os.replace`."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ...domain.stage0_rotation import Heartbeat, Unreadable

HEARTBEAT_INTERVAL_MAX_S = 86400
"""The longest interval a heartbeat may promise: one day."""

_SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_WRITE_PHASES = ("start", "end", "stop")
_READ_PHASES = ("start", "end")


def _aware(moment: datetime) -> bool:
    return moment.tzinfo is not None and moment.utcoffset() is not None


def _interval(value: object) -> float | None:
    """`value` as a float when it is a finite number in (0, the bound]; a bool is not one."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not 0 < value <= HEARTBEAT_INTERVAL_MAX_S:
        return None
    return float(value)


class FilesystemHeartbeats:
    def __init__(self, root: Path) -> None:
        self._dir = root / "heartbeats"

    def write(
        self,
        key: str,
        checkout: Path,
        at: datetime,
        phase: Literal["start", "end", "stop"],
        interval_s: float,
    ) -> None:
        if _SLUG.fullmatch(key) is None:
            raise ValueError(f"not a heartbeat key: {key!r}")
        if not checkout.is_absolute():
            raise ValueError("heartbeat checkout must be absolute")
        if not _aware(at):
            raise ValueError("heartbeat at must be timezone-aware")
        if phase not in _WRITE_PHASES:
            raise ValueError(f"not a heartbeat phase: {phase!r}")
        if _interval(interval_s) is None:
            raise ValueError(f"not a heartbeat interval: {interval_s!r}")
        data = json.dumps(
            {
                "checkout": str(checkout),
                "at": at.isoformat(),
                "phase": phase,
                "interval_s": interval_s,
            }
        ).encode()
        self._dir.mkdir(parents=True, exist_ok=True)
        temp = self._dir / f".tmp-{uuid4().hex}"
        try:
            with open(temp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._dir / f"{key}.json")
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def read_all(self) -> Mapping[str, Heartbeat | Unreadable]:
        try:
            entries = sorted(self._dir.glob("*.json"))
        except OSError:
            return {}
        return {entry.stem: self._read(entry) for entry in entries}

    def _read(self, path: Path) -> Heartbeat | Unreadable:
        try:
            loaded: Any = json.loads(path.read_bytes())
            return self._beat(path.stem, loaded)
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            return Unreadable()

    @staticmethod
    def _beat(key: str, loaded: Any) -> Heartbeat | Unreadable:
        if not isinstance(loaded, dict):
            return Unreadable()
        checkout, at, phase = loaded.get("checkout"), loaded.get("at"), loaded.get("phase")
        interval_s = _interval(loaded.get("interval_s"))
        if not isinstance(checkout, str) or not isinstance(at, str):
            return Unreadable()
        if phase not in _READ_PHASES or interval_s is None:
            return Unreadable()
        moment = datetime.fromisoformat(at)
        path = Path(checkout)
        if not _aware(moment) or not path.is_absolute():
            return Unreadable()
        return Heartbeat(key, path.resolve(), moment, interval_s)
