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

`reap_within` (GH-315) is the same sequence with another rule: the processes whose cwd is
inside a checkout under the worktrees root, that carry no mark, are not the system or one of
its ancestors, are not a zombie and have no controlling terminal. A root that does not
resolve strictly inside the worktrees root is dropped, so nothing outside it is reachable.

A process whose `environ` cannot be read (another user's, gone, a zombie's) is skipped:
never signalled and never recorded. A survivor is an entry and one line on stderr, never
an exception; no method of this class raises.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable, Sequence
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

# Whether the process `pid` is still one the reap that picked it may signal.
_Rule = Callable[[int], bool]


class _Stat(NamedTuple):
    state: str  # field 3
    ppid: int  # field 4
    cpu_ticks: int  # utime + stime, fields 14 and 15
    tty: int  # field 7, the controlling terminal: 0 for none
    started: int  # field 22


def _stat(pid: int) -> _Stat:
    """What `/proc/<pid>/stat` says of `pid`. The command is field 2, in parentheses and
    free to hold spaces and parentheses, so it is cut at the last one."""
    text = (_PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    fields = text[text.rindex(")") + 2 :].split()
    return _Stat(
        fields[0],
        int(fields[1]),
        int(fields[11]) + int(fields[12]),
        int(fields[4]),
        int(fields[19]),
    )


class ShellReaper:
    def __init__(self, product_key: str, worktrees_root: Path) -> None:
        self._product = product_key
        self._worktrees = worktrees_root

    def reap_task(self, task_id: str) -> tuple[ReapedProcess, ...]:
        return self._reap(ReapScope(self._product, task_id, None, None))

    def reap_run(self, task_id: str, run: int) -> tuple[ReapedProcess, ...]:
        return self._reap(ReapScope(self._product, task_id, run, None))

    def reap_attempt(self, task_id: str, run: int, token: str) -> tuple[ReapedProcess, ...]:
        return self._reap(ReapScope(self._product, task_id, run, token))

    def reap_within(self, paths: Sequence[Path]) -> tuple[ReapedProcess, ...]:
        try:
            roots = self._roots(paths)
            if not roots:
                return ()
            excluded = self._own_lineage()
            reaped = self._sequence(
                lambda: self._within_scan(roots, excluded),
                lambda pid: self._within_rule(pid, roots, excluded),
            )
        except Exception as exc:  # the port never raises: whatever broke is an entry
            return (ReapedProcess(pid=0, command=type(exc).__name__, outcome="error"),)
        for entry in reaped:
            if entry["outcome"] != "survived":  # a survivor has its own line already
                print(
                    f"reaper: stopped {entry['pid']} {entry['command']} ({entry['outcome']})",
                    file=sys.stderr,
                )
        return reaped

    def _roots(self, paths: Sequence[Path]) -> list[str]:
        """The `paths` that resolve strictly inside the worktrees root, resolved."""
        base = self._worktrees.resolve()
        resolved = (path.resolve() for path in paths)
        return [str(path) for path in resolved if path != base and path.is_relative_to(base)]

    @staticmethod
    def _own_lineage() -> set[int]:
        """This process and every ancestor of it: the system's own, never to be signalled."""
        lineage = {os.getpid()}
        pid = os.getpid()
        while pid > 1:
            try:
                pid = _stat(pid).ppid
            except (OSError, ValueError, IndexError):
                break
            if pid in lineage:
                break
            lineage.add(pid)
        return lineage

    def _reap(self, scope: ReapScope) -> tuple[ReapedProcess, ...]:
        try:
            return self._sequence(
                lambda: self._scan(scope), lambda pid: self._marked_rule(pid, scope)
            )
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

    def _sequence(
        self, scan: Callable[[], dict[_Key, str]], rule: _Rule
    ) -> tuple[ReapedProcess, ...]:
        seen: dict[_Key, str] = {}
        killed: set[_Key] = set()
        deadline = time.monotonic() + REAP_GRACE_S
        while True:
            current = scan()
            for key in current.keys() - seen.keys():
                seen[key] = current[key]
                self._signal(key, rule, signal.SIGTERM)
            if not current or time.monotonic() >= deadline:
                break
            time.sleep(_POLL_S)
        if current:
            deadline = time.monotonic() + REAP_KILL_WAIT_S
            while True:
                for key in current.keys() - killed:
                    seen.setdefault(key, current[key])
                    killed.add(key)
                    self._signal(key, rule, signal.SIGKILL)
                time.sleep(_POLL_S)
                current = scan()
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

    def _within_scan(self, roots: list[str], excluded: set[int]) -> dict[_Key, str]:
        """The live processes `_within_rule` takes: `(pid, starttime)` -> command."""
        found: dict[_Key, str] = {}
        for name in os.listdir(_PROC):
            if not name.isdigit() or int(name) in excluded:
                continue
            pid = int(name)
            try:
                if not self._within_rule(pid, roots, excluded):
                    continue
                stat = _stat(pid)
                command = reaping.truncate_command((_PROC / name / "cmdline").read_bytes())
            except (OSError, ValueError, IndexError):
                continue  # unreadable, gone or unparsable: not provably inside a checkout
            found[(pid, stat.started)] = command
        return found

    @staticmethod
    def _within_rule(pid: int, roots: list[str], excluded: set[int]) -> bool:
        """Unmarked, not the system's lineage, not a zombie, no terminal, cwd inside `roots`."""
        if pid in excluded:
            return False
        try:
            if reaping.is_marked((_PROC / str(pid) / "environ").read_bytes()):
                return False
            if not reaping.cwd_within(os.readlink(_PROC / str(pid) / "cwd"), roots):
                return False
            stat = _stat(pid)
        except (OSError, ValueError, IndexError):
            return False
        return stat.tty == 0 and stat.state not in {"Z", "X"}

    @staticmethod
    def _marked_rule(pid: int, scope: ReapScope) -> bool:
        try:
            return reaping.matches((_PROC / str(pid) / "environ").read_bytes(), scope)
        except (OSError, ValueError, IndexError):
            return False

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
    def _signal(key: _Key, rule: _Rule, sig: signal.Signals) -> None:
        pid, started = key
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            return
        try:
            if _stat(pid).started != started:
                return
            if not rule(pid):
                return
            signal.pidfd_send_signal(fd, sig)
        except (OSError, ValueError, IndexError):
            return
        finally:
            os.close(fd)
