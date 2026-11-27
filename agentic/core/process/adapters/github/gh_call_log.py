"""The in-process `GhCallLog` (GH-990): counts under one lock. No process starts here."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from types import MappingProxyType

from ...domain.gh_calls import GH_CALL_KINDS, GhCallKind


class InProcessGhCallLog:
    """Counts `gh` launches by kind for this process. `record` and `drain` share one lock, so
    a count recorded while a drain runs lands in this drain or the next, never in neither."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[GhCallKind, int] = dict.fromkeys(GH_CALL_KINDS, 0)

    def record(self, kind: GhCallKind) -> None:
        with self._lock:
            self._counts[kind] += 1

    def drain(self) -> Mapping[GhCallKind, int]:
        """The counts since the last drain, every kind present, read-only; resets them."""
        with self._lock:
            taken = dict(self._counts)
            self._counts = dict.fromkeys(GH_CALL_KINDS, 0)
        return MappingProxyType(taken)


__all__ = ["InProcessGhCallLog"]
