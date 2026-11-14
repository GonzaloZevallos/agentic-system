"""`InstalledProducts` over the user's `agentic-poll*.service` units (GH-726): the product dirs
the stage0 smoke runs `poll --dry-run --once` for. Reads only the unit files in one directory;
the grammar of a unit is `domain/poller_units.py`'s."""

from __future__ import annotations

from pathlib import Path

from ...domain.poller_units import product_dir
from ...domain.stage0_rotation import Unreadable


class SystemdUnitInstalledProducts:
    def __init__(self, unit_dir: Path) -> None:
        self._unit_dir = unit_dir

    def read(self) -> tuple[Path, ...] | Unreadable:
        """The product dirs of the units, each once, sorted. A missing directory has no
        units (`()`); a directory that cannot be listed, a unit that cannot be read as text or
        a unit whose product dir `product_dir` cannot read is `Unreadable`."""
        try:
            units = sorted(p for p in self._unit_dir.iterdir() if p.match("agentic-poll*.service"))
        except FileNotFoundError:
            return ()
        except OSError:
            return Unreadable()
        dirs: set[Path] = set()
        for path in units:
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                return Unreadable()
            found = product_dir(text)
            if isinstance(found, Unreadable):
                return found
            dirs.add(found)
        return tuple(sorted(dirs))
