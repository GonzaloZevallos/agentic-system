"""`PerfLock` over one file: the machine-wide lock that keeps two measurements off the same
CPU (GH-318, ADR-0059). Unlike `FilesystemRunLock` (one file per task, under the product),
this is one file for the whole machine, and its holder is named by a record of its pid and
the boot it ran in; `liveness.is_alive` decides whether that holder is still there, the same
rule the run lock and the registry use, so a record from another boot or from a dead pid is
dead and is taken over.

The record is written whole to a private file and hard-linked to the lock path, which fails
if the path exists, so two processes never both think they took it and a waiter never reads a
half-written record. A dead or unreadable record is removed and the exclusive create tried
again; a live one is waited for, at `POLL_INTERVAL_S`, until `wait_s` is spent. Two waiters
that both find the same dead record may both unlink it: the second unlink can take a record
the first waiter has just written. The record is re-read right before the unlink to keep that
window to a few instructions; it is not closed, and the cost of the rare double take is two
measurements on one CPU, which the load averages in the table show.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .liveness import current_boot_id, is_alive

# Declared, not measured: how often a waiter looks at a held lock again.
POLL_INTERVAL_S = 1.0


class FilesystemPerfLock:
    def __init__(
        self,
        path: Path,
        boot_id: Callable[[], str | None] = current_boot_id,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path
        self._boot_id = boot_id
        self._clock = clock
        self._sleep = sleep

    def acquire(self, wait_s: float) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = self._clock() + wait_s
        while True:
            if self._create():
                return True
            if not self._holder_alive():
                self._remove_dead()
                continue
            if self._clock() >= deadline:
                return False
            self._sleep(POLL_INTERVAL_S)

    def release(self) -> None:
        record = self._read()
        if record is None:
            return
        if record == (os.getpid(), self._boot_id()):
            with contextlib.suppress(FileNotFoundError):
                self._path.unlink()

    def _create(self) -> bool:
        """The record is written whole to a private file and then linked to the lock path:
        `link` fails if the path exists, like `O_EXCL`, and the lock never shows a waiter an
        empty file that it would read as unreadable and take from a live holder."""
        record = {
            "pid": os.getpid(),
            "boot_id": self._boot_id(),
            "host": socket.gethostname(),
            "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        private = self._path.with_name(f"{self._path.name}.{os.getpid()}.{uuid4().hex}")
        private.write_text(json.dumps(record), encoding="utf-8")
        try:
            os.link(private, self._path)
        except FileExistsError:
            return False
        finally:
            private.unlink(missing_ok=True)
        return True

    def _read(self) -> tuple[int, str | None] | None:
        """The holder's `(pid, boot id)`; `None` when the file is gone or says nothing usable."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        pid, boot = data.get("pid"), data.get("boot_id")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
        return pid, boot if isinstance(boot, str) else None

    def _holder_alive(self) -> bool:
        if not self._path.exists():
            return False  # released between our create and our read: the next create takes it
        record = self._read()
        if record is None:
            return False
        return is_alive(record[0], record[1], self._boot_id())

    def _remove_dead(self) -> None:
        if self._holder_alive():  # a waiter took it over since we looked
            return
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()
