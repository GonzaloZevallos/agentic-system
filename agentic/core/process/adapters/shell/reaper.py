"""`ProcessReaper` over `/proc` (GH-282, ADR-0052): stops the processes that carry the
mark of a role the system ran.

A process is the role's if its environment says so (`domain/reaping.py` decides), wherever
it runs and whatever its command line. The sequence for one reap:

1. Scan `/proc` and snapshot the marked processes as `(pid, starttime)`.
2. SIGTERM each one through a pidfd, after re-checking its starttime and its mark: the
   pidfd pins the process, so a reused pid is never signalled. Never `os.kill` by bare pid
   and never `killpg` (a `setsid` child is outside the group anyway).
3. Re-scan for up to `REAP_GRACE_S`: a child born during the grace inherited the mark and
   is found too.
4. SIGKILL what is left, and wait up to `REAP_KILL_WAIT_S` for it to go.

A process whose `environ` cannot be read (another user's, gone, a zombie's) is skipped:
never signalled and never recorded. A survivor is an entry and one line on stderr, never
an exception; no method of this class raises.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import NamedTuple

from ...domain import reaping
from ...domain.reaping import (
    REAP_GRACE_S,
    REAP_KILL_WAIT_S,
    ReapedProcess,
    ReapScope,
)
from ...domain.stall import ChildActivity

_PROC = Path("/proc")
_POLL_S = 0.05

# `(pid, starttime)`: the starttime is what tells a process from a later one with its pid.
_Key = tuple[int, int]


class _Stat(NamedTuple):
    state: str  # field 3
    ppid: int  # field 4
    cpu_ticks: int  # utime + stime, fields 14 and 15
    started: int  # field 22


def _stat(pid: int) -> _Stat:
    """What `/proc/<pid>/stat` says of `pid`. The command is field 2, in parentheses and
    free to hold spaces and parentheses, so it is cut at the last one."""
    text = (_PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    fields = text[text.rindex(")") + 2 :].split()
    return _Stat(fields[0], int(fields[1]), int(fields[11]) + int(fields[12]), int(fields[19]))


class ShellReaper:
    def __init__(self, product_key: str) -> None:
        self._product = product_key

    def reap_task(self, task_id: str) -> tuple[ReapedProcess, ...]:
        return self._reap(ReapScope(self._product, task_id, None, None))

    def reap_run(self, task_id: str, run: int) -> tuple[ReapedProcess, ...]:
        return self._reap(ReapScope(self._product, task_id, run, None))

    def reap_attempt(self, task_id: str, run: int, token: str) -> tuple[ReapedProcess, ...]:
        return self._reap(ReapScope(self._product, task_id, run, token))

    def _reap(self, scope: ReapScope) -> tuple[ReapedProcess, ...]:
        try:
            return self._sequence(scope)
        except Exception as exc:  # the port never raises: whatever broke is an entry
            return (ReapedProcess(pid=0, command=type(exc).__name__, outcome="error"),)

    def child_activity(
        self, task_id: str, run: int, token: str, parent_pid: int
    ) -> ChildActivity | None:
        """What the attempt's marked processes are doing, the CLI left out: the one marked
        process whose parent is `parent_pid`, the orchestrator (a CLI that waits on an API
        is idle by nature, and one in a loop is not a tool the model is waiting for).
        `None` when the scan could not be made; never raises (the verdict reads `None` as
        activity, so a failed read never kills an attempt)."""
        try:
            census = self._census(ReapScope(self._product, task_id, run, token))
        except Exception:
            return None
        children = [stat for _, stat in census.values() if stat.ppid != parent_pid]
        return ChildActivity(
            cpu_ticks=sum(stat.cpu_ticks for stat in children),
            running=any(stat.state in {"R", "D"} for stat in children),
        )

    def _sequence(self, scope: ReapScope) -> tuple[ReapedProcess, ...]:
        seen: dict[_Key, str] = {}
        killed: set[_Key] = set()
        deadline = time.monotonic() + REAP_GRACE_S
        while True:
            current = self._scan(scope)
            for key in current.keys() - seen.keys():
                seen[key] = current[key]
                self._signal(key, scope, signal.SIGTERM)
            if not current or time.monotonic() >= deadline:
                break
            time.sleep(_POLL_S)
        if current:
            deadline = time.monotonic() + REAP_KILL_WAIT_S
            while True:
                for key in current.keys() - killed:
                    seen.setdefault(key, current[key])
                    killed.add(key)
                    self._signal(key, scope, signal.SIGKILL)
                time.sleep(_POLL_S)
                current = self._scan(scope)
                if not current or time.monotonic() >= deadline:
                    break
        return tuple(
            self._entry(key, command, key in current, key in killed)
            for key, command in seen.items()
        )

    @staticmethod
    def _entry(key: _Key, command: str, alive: bool, killed: bool) -> ReapedProcess:
        if alive:
            print(f"reaper: process {key[0]} survived SIGKILL", file=sys.stderr)
            return ReapedProcess(pid=key[0], command=command, outcome="survived")
        return ReapedProcess(pid=key[0], command=command, outcome="kill" if killed else "term")

    def _scan(self, scope: ReapScope) -> dict[_Key, str]:
        """The marked, live processes of `scope`: `(pid, starttime)` -> command."""
        return {key: command for key, (command, _) in self._census(scope).items()}

    def _census(self, scope: ReapScope) -> dict[_Key, tuple[str, _Stat]]:
        """`_scan` with the stat of each process as read."""
        found: dict[_Key, tuple[str, _Stat]] = {}
        own = os.getpid()
        for name in os.listdir(_PROC):
            if not name.isdigit() or int(name) == own:
                continue
            pid = int(name)
            try:
                if not reaping.matches((_PROC / name / "environ").read_bytes(), scope):
                    continue
                stat = _stat(pid)
                if stat.state in {"Z", "X"}:
                    continue
                command = reaping.truncate_command((_PROC / name / "cmdline").read_bytes())
            except (OSError, ValueError, IndexError):
                continue  # unreadable, gone or unparsable: not provably the scope's
            found[(pid, stat.started)] = (command, stat)
        return found

    @staticmethod
    def _signal(key: _Key, scope: ReapScope, sig: signal.Signals) -> None:
        pid, started = key
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            return
        try:
            if _stat(pid).started != started:
                return
            if not reaping.matches((_PROC / str(pid) / "environ").read_bytes(), scope):
                return
            signal.pidfd_send_signal(fd, sig)
        except (OSError, ValueError, IndexError):
            return
        finally:
            os.close(fd)
