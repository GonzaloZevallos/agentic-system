"""`CheckScheduler` over one JSON ledger per machine (GH-361, ADR-0069).

The ledger lists who holds a share of the machine's cores and who waits for one, across
every product and every process: an in-process semaphore bounds nothing when the gates are
separate processes. Each read-modify-write holds `fcntl.flock` on a sibling lock file, which
the kernel releases if its holder dies, so a crashed gate never wedges the others.

An entry is kept while its pid is alive in this boot and its lease has not run out. A dead
holder of this product has its marked processes reaped by this product's own reaper before
its cores come back; a dead entry of another product stays booked until that product reaps
it or the lease runs out, because only that product's key finds its processes. A live pid
past its lease is dropped without a reap: the pid may have been reused by a gate of ours.
The ledger holds closed fields only, never a command.

The rules (who is next, how many cores) are `domain.check_schedule`; this module is the
file, the lock, the polling and the reaping.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ...application.ports import CheckRequest, Grant, ProcessReaper
from ...domain import reaping
from ...domain.check_schedule import (
    CheckPriority,
    Decision,
    Entry,
    Limits,
    Request,
    alive,
    decide,
    overdue_decision,
)
from .liveness import current_boot_id
from .liveness import is_alive as pid_alive

# Declared, not measured: how often a waiter looks at the ledger again.
POLL_INTERVAL_S = 1.0

LEDGER_FILE = "ledger.json"
LOCK_FILE = "ledger.lock"


class FilesystemCheckLedger:
    def __init__(
        self,
        directory: Path,
        product: str,
        reaper: ProcessReaper,
        limits: Limits,
        *,
        boot_id: Callable[[], str | None] = current_boot_id,
        is_alive: Callable[[int, str | None], bool] | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        pid: Callable[[], int] = os.getpid,
    ) -> None:
        self._directory = directory
        self._product = product
        self._reaper = reaper
        self._limits = limits
        self._boot_id = boot_id
        self._is_alive = is_alive or (lambda p, recorded: pid_alive(p, recorded, boot_id()))
        self._wall = wall_clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._pid = pid

    def acquire(self, request: CheckRequest) -> Grant:
        token = request.token or uuid4().hex
        enqueued_at = request.enqueued_at if request.enqueued_at is not None else self._wall()
        started = self._monotonic()
        deadline = started + request.wait_max_s
        ask = Request(request.priority, enqueued_at, self._pid(), token)
        try:
            while True:
                with self._ledger() as entries:
                    now = self._wall()
                    decision = decide(entries, ask, now, self._limits)
                    if decision.granted:
                        return self._book(entries, request, ask, decision, now, started)
                    self._queue(entries, request, ask, now)
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                self._sleep(min(POLL_INTERVAL_S, remaining))
            with self._ledger() as entries:
                now = self._wall()
                decision = overdue_decision(entries, ask, now, self._limits)
                return self._book(entries, request, ask, decision, now, started)
        except BaseException:
            self._drop(token)
            raise

    def release(self, grant: Grant) -> None:
        self._drop(grant.token)

    def _drop(self, token: str) -> None:
        with self._ledger() as entries:
            entries[:] = [e for e in entries if e.token != token]

    def _queue(self, entries: list[Entry], request: CheckRequest, ask: Request, now: float) -> None:
        """Our waiter line, kept where the others can see it; its lease is set once."""
        mine = next((e for e in entries if e.token == ask.token), None)
        if mine is not None:
            return
        entries.append(
            Entry(
                self._product,
                ask.pid,
                self._boot_id(),
                ask.token,
                ask.priority,
                ask.enqueued_at,
                0,
                None,
                now + request.expires_after_s,
            )
        )

    def _book(
        self,
        entries: list[Entry],
        request: CheckRequest,
        ask: Request,
        decision: Decision,
        now: float,
        started: float,
    ) -> Grant:
        peers = sum(1 for e in entries if e.token != ask.token and e.share > 0)
        entries[:] = [e for e in entries if e.token != ask.token]
        entries.append(
            Entry(
                self._product,
                ask.pid,
                self._boot_id(),
                ask.token,
                ask.priority,
                ask.enqueued_at,
                decision.share,
                now,
                now + request.expires_after_s,
                decision.overdue,
            )
        )
        return Grant(
            decision.share,
            self._monotonic() - started,
            peers,
            decision.overdue,
            ask.enqueued_at,
            ask.token,
        )

    @contextmanager
    def _ledger(self) -> Iterator[list[Entry]]:
        """The live entries under the lock; whatever the block leaves in the list is
        written back when it ends without raising."""
        self._directory.mkdir(parents=True, exist_ok=True)
        with open(self._directory / LOCK_FILE, "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            entries = self._prune(self._read())
            yield entries
            self._write(entries)

    def _read(self) -> list[Entry]:
        try:
            data = json.loads((self._directory / LEDGER_FILE).read_text(encoding="utf-8"))
            rows = data["entries"]
        except (OSError, ValueError, KeyError, TypeError):
            return []
        entries: list[Entry] = []
        for row in rows if isinstance(rows, list) else []:
            try:
                entries.append(Entry(**{**row, "priority": CheckPriority(row["priority"])}))
            except (TypeError, ValueError, KeyError):
                continue  # one unreadable line is not worth the others
        return entries

    def _write(self, entries: list[Entry]) -> None:
        rows = [{**asdict(e), "priority": e.priority.value} for e in entries]
        path = self._directory / LEDGER_FILE
        private = path.with_name(f"{LEDGER_FILE}.{os.getpid()}.{uuid4().hex}")
        private.write_text(json.dumps({"entries": rows}), encoding="utf-8")
        os.replace(private, path)

    def _prune(self, entries: list[Entry]) -> list[Entry]:
        now = self._wall()
        kept: list[Entry] = []
        for entry in entries:
            if alive(entry, now, self._is_alive):
                kept.append(entry)
                continue
            expired = now >= entry.expires_at
            if entry.product != self._product:
                if not expired:
                    kept.append(entry)  # not ours to reap: it stays booked until its lease ends
                continue
            if self._is_alive(entry.pid, entry.boot_id):
                continue  # past its lease with a live pid, maybe a reused one: never reaped
            if entry.boot_id is not None and entry.boot_id != self._boot_id():
                continue  # an earlier boot: nothing survives, its pid may be a live gate's
            if not self._reaped(entry) and not expired:
                kept.append(entry)  # a process survived the reap: the cores stay booked
        return kept

    def _reaped(self, entry: Entry) -> bool:
        """Its marked processes are gone: nothing survived the reap."""
        reaped = self._reaper.reap_run(reaping.CHECK_TASK, entry.pid)
        return all(r["outcome"] != "survived" for r in reaped)
