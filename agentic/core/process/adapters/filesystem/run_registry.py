"""`FilesystemRunRegistry`, over `Configuration.run_registry_dir`: how many `agentic`
invocations are alive on this machine right now, across every product (GH-137,
`AGENTIC_MAX_CONCURRENT_RUNS` -- the account's own 429 is not scoped to one product),
and the poller's own schedule for a launch attempt that has not registered a run yet.

Three kinds of file live under the same directory, all plain JSON written whole: one
entry per live process, named by its own pid (`<pid>.json`, mirroring
`FilesystemRunLock`'s one-file-per-task shape), one per launch attempt that has
not cleared yet (`launches/<product>-<issue>.json`), and, only when `AGENTIC_SPEND_PACING`
is on, one spend ledger per task-document directory (`spend/<key>.json`, GH-177) -- the
only way a poller learns what the other products spent."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import socket
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from ...application.ports import LaunchAttempt, RunRegistration
from ...domain import landing, rules
from .liveness import current_boot_id, is_alive

_LOCK_NAME = ".lock"
_LAUNCHES_DIR = "launches"
_SPEND_DIR = "spend"
_WAITING_DIR = "waiting"
_FLAKES_DIR = "flakes"
# How often a flake lock that is held is tried again, in seconds.
_FLAKE_LOCK_POLL_S = 0.25


class FilesystemRunRegistry:
    def __init__(
        self,
        dir: Path,
        boot_id: Callable[[], str | None] = current_boot_id,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.dir = dir
        self._boot_id = boot_id
        # The flake lock's wait is measured with these, so a test passes a clock that only
        # moves when it sleeps and never waits for real; `composition` never passes them.
        self._clock = clock
        self._sleep = sleep

    # -- live runs ----------------------------------------------------------------

    def register(self, product: str, task_id: str, kind: rules.RunKind = "role") -> None:
        self._write(os.getpid(), _host(), product, task_id, self._boot_id(), kind)

    def unregister(self) -> None:
        self._path(os.getpid()).unlink(missing_ok=True)

    def live(self) -> list[RunRegistration]:
        if not self.dir.is_dir():
            return []
        here = _host()
        current = self._boot_id()
        entries = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pid, host = int(data["pid"]), str(data["host"])
            except (OSError, ValueError, KeyError):
                continue
            # Never probed: a pid recorded on another host is a coin flip, the same
            # rule `FilesystemRunLock` already enforces. An entry from another boot is
            # dead whatever the pid says (`liveness.is_alive`).
            if host == here and is_alive(pid, data.get("boot_id"), current):
                entries.append(
                    RunRegistration(
                        pid, host, data["product"], data["task_id"], _kind_of(data.get("kind"))
                    )
                )
        return entries

    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / _LOCK_NAME, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def flake_lock(self, product: str) -> Iterator[bool]:
        """`flakes/<product>.lock`, `flock(LOCK_EX | LOCK_NB)` polled for at most
        `landing.FLAKE_LOCK_WAIT_S`, released when the block ends. `False` -- never an
        exception -- when it was not taken in time or the file cannot be opened. Unlike
        `exclusive` this never blocks without a bound: a merge waits on it."""
        handle = self._take_flake_lock(product)
        try:
            yield handle is not None
        finally:
            if handle is not None:
                handle.close()  # closing the descriptor drops the flock

    def _take_flake_lock(self, product: str) -> IO[str] | None:
        # The product name is a file name, never a path: anything outside this alphabet is `_`.
        name = re.sub(r"[^A-Za-z0-9._-]", "_", product).lstrip(".") or "_"
        try:
            (self.dir / _FLAKES_DIR).mkdir(parents=True, exist_ok=True)
            handle = open(self.dir / _FLAKES_DIR / f"{name}.lock", "w")
        except OSError:
            return None
        deadline = self._clock() + landing.FLAKE_LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except OSError:  # held by another (BlockingIOError), or no locking here at all
                if self._clock() >= deadline:
                    handle.close()
                    return None
                self._sleep(_FLAKE_LOCK_POLL_S)

    def _write(
        self,
        pid: int,
        host: str,
        product: str,
        task_id: str,
        boot_id: str | None = None,
        kind: rules.RunKind = "role",
    ) -> None:
        """The record whole, once: a temp file in the same directory then `os.replace`,
        so `live()` never reads half a record."""
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {
            "pid": pid,
            "host": host,
            "product": product,
            "task_id": task_id,
            "boot_id": boot_id,
            "kind": kind,
        }
        _replace_json(self._path(pid), record)

    def _path(self, pid: int) -> Path:
        return self.dir / f"{pid}.json"

    # -- launch schedule ------------------------------------------------------------

    def launch_attempts(self, product: str, issue: int) -> LaunchAttempt | None:
        path = self._launch_path(product, issue)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return LaunchAttempt(
            datetime.fromisoformat(data["since"]), int(data["prior"]), data.get("arm")
        )

    def record_launch(
        self, product: str, issue: int, when: datetime, arm: str | None = None
    ) -> None:
        path = self._launch_path(product, issue)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.launch_attempts(product, issue)
        prior = existing.prior if existing is not None else 0
        kept = arm if arm is not None else (existing.arm if existing is not None else None)
        record: dict[str, str | int] = {"since": when.isoformat(), "prior": prior + 1}
        if kept is not None:
            record["arm"] = kept
        path.write_text(json.dumps(record), encoding="utf-8")

    def clear_launch(self, product: str, issue: int) -> None:
        self._launch_path(product, issue).unlink(missing_ok=True)

    def _launch_path(self, product: str, issue: int) -> Path:
        return self.dir / _LAUNCHES_DIR / f"{product}-{issue}.json"

    # -- spend ledger (GH-177) --------------------------------------------------------

    def publish_spend(
        self, root: Path, product: str, runs: Sequence[rules.SpendRun], now: datetime
    ) -> list[str]:
        """Merges `runs` into this poller's own file, keyed by the resolved `root` (two
        products may share a name, two checkouts may share a product). The caller holds
        `exclusive()`; the write is a temp file in the same directory then `os.replace`,
        so a reader never sees half a file. `spend/` is created only here."""
        resolved = root.resolve()
        path = self._spend_path(resolved)
        published, notes = self._read_spend_file(path, now - rules.TRAILING_SPEND_WINDOW)
        merged = rules.merged_spend(published, runs, now)
        record = {
            "product": product,
            "root": str(resolved),
            "written_at": now.isoformat(),
            "runs": [
                [r.started_at.isoformat(), r.ended_at.isoformat(), r.cost_usd] for r in merged
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace_json(path, record)
        return notes

    def spend(self, since: datetime) -> tuple[list[rules.SpendRun], list[str]]:
        """Every spend file's entries that ended at or after `since`, and a note per file
        or entry skipped. Never raises on what is in a file."""
        directory = self.dir / _SPEND_DIR
        if not directory.is_dir():
            return [], []
        runs: list[rules.SpendRun] = []
        notes: list[str] = []
        for path in sorted(directory.glob("*.json")):
            found, skipped = self._read_spend_file(path, since)
            runs.extend(found)
            notes.extend(skipped)
        return runs, notes

    def _spend_path(self, root: Path) -> Path:
        key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
        return self.dir / _SPEND_DIR / f"{key}.json"

    # -- land waits (GH-362) ------------------------------------------------------------

    def publish_land_wait(
        self,
        root: Path,
        product: str,
        issues: Sequence[int],
        since: datetime | None,
        now: datetime,
    ) -> None:
        resolved = root.resolve()
        path = self._land_wait_path(resolved)
        if not issues:
            path.unlink(missing_ok=True)
            return
        record = {
            "product": product,
            "root": str(resolved),
            "written_at": now.isoformat(),
            "boot_id": self._boot_id(),
            "since": (now if since is None else since).isoformat(),
            "issues": sorted(issues),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace_json(path, record)

    def land_waits(self, now: datetime) -> tuple[list[rules.LandWait], list[str]]:
        directory = self.dir / _WAITING_DIR
        if not directory.is_dir():
            return [], []
        current = self._boot_id()
        waits: list[rules.LandWait] = []
        notes: list[str] = []
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                written_at = datetime.fromisoformat(data["written_at"])
                since = datetime.fromisoformat(data["since"])
                issues = tuple(int(n) for n in data["issues"])
                product, root = str(data["product"]), str(data["root"])
                boot = data.get("boot_id")
                if written_at.tzinfo is None or since.tzinfo is None:
                    raise ValueError("naive instant")
            except (OSError, ValueError, KeyError, TypeError):
                notes.append(f"agentic: poll: land wait file {path.name} unreadable, skipped")
                continue
            if boot is not None and current is not None and boot != current:
                continue
            if now - written_at > rules.LAND_WAIT_TTL:
                continue
            waits.append(rules.LandWait(product, rules.root_key(root), written_at, since, issues))
        return waits, notes

    def _land_wait_path(self, root: Path) -> Path:
        return self.dir / _WAITING_DIR / f"{rules.root_key(str(root))}.json"

    @staticmethod
    def _read_spend_file(path: Path, since: datetime) -> tuple[list[rules.SpendRun], list[str]]:
        """The entries of one spend file that ended at or after `since`; an unparseable
        file is `[]` plus a note, and a naive or malformed entry is skipped with a note."""
        if not path.is_file():
            return [], []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data["runs"]
            if not isinstance(entries, list):
                raise TypeError("runs is not a list")
        except (OSError, ValueError, KeyError, TypeError):
            return [], [f"agentic: poll: spend file {path.name} unreadable, skipped"]
        runs: list[rules.SpendRun] = []
        bad = 0
        for entry in entries:
            parsed = _spend_entry(entry)
            if parsed is None:
                bad += 1
            elif parsed.ended_at >= since:
                runs.append(parsed)
        notes = (
            [f"agentic: poll: spend file {path.name}: {bad} malformed entries skipped"]
            if bad
            else []
        )
        return runs, notes


def _replace_json(path: Path, record: dict[str, object]) -> None:
    """`record` as `path`, whole: a temp file beside it, then `os.replace`. A temp file
    never outlives a failed write. The temp name ends in `.tmp`, never `.json`, so
    `live()`'s glob cannot read it."""
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps(record))
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _kind_of(raw: object) -> rules.RunKind:
    """The kind a record names; missing, unknown or not a string is `role`, which counts."""
    match raw:
        case "merge":
            return "merge"
        case "land_checks":
            return "land_checks"
        case "land_model":
            return "land_model"
        case _:
            return "role"


def _spend_entry(entry: object) -> rules.SpendRun | None:
    """`[started_at, ended_at, cost_usd]` as the ledger writes it; `None` for anything
    else: wrong shape, an unparseable or naive instant, a cost that is not a finite
    non-negative number."""
    if not isinstance(entry, list) or len(entry) != 3:
        return None
    started, ended, cost = entry
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return None
    if not math.isfinite(cost) or cost < 0:
        return None
    try:
        start, end = datetime.fromisoformat(started), datetime.fromisoformat(ended)
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None or end.tzinfo is None:
        return None
    return rules.SpendRun(start.astimezone(UTC), end.astimezone(UTC), float(cost))


def _host() -> str:
    return socket.gethostname()
