"""`Scratch` over the product's disk (GH-391, ADR-0069): one directory per check or role
attempt under `.agentic/tmp/<task>/`, with an owner record next to it.

The record is written whole to a hidden temp name and renamed into place before the
directory is created, and removed only after the directory is gone: a crash at any point
leaves either nothing or a record that still names an owner, never a directory nobody
claims. Whoever sweeps what a crash left reads the records (`liveness.is_alive`)."""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ...domain import scratch
from ...domain.scratch import (
    OwnerEntry,
    OwnerKind,
    OwnerRecord,
    RecordFact,
    ScratchListing,
    SweepDecision,
)
from .liveness import current_boot_id, is_alive


def _utc_now() -> datetime:
    return datetime.fromtimestamp(time.time(), UTC)


def _children(directory: Path) -> list[Path]:
    try:
        return list(directory.iterdir())
    except OSError:
        return []


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


class FilesystemScratch:
    def __init__(
        self,
        product_dir: Path,
        boot_id: Callable[[], str | None] = current_boot_id,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        # Absolute: the directory is exported as `TMPDIR` to processes that run elsewhere.
        self._root = product_dir.resolve()
        self._boot_id = boot_id
        self._clock = clock

    @contextmanager
    def open(self, task_key: str | None, kind: OwnerKind, token: str, slug: str) -> Iterator[Path]:
        owner = scratch.owner(kind, token)
        directory = self._root / scratch.path_for(task_key, owner)
        record = self._root / scratch.record_path_for(task_key, owner)
        self._write_record(
            record, OwnerRecord(os.getpid(), self._boot_id(), slug, kind, self._clock())
        )
        try:
            directory.mkdir()
            yield directory
        finally:
            shutil.rmtree(directory, ignore_errors=True)
            with contextlib.suppress(FileNotFoundError):
                record.unlink()

    def ensure_kept(self, task_key: str | None) -> Path:
        kept = self._root / scratch.kept_path_for(task_key)
        kept.mkdir(parents=True, exist_ok=True)
        return kept

    def listing(self) -> ScratchListing:
        base = self._root / scratch.SCRATCH_DIR
        tasks: list[str] = []
        owners: list[OwnerEntry] = []
        for task_dir in sorted(_children(base)):
            if not task_dir.is_dir():
                continue
            tasks.append(task_dir.name)
            owners.extend(self._owners_in(task_dir))
        return ScratchListing(tuple(tasks), tuple(owners))

    def kept_folders(self) -> list[tuple[str, datetime]]:
        folders = []
        for folder in sorted(_children(self._root / scratch.KEPT_DIR)):
            mtime = _mtime(folder)
            if folder.is_dir() and mtime is not None:
                folders.append((folder.name, mtime))
        return folders

    def sweep(self, decision: SweepDecision) -> None:
        """Best effort: a path that cannot be removed is left for the next round."""
        for task in decision.tasks:
            shutil.rmtree(self._root / scratch.task_path_for(task), ignore_errors=True)
        for removal in decision.owners:
            task_dir = self._root / scratch.task_path_for(removal.task)
            if removal.directory:
                shutil.rmtree(task_dir / removal.name, ignore_errors=True)
            if removal.record:
                # After the directory, never before: a record is what says whose it was.
                with contextlib.suppress(OSError):
                    (task_dir / f"{removal.name}.json").unlink()
        for task in decision.kept:
            shutil.rmtree(self._root / scratch.KEPT_DIR / task, ignore_errors=True)

    def _owners_in(self, task_dir: Path) -> list[OwnerEntry]:
        directories: dict[str, datetime] = {}
        records: dict[str, RecordFact] = {}
        for child in _children(task_dir):
            if child.name.startswith("."):
                continue  # the temp name a record is written under
            mtime = _mtime(child)
            if mtime is None:
                continue
            if child.is_dir():
                directories[child.name] = mtime
            elif child.suffix == ".json":
                records[child.stem] = RecordFact(mtime, self._alive(child))
        return [
            OwnerEntry(task_dir.name, name, directories.get(name), records.get(name))
            for name in sorted(directories.keys() | records.keys())
        ]

    def _alive(self, record: Path) -> bool | None:
        """Whether the owner a record names still runs; `None` when it cannot be read."""
        try:
            parsed = OwnerRecord.from_json(record.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        if parsed is None:
            return None
        return is_alive(parsed.pid, parsed.boot_id, self._boot_id())

    @staticmethod
    def _write_record(record: Path, content: OwnerRecord) -> None:
        record.parent.mkdir(parents=True, exist_ok=True)
        temp = record.with_name(f".{record.name}.tmp")
        try:
            temp.write_text(content.to_json(), encoding="utf-8")
            os.replace(temp, record)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()
