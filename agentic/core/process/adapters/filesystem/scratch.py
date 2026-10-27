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
from ...domain.scratch import OwnerKind, OwnerRecord
from .liveness import current_boot_id


def _utc_now() -> datetime:
    return datetime.fromtimestamp(time.time(), UTC)


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
