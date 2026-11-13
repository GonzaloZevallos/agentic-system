"""`Stage0Users` over a proc directory (GH-632, part 2b). Only entries of this uid are read; a
stage is in use when a process's `cwd`, `exe` or any `cmdline` element lies under it. The
command line is compared as a path and never printed or stored."""

from __future__ import annotations

import os
from pathlib import Path

from ...domain.stage0_rotation import Unreadable


class ProcStage0Users:
    def __init__(self, root: Path, proc: Path = Path("/proc")) -> None:
        self._root = root.resolve()
        self._proc = proc

    def users(self) -> frozenset[str] | Unreadable:
        uid = os.getuid()
        used: set[str] = set()
        try:
            entries = sorted(e for e in os.listdir(self._proc) if e.isascii() and e.isdigit())
        except OSError:
            return Unreadable()
        for entry in entries:
            directory = self._proc / entry
            try:
                if os.stat(directory).st_uid != uid:
                    continue
                paths = self._paths(directory)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                return Unreadable()
            used.update(stage for path in paths if (stage := self._stage(path)) is not None)
        return frozenset(used)

    @staticmethod
    def _paths(directory: Path) -> list[str]:
        """The paths this process holds; a source that vanished is left out."""
        paths: list[str] = []
        cwd: str | None = None
        try:
            cwd = os.readlink(directory / "cwd")
            paths.append(cwd)
        except (FileNotFoundError, ProcessLookupError):
            pass
        try:
            paths.append(os.readlink(directory / "exe"))
        except (FileNotFoundError, ProcessLookupError):
            pass
        try:
            argv = (directory / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            return paths
        for raw in argv:
            element = os.fsdecode(raw)
            if "/" not in element:
                continue
            if os.path.isabs(element):
                paths.append(element)
            elif cwd is not None:
                paths.append(os.path.join(cwd, element))
        return paths

    def _stage(self, path: str) -> str | None:
        parts = Path(os.path.realpath(path)).parts
        base = self._root.parts
        if len(parts) > len(base) and parts[: len(base)] == base:
            return parts[len(base)]
        return None
