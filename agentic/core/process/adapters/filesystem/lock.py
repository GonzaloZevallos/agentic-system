"""`RunLock` over `.agentic/runs/<task>.lock`: one JSON file per task, holding the pid,
host, start time and command of the process that holds it (Tech Lead plan, subtask
`run-lock`)."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ...application.ports import LockHeld, LockRecord
from .liveness import current_boot_id, is_alive


class FilesystemRunLock:
    def __init__(self, runs_dir: Path, boot_id: Callable[[], str | None] = current_boot_id):
        self.runs_dir = runs_dir
        self._boot_id = boot_id

    def read(self, task_id: str) -> LockRecord | None:
        path = self._path(task_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockRecord(
            data["pid"],
            data["host"],
            data["started_at"],
            data["command"],
            data.get("boot_id"),
            bool(data.get("stopped_at_shutdown", False)),
        )

    def acquire(self, task_id: str, command: str) -> LockRecord | None:
        existing = self.read(task_id)
        taken_over = None
        if existing is not None:
            message = self._refusal_message(task_id, existing)
            if message is not None:
                raise LockHeld(existing, message)
            taken_over = existing
        self._write(
            task_id,
            pid=os.getpid(),
            host=self._host(),
            started_at=_now(),
            command=command,
            boot_id=self._boot_id(),
        )
        return taken_over

    def refusal(self, task_id: str) -> str | None:
        existing = self.read(task_id)
        if existing is None:
            return None
        return self._refusal_message(task_id, existing)

    def _refusal_message(self, task_id: str, existing: LockRecord) -> str | None:
        """The one place `acquire` and `refusal` share: never probes a pid recorded
        on another host, and produces the same two message shapes either way."""
        if existing.host != self._host():
            return (
                f"{task_id} is locked by pid {existing.pid} on {existing.host} "
                f"since {existing.started_at}"
            )
        if is_alive(existing.pid, existing.boot_id, self._boot_id()):
            return (
                f"{task_id} is already running: pid {existing.pid} on {existing.host} "
                f"since {existing.started_at} ({existing.command})"
            )
        return None

    def release(self, task_id: str) -> None:
        existing = self.read(task_id)
        if existing is None:
            return
        if existing.pid == os.getpid() and existing.host == self._host():
            self._path(task_id).unlink()

    def keep_at_shutdown(self, task_id: str) -> None:
        existing = self.read(task_id)
        if existing is None:
            return
        if existing.pid == os.getpid() and existing.host == self._host():
            self._write(
                task_id,
                pid=existing.pid,
                host=existing.host,
                started_at=existing.started_at,
                command=existing.command,
                boot_id=existing.boot_id,
                stopped_at_shutdown=True,
            )

    def _path(self, task_id: str) -> Path:
        return self.runs_dir / f"{task_id}.lock"

    def _host(self) -> str:
        return socket.gethostname()

    def _write(
        self,
        task_id: str,
        *,
        pid: int,
        host: str,
        started_at: str,
        command: str = "",
        boot_id: str | None = None,
        stopped_at_shutdown: bool = False,
    ) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        record: dict[str, object] = {
            "pid": pid,
            "host": host,
            "started_at": started_at,
            "command": command,
            "boot_id": boot_id,
        }
        if stopped_at_shutdown:
            record["stopped_at_shutdown"] = True
        self._path(task_id).write_text(json.dumps(record), encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
