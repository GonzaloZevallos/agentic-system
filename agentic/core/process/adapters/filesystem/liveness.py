"""Is the process a lock or registry record names still there. The one reader of the
kernel's boot id and the one pid probe: `FilesystemRunLock` and `FilesystemRunRegistry`
both ask here, so the two cannot disagree (GH-176)."""

from __future__ import annotations

import os
import signal
import socket
from collections.abc import Callable
from pathlib import Path

from ...application.ports import LockRecord
from ...domain.boot import boot_verdict

BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


def current_boot_id() -> str | None:
    """The kernel's id for this boot, or `None` where it cannot be read (no `/proc`)."""
    try:
        text = BOOT_ID_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def is_alive(pid: int, recorded_boot: str | None, current_boot: str | None) -> bool:
    """A record from another boot is dead whatever `os.kill` says: the pid may have been
    reused by an unrelated process. Otherwise the pid itself is probed."""
    if boot_verdict(recorded_boot, current_boot) == "other_boot":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # owned by someone else, but the OS knows it is there
    return True


def starttime_of(pid: int, proc: Path = Path("/proc")) -> str | None:
    """When `pid` started, in the kernel's clock ticks since boot (`/proc/<pid>/stat`,
    field 22): with the boot id it names one process for good, a reused pid included.
    `None` where it cannot be read (the process is gone, or no `/proc`)."""
    try:
        text = (proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # The command (field 2) is in parentheses and may hold anything: split after the last.
    fields = text.rpartition(")")[2].split()
    # `fields` starts at field 3 (state), so field 22 is the 20th.
    return fields[19] if len(fields) > 19 else None


class OsRunProcess:
    """`RunProcess` over the kernel: a pidfd on the pid the record names, checked against
    the record's start time while the pidfd is held, so a signal reaches that process or
    none (a pid reused between the check and the signal cannot receive it)."""

    def __init__(
        self,
        boot_id: Callable[[], str | None] = current_boot_id,
        host: Callable[[], str] = socket.gethostname,
    ) -> None:
        self._boot_id = boot_id
        self._host = host

    def holds(self, record: LockRecord) -> bool:
        if record.host != self._host():
            return False  # a pid on another host is never probed
        if not is_alive(record.pid, record.boot_id, self._boot_id()):
            return False
        return record.starttime is None or starttime_of(record.pid) == record.starttime

    def terminate(self, record: LockRecord) -> None:
        self._send(record, signal.SIGTERM)

    def kill(self, record: LockRecord) -> None:
        self._send(record, signal.SIGKILL)

    def _send(self, record: LockRecord, sig: signal.Signals) -> None:
        if not self.holds(record):
            return
        try:
            fd = os.pidfd_open(record.pid)
        except ProcessLookupError:
            return
        try:
            # Re-checked with the pidfd held: from here the pid cannot name anyone else.
            if record.starttime is not None and starttime_of(record.pid) != record.starttime:
                return
            signal.pidfd_send_signal(fd, sig)
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)
