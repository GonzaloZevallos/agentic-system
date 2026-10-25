"""`TaskDocuments` over `.agentic/tasks/` of the product repo."""

from __future__ import annotations

import copy
import os
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ...domain import document
from ...domain.task import TaskState
from .. import plugins

# The parse cache's byte budget, in bytes of *file* (`st_size`) admitted: a document is
# cached only while `admitted + size` fits, and nothing is ever evicted (ADR-0063).
BUDGET = 64 * 1024 * 1024

# A file whose ctime is younger than this is "racy": a write in the same filesystem
# timestamp tick as our read would leave the key unchanged, so it is never cached.
RACY_WINDOW_NS = 2_000_000_000

_Key = tuple[int, int, int, int]


@dataclass(frozen=True)
class _Entry:
    key: _Key
    size: int
    data: dict[str, Any]


def _key(info: os.stat_result) -> _Key:
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class FilesystemTaskDocuments:
    def __init__(
        self,
        product_dir: Path,
        system_version: Callable[[], str] | None = None,
        system_baseline: Callable[[], str] | None = None,
        *,
        subdir: Path = document.TASKS_SUBDIR,
        clock: Callable[[], int] = time.time_ns,
    ):
        self.product_dir = product_dir
        # The parse cache (ADR-0063): validated on every `load` by `fstat` of the open
        # file. `clock` (ns) only decides whether a file is old enough to trust; tests
        # age their files through it, `composition` never passes it.
        self.clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._admitted = 0
        # `.agentic/tasks/` by default, so every existing construction site is
        # byte-identical; the stack review's own corpus is the one caller that passes
        # `.agentic/stack-reviews/` (ADR-0034). `close --all`, `eval score` and
        # `_bucket_documents` reach the corpus through `ids()`, never through this
        # field, so none of them can see a document under another `subdir`.
        self.subdir = subdir
        # The joinable version label's producer (`telemetry.system_version`, wired by
        # `composition.default_ports`): injected rather than computed here, which is
        # what keeps one `git rev-parse` in the system. `None` -- a bare-constructed
        # adapter, the graph regression's own included -- writes today's document plus
        # nothing (`document.render`'s own docstring).
        self.system_version = system_version
        # `telemetry.system_baseline`'s own producer, the same injection for the same
        # reason (ADR-0030): `None` writes today's document plus nothing, same as an
        # absent `system_version`.
        self.system_baseline = system_baseline

    def path_for(self, task_id: str) -> Path:
        return self.product_dir / document.relative_path(task_id, subdir=self.subdir)

    def ids(self) -> list[str]:
        tasks_dir = self.product_dir / self.subdir
        listed = sorted(path.stem for path in tasks_dir.glob("*.md")) if tasks_dir.is_dir() else []
        self._prune(set(listed))
        return listed

    def ids_touched_since(self, since: datetime) -> list[str]:
        """Ids of the documents whose file `mtime` is at or after `since`, sorted. Only
        `stat` runs here -- nothing is parsed -- and only inside the documents subdir."""
        tasks_dir = self.product_dir / self.subdir
        if not tasks_dir.is_dir():
            return []
        cutoff = since.timestamp()
        touched = []
        for path in tasks_dir.glob("*.md"):
            try:
                if path.stat().st_mtime >= cutoff:
                    touched.append(path.stem)
            except OSError:
                continue
        return sorted(touched)

    def save(self, state: TaskState) -> Path:
        path = self.path_for(state["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        version = self.system_version() if self.system_version is not None else None
        baseline = self.system_baseline() if self.system_baseline is not None else None
        text = document.render(
            state,
            system_commit=system_commit(),
            plugins=plugins.version(),
            system_version=version,
            system_baseline=baseline,
        )
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, task_id: str) -> dict | None:
        path = self.path_for(task_id)
        try:
            fd = os.open(path, os.O_RDONLY)
        except (FileNotFoundError, NotADirectoryError):
            self._drop(task_id)
            return None
        try:
            first = os.fstat(fd)
            if not stat.S_ISREG(first.st_mode):
                self._drop(task_id)
                return None
            key = _key(first)
            with self._lock:
                seen = self._entries.get(task_id)
            if seen is not None and seen.key == key:
                return copy.deepcopy(seen.data)
            with open(fd, encoding="utf-8", closefd=False) as handle:
                text = handle.read()
            try:
                second: os.stat_result | None = os.fstat(fd)
            except OSError:
                second = None
        finally:
            os.close(fd)
        parsed = document.parse(text, path)
        if second is not None and _key(second) == key and self._settled(first):
            self._store(task_id, seen, _Entry(key, first.st_size, copy.deepcopy(parsed)))
        return parsed

    def _settled(self, info: os.stat_result) -> bool:
        """Old enough to trust the key: a ctime in the future or inside the racy window
        is never cached."""
        return self.clock() - info.st_ctime_ns >= RACY_WINDOW_NS

    def _store(self, task_id: str, seen: _Entry | None, entry: _Entry) -> None:
        with self._lock:
            current = self._entries.get(task_id)
            if current is not None and current.key not in (entry.key, seen.key if seen else None):
                return  # another thread stored a different, fresher key meanwhile
            freed = current.size if current is not None else 0
            if self._admitted - freed + entry.size > BUDGET:
                if current is not None:
                    del self._entries[task_id]
                    self._admitted -= freed
                return
            self._entries[task_id] = entry
            self._admitted += entry.size - freed

    def _drop(self, task_id: str) -> None:
        with self._lock:
            gone = self._entries.pop(task_id, None)
            if gone is not None:
                self._admitted -= gone.size

    def _prune(self, listed: set[str]) -> None:
        """Forget the entries of ids `ids()` no longer lists. The drops are worked out
        outside the lock from a snapshot, and an entry whose key moved since is kept."""
        with self._lock:
            snapshot = {task_id: entry.key for task_id, entry in self._entries.items()}
        stale = {task_id: key for task_id, key in snapshot.items() if task_id not in listed}
        if not stale:
            return
        with self._lock:
            for task_id, key in stale.items():
                entry = self._entries.get(task_id)
                if entry is not None and entry.key == key:
                    del self._entries[task_id]
                    self._admitted -= entry.size

    def record_closure(self, task_id: str, values: dict[str, str]) -> Path:
        """Line-level edit of the closure keys into the document already on disk
        (`document.record_closure`'s own docstring): never through `save`, which would
        re-render `system_commit` from today's sha and `plugins` from today's submodule
        and falsify the provenance of a finished Corrida (ADR-0001)."""
        path = self.path_for(task_id)
        raw = path.read_text(encoding="utf-8")
        path.write_text(document.record_closure(raw, values), encoding="utf-8")
        return path

    def write_into(self, task_id: str, root: Path) -> Path | None:
        source = self.path_for(task_id)
        if not source.is_file():
            return None
        destination = root / document.relative_path(task_id, subdir=self.subdir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return destination


def system_commit() -> str | None:
    """Short commit of this repo, for the prompt version record (ADR-0005). Best effort."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None
