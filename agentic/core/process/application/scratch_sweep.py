"""What a poller round does about scratch (GH-391, ADR-0069): sweep what is dead or closed,
then read how much room the two volumes have and say when they are under the floor.

The decisions are pure (`scratch.sweep_decision`, `rules.space_holds`); this module gathers
the facts from the ports and carries the sweep out through `Scratch.sweep`. Neither step may
end a round: a listing or a volume that cannot be read is a line, and the round goes on."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.scratch import INODE_FLOOR, KeptFolder, SpaceReading, sweep_decision
from .ports import Ports

TMP_ROOT = Path("/tmp")
_MB = 1024 * 1024


# The statuses of a task that ended with no delivery: `rules.finished` writes one of them with
# `finished_at`, the way a merged or closed task gets `closed_at`.
_ENDED_WITHOUT_DELIVERY = frozenset({"discarded", "split", "tracker_only"})


def _stamp(value: object, fallback: datetime) -> datetime:
    """`value` as an aware time; `fallback` (the folder's mtime) when it cannot be read."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _closed_at(
    ports: Ports, task: str, fallback: datetime, unreadable: datetime | None
) -> datetime | None:
    """When `task` closed, or `None` when it has not (or has no document). A task closes with
    `closed_at` (merged, closed) or, ended without a delivery (discarded, split), with a
    `status` and `finished_at`; a stamp that cannot be read counts as `fallback`. A document
    that cannot be read at all answers `unreadable`: the folder's mtime for a kept folder's
    clock, `None` for the closed set, since a task nobody can read is not known closed."""
    try:
        raw = ports.documents.load(task)
    except (OSError, ValueError):
        return unreadable
    raw = raw or {}
    if raw.get("closed_at"):
        return _stamp(raw["closed_at"], fallback)
    if raw.get("status") in _ENDED_WITHOUT_DELIVERY and raw.get("finished_at"):
        return _stamp(raw["finished_at"], fallback)
    return None


def sweep(ports: Ports, now: datetime) -> list[str]:
    """Removes the scratch of closed tasks, of owners that are no longer alive and of orphans,
    and the kept folders past their retention. One line when something was removed, one when
    the disk could not be read; nothing otherwise. Only documents of tasks that have scratch
    or a kept folder are read."""
    if ports.scratch is None:
        return []
    try:
        listing = ports.scratch.listing()
        kept_entries = ports.scratch.kept_folders()
        known = {*listing.tasks, *(task for task, _ in kept_entries)}
        closed = {task for task in sorted(known) if _closed_at(ports, task, now, None) is not None}
        kept = [
            KeptFolder(task, _closed_at(ports, task, mtime, mtime)) for task, mtime in kept_entries
        ]
        decision = sweep_decision(listing, closed, kept, now)
        if not decision:
            return []
        ports.scratch.sweep(decision)
    except OSError as exc:
        return [f"agentic: poll: scratch sweep failed: {exc}"]
    return [
        "agentic: poll: scratch swept: "
        f"{len(decision.tasks)} closed task(s), {len(decision.owners)} owner(s), "
        f"{len(decision.kept)} kept folder(s)"
    ]


def _describe(name: str, reading: SpaceReading, floor_bytes: int) -> str:
    reasons = []
    if reading.free_bytes < floor_bytes:
        reasons.append(f"{reading.free_bytes // _MB} MB < floor {floor_bytes // _MB} MB")
    if reading.free_inodes < INODE_FLOOR:
        reasons.append(f"{reading.free_inodes} inodes < floor {INODE_FLOOR}")
    return (
        f"{name} {', '.join(reasons)}" if reasons else f"{name} {reading.free_bytes // _MB} MB ok"
    )


def hold_line(held: tuple[str, ...], readings: Mapping[str, SpaceReading], floor: int) -> str:
    """`held: /tmp 1400 MB < floor 2048 MB (scratch 380000 MB ok)`: each volume under the
    floor with its figures, then the others."""
    under = ", ".join(_describe(name, readings[name], floor) for name in held)
    fine = ", ".join(_describe(name, r, floor) for name, r in readings.items() if name not in held)
    return f"agentic: poll: held: {under}" + (f" ({fine})" if fine else "")


def read_holds(cfg: Configuration, ports: Ports) -> tuple[tuple[str, ...], list[str]]:
    """The volumes under the floor, and the line that says so. A volume that cannot be read
    holds nothing: the round runs as it did before the floor existed, and says why."""
    if ports.free_space is None:
        return (), []
    try:
        readings = {
            rules.SCRATCH_VOLUME: ports.free_space.read(cfg.product_dir / ".agentic"),
            rules.TMP_VOLUME: ports.free_space.read(TMP_ROOT),
        }
    except OSError as exc:
        return (), [f"agentic: poll: free space unreadable: {exc}"]
    floor = cfg.scratch_floor_bytes
    held = rules.space_holds(readings[rules.SCRATCH_VOLUME], readings[rules.TMP_VOLUME], floor)
    return held, [hold_line(held, readings, floor)] if held else []
