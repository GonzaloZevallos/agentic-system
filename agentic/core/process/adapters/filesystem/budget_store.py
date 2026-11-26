"""`FilesystemBudgetStore`, over `Configuration.github_budget_dir`: the GitHub account's
request budget (GH-1071), one `<account>.json` per account and one `<account>.lock` taken
with `flock` for a `claim` or a `put`, shared by every process of this machine.

The directory is the user's own (0700) and is never followed through a symlink; the files
hold rate-limit counters only, named by a hash of the token (the token is never seen here).
Anything that cannot be read or written -- a foreign directory, a lock held for too long, a
full disk, a torn file -- reads as unknown: nothing raises past the store but the
`ValueError` of an account key of another shape."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ...domain.github_budget import (
    BUDGET_TTL,
    RESOURCES,
    UNKNOWN,
    Budget,
    Resource,
    ResourceBudget,
)

_ACCOUNT = re.compile(r"github\.com-[0-9a-f]{16}")
_VERSION = 1
_LOCK_WAIT_S = 1.0
_LOCK_POLL_S = 0.05
_FIELDS = {"version", "read_at", "fetched_at", "resources"}
_ENTRY_FIELDS = {"remaining", "reset"}


@dataclass(frozen=True)
class _Stored:
    read_at: datetime
    budget: Budget


class FilesystemBudgetStore:
    def __init__(
        self,
        directory: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._dir = directory
        # The lock's wait is measured with these, so a test passes a clock that only moves
        # when it sleeps and never waits for real.
        self._clock = clock
        self._sleep = sleep

    def load(self, key: str) -> Budget | None:
        _check(key)
        try:
            if not self._prepare():
                return None
            stored = self._read(key)
        except OSError:
            return None
        return None if stored is None else stored.budget

    def claim(self, key: str, now: datetime) -> datetime | None:
        _check(key)
        at = _whole_seconds(now)
        try:
            with self._locked(key) as held:
                if not held:
                    return None
                stored = self._read(key)
                if stored is not None and stored.read_at <= at and at - stored.read_at < BUDGET_TTL:
                    return None
                self._write(key, at, UNKNOWN if stored is None else stored.budget)
                return at
        except OSError:
            return None

    def put(self, key: str, claim: datetime, budget: Budget) -> None:
        _check(key)
        at = _whole_seconds(claim)
        try:
            with self._locked(key) as held:
                if not held:
                    return
                stored = self._read(key)
                if stored is not None and stored.read_at > at:
                    return  # a later claim already took the read: this one is late
                self._write(key, at, budget)
        except OSError:
            return

    # -- directory and lock ---------------------------------------------------------

    def _prepare(self) -> bool:
        """The directory exists, is ours and is 0700; `False` disables the call."""
        try:
            info = os.lstat(self._dir)
        except FileNotFoundError:
            self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = os.lstat(self._dir)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            return False
        os.chmod(self._dir, 0o700)
        return True

    @contextlib.contextmanager
    def _locked(self, key: str) -> Iterator[bool]:
        """`<key>.lock`, `flock(LOCK_EX | LOCK_NB)` polled for at most `_LOCK_WAIT_S`,
        released when the block ends. `False` when the call is disabled or the lock was not
        taken in time; an `OSError` while opening or locking propagates to the caller."""
        if not self._prepare():
            yield False
            return
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        fd = os.open(self._dir / f"{key}.lock", flags, 0o600)
        try:
            deadline = self._clock() + _LOCK_WAIT_S
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:  # held by another process
                    if self._clock() >= deadline:
                        yield False
                        return
                    self._sleep(_LOCK_POLL_S)
            yield True
        finally:
            os.close(fd)  # closing the descriptor drops the flock

    # -- the file -------------------------------------------------------------------

    def _read(self, key: str) -> _Stored | None:
        try:
            fd = os.open(self._dir / f"{key}.json", os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, encoding="utf-8") as handle:
            try:
                return _parse(json.load(handle))
            except ValueError:  # torn, or not UTF-8
                return None

    def _write(self, key: str, read_at: datetime, budget: Budget) -> None:
        """The record whole, once: a temp file in the same directory then `os.replace`, so
        a reader never sees half a record."""
        record = {
            "version": _VERSION,
            "read_at": _epoch(read_at),
            "fetched_at": None if budget.fetched_at is None else _epoch(budget.fetched_at),
            "resources": {
                resource: {"remaining": known.remaining, "reset": _epoch(known.reset)}
                for resource in RESOURCES
                if (known := budget.resources.get(resource)) is not None
            },
        }
        fd, temp = tempfile.mkstemp(dir=self._dir, prefix=f"{key}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(record, handle)
            os.replace(temp, self._dir / f"{key}.json")
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise


def _check(key: str) -> None:
    if _ACCOUNT.fullmatch(key) is None:
        raise ValueError("account key must be github.com- and 16 lowercase hex characters")


def _whole_seconds(instant: datetime) -> datetime:
    return datetime.fromtimestamp(_epoch(instant), tz=UTC)


def _epoch(instant: datetime) -> int:
    return int(instant.timestamp() // 1)


def _int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _instant(value: object) -> datetime | None:
    seconds = _int(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse(data: object) -> _Stored | None:
    """The record a file holds, `None` for anything that is not exactly the schema."""
    if not isinstance(data, dict) or data.keys() != _FIELDS:
        return None
    version = data["version"]
    if isinstance(version, bool) or version != _VERSION:
        return None
    read_at = _instant(data["read_at"])
    fetched_at = None if data["fetched_at"] is None else _instant(data["fetched_at"])
    stored = data["resources"]
    if read_at is None or (data["fetched_at"] is not None and fetched_at is None):
        return None
    if not isinstance(stored, dict) or not stored.keys() <= set(RESOURCES):
        return None
    read: dict[Resource, ResourceBudget | None] = {}
    for resource in RESOURCES:
        entry = stored.get(resource)
        if entry is None:
            read[resource] = None
            continue
        parsed = _entry(entry)
        if parsed is None:
            return None
        read[resource] = parsed
    return _Stored(read_at, Budget(read, fetched_at))


def _entry(entry: object) -> ResourceBudget | None:
    if not isinstance(entry, Mapping) or entry.keys() != _ENTRY_FIELDS:
        return None
    remaining, reset = _int(entry["remaining"]), _instant(entry["reset"])
    if remaining is None or reset is None:
        return None
    return ResourceBudget(remaining, reset)
