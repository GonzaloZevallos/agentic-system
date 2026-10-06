"""`FilesystemRunRegistry`, over `Configuration.run_registry_dir`: how many `agentic`
invocations are alive on this machine right now, across every product (GH-137,
`AGENTIC_MAX_CONCURRENT_RUNS` -- the account's own 429 is not scoped to one product),
and the poller's own schedule for a launch attempt that has not registered a run yet.

Two kinds of file live under the same directory, all plain JSON written whole: one
entry per live process, named by its own pid (`<pid>.json`, mirroring
`FilesystemRunLock`'s one-file-per-task shape), and one per launch attempt that has
not cleared yet (`launches/<product>-<issue>.json`)."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import socket
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from ...application.ports import LaunchAttempt, RunRegistration
from .liveness import current_boot_id, is_alive

_LOCK_NAME = ".lock"
_LAUNCHES_DIR = "launches"


class FilesystemRunRegistry:
    def __init__(self, dir: Path, boot_id: Callable[[], str | None] = current_boot_id):
        self.dir = dir
        self._boot_id = boot_id

    # -- live runs ----------------------------------------------------------------

    def register(self, product: str, task_id: str) -> None:
        self._write(os.getpid(), _host(), product, task_id, self._boot_id())

    def unregister(self) -> None:
        self._path(os.getpid()).unlink(missing_ok=True)

    def live(self) -> list[RunRegistration]:
        if not self.dir.is_dir():
            return []
        here = _host()
        current = self._boot_id()
        entries = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pid, host = int(data["pid"]), str(data["host"])
            except (OSError, ValueError, KeyError):
                continue
            # Never probed: a pid recorded on another host is a coin flip, the same
            # rule `FilesystemRunLock` already enforces. An entry from another boot is
            # dead whatever the pid says (`liveness.is_alive`).
            if host == here and is_alive(pid, data.get("boot_id"), current):
                entries.append(RunRegistration(pid, host, data["product"], data["task_id"]))
        return entries

    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / _LOCK_NAME, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _write(
        self, pid: int, host: str, product: str, task_id: str, boot_id: str | None = None
    ) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {
            "pid": pid,
            "host": host,
            "product": product,
            "task_id": task_id,
            "boot_id": boot_id,
        }
        self._path(pid).write_text(json.dumps(record), encoding="utf-8")

    def _path(self, pid: int) -> Path:
        return self.dir / f"{pid}.json"

    # -- launch schedule ------------------------------------------------------------

    def launch_attempts(self, product: str, issue: int) -> LaunchAttempt | None:
        path = self._launch_path(product, issue)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return LaunchAttempt(datetime.fromisoformat(data["since"]), int(data["prior"]))

    def record_launch(self, product: str, issue: int, when: datetime) -> None:
        path = self._launch_path(product, issue)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.launch_attempts(product, issue)
        prior = existing.prior if existing is not None else 0
        record = {"since": when.isoformat(), "prior": prior + 1}
        path.write_text(json.dumps(record), encoding="utf-8")

    def clear_launch(self, product: str, issue: int) -> None:
        self._launch_path(product, issue).unlink(missing_ok=True)

    def _launch_path(self, product: str, issue: int) -> Path:
        return self.dir / _LAUNCHES_DIR / f"{product}-{issue}.json"


def _host() -> str:
    return socket.gethostname()
