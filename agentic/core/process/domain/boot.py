"""Which boot of the machine a lock or registry record belongs to (glossary: Arranque).
Pure: the caller reads `/proc` and hands both ids in, so the verdict does no I/O."""

from __future__ import annotations

from typing import Literal

BootVerdict = Literal["other_boot", "same_boot", "unknown"]


def boot_verdict(recorded: str | None, current: str | None) -> BootVerdict:
    """`other_boot` when both ids are known and differ: whatever the pid says, the
    process that wrote the record did not survive the reboot. A missing id on either
    side (an old record, a machine with no `/proc`) is `unknown`: nothing is claimed."""
    if recorded is None or current is None:
        return "unknown"
    return "same_boot" if recorded == current else "other_boot"
