"""`RunLock` over `.agentic/runs/<task>.lock`: one JSON file per task, holding the pid,
host, start time and command of the process that holds it (Tech Lead plan, subtask
`run-lock`)."""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

from ...application.ports import LockHeld, LockRecord


class FilesystemRunLock:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir

    def read(self, task_id: str) -> LockRecord | None:
        path = self._path(task_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockRecord(data["pid"], data["host"], data["started_at"], data["command"])

    def acquire(self, task_id: str, command: str) -> LockRecord | None:
        existing = self.read(task_id)
        taken_over = None
        if existing is not None:
            if existing.host != self._host():
                raise LockHeld(
                    existing,
                    f"{task_id} is locked by pid {existing.pid} on {existing.host} "
                    f"since {existing.started_at}",
                )
            if _alive(existing.pid):
                raise LockHeld(
                    existing,
                    f"{task_id} is already running: pid {existing.pid} on {existing.host} "
                    f"since {existing.started_at} ({existing.command})",
                )
            taken_over = existing
        self._write(task_id, pid=os.getpid(), host=self._host(), started_at=_now(), command=command)
        return taken_over

    def release(self, task_id: str) -> None:
        path = self._path(task_id)
        if path.is_file():
            path.unlink()

    def _path(self, task_id: str) -> Path:
        return self.runs_dir / f"{task_id}.lock"

    def _host(self) -> str:
        return socket.gethostname()

    def _write(
        self, task_id: str, *, pid: int, host: str, started_at: str, command: str = ""
    ) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        record = {"pid": pid, "host": host, "started_at": started_at, "command": command}
        self._path(task_id).write_text(json.dumps(record), encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # owned by someone else, but the OS knows it is there
    return True
