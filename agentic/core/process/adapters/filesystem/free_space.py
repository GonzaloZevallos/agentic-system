"""`FreeSpace` over `os.statvfs` (GH-391): what is left on the volume a path lives on."""

from __future__ import annotations

import os
from pathlib import Path

from ...domain.scratch import SpaceReading


class StatvfsFreeSpace:
    def read(self, path: Path) -> SpaceReading:
        """Bytes an unprivileged user may still write (`f_bavail`, not `f_bfree`: the
        reserved blocks are not ours) and inodes available to one. A path not made yet is
        read through its nearest existing parent, which is on the same volume."""
        existing = path
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        stat = os.statvfs(existing)
        return SpaceReading(free_bytes=stat.f_bavail * stat.f_frsize, free_inodes=stat.f_favail)
