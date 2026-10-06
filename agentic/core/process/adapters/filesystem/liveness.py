"""Is the process a lock or registry record names still there. The one reader of the
kernel's boot id and the one pid probe: `FilesystemRunLock` and `FilesystemRunRegistry`
both ask here, so the two cannot disagree (GH-176)."""

from __future__ import annotations

import os
from pathlib import Path

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
