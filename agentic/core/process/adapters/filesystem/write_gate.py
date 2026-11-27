"""`FilesystemWriteGate`: the adapter of the `WriteGate` port over one file, so the writes of
every process of the machine that reach GitHub are at least `_SPACING_S` apart (GH-1083,
ADR-0107). The file holds one number, the slot the last write reserved, as epoch seconds.

`take` locks the file with `flock`, reads the last slot, computes this write's slot with
`write_slot` and writes it back, all under the lock, then unlocks and sleeps outside it: the
lock is never held while sleeping or while `gh` runs. A write whose wait is over the cap, or
over what its caller can still spend, is refused and writes nothing.

A granted turn writes its slot even when the wait is zero. That departs on purpose from "only
when the caller will wait": read that way, two writers that both find a stale file would both
get a zero wait and land less than a second apart. A stopped turn keeps its reservation: the
slot stays in the file and the next writer comes after it.

`stop` is looked at only around a sleep: before each slice and once after the last one. A turn
whose wait is zero is granted whatever the flag says, so the closing writes of a stopped run
are not refused.

The record is fixed-width (`_RECORD_LEN` bytes) and written with one `os.pwrite` at offset 0,
so a reader never sees the file emptied by a truncate before the write. Replacing the file
with `os.replace` would be no better: the lock lives on the inode, and a new inode would let
two writers each hold a lock on a different file. The file is cut only when what was read was
not exactly one record (empty, torn or garbage).

Every writer must resolve the same cache root (`Configuration.github_dir`, from
`$XDG_CACHE_HOME` or `~/.cache`); otherwise each root has its own file and the spacing silently
splits across them.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Callable
from pathlib import Path

from ...application.ports import StopFlag
from ...domain.github_access import Turn, last_slot_of, write_slot

# The file name under `Configuration.github_dir` that part 3 builds the gate on.
WRITE_TURN_FILE = "write-turn"

# Declared, not measured: the longest sleep before the stop flag is looked at again.
_SLICE_S = 1.0
# Declared: the least time between two writes' slots.
_SPACING_S = 1.0
# `f"{slot:24.6f}\n"`: 24 characters and a newline.
_RECORD_LEN = 25
# A slot further ahead than this is a clock that stepped back, and reads as no slot. The same
# number as `GH_RATE_WAIT_MAX_S` in `adapters/github/run.py`, restated here because an adapter
# does not import another; `test_write_gate.py` asserts that they are equal.
_HORIZON_S = 900.0


class FilesystemWriteGate:
    def __init__(
        self,
        path: Path,
        cap_s: float,
        stop: StopFlag,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path
        self._cap_s = cap_s
        self._stop = stop
        self._now = now
        self._sleep = sleep

    @property
    def cap_s(self) -> float:
        return self._cap_s

    def take(self, remaining_s: float) -> Turn:
        """The turn of one write: `refused` when its wait would pass `min(cap_s, remaining_s)`,
        `stopped` when the stop flag ended its wait, else `granted`; `waited_s` is the time
        actually slept."""
        slot, now = self._reserve(remaining_s)
        if slot is None:
            return Turn("refused", 0.0)
        return self._wait_until(slot - now)

    def _reserve(self, remaining_s: float) -> tuple[float | None, float]:
        """Under the lock: read the last slot and write this one. `None` when refused."""
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._path.parent.chmod(0o700)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                data = os.pread(fd, os.fstat(fd).st_size, 0)
                now = self._now()
                last = last_slot_of(data.decode("utf-8", errors="replace"), now, _HORIZON_S)
                slot = write_slot(last, now, _SPACING_S, min(self._cap_s, max(remaining_s, 0.0)))
                if slot is None:
                    return None, now
                os.pwrite(fd, f"{slot:24.6f}\n".encode("ascii"), 0)
                if len(data) != _RECORD_LEN:
                    os.ftruncate(fd, _RECORD_LEN)
                return slot, now
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _wait_until(self, wait_s: float) -> Turn:
        """Sleep `wait_s` outside the lock, in slices, with the stop flag looked at before each
        slice and once after the last."""
        if wait_s <= 0:
            return Turn("granted", 0.0)
        waited = 0.0
        while waited < wait_s:
            if self._stop.is_set():
                return Turn("stopped", waited)
            step = min(_SLICE_S, wait_s - waited)
            self._sleep(step)
            waited += step
        if self._stop.is_set():
            return Turn("stopped", waited)
        return Turn("granted", waited)
