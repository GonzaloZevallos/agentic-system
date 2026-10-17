"""Which processes belong to a role the system ran, and how they are named (GH-282,
ADR-0052).

A role that starts something in the background (`nohup`, `setsid`, `run_in_background`)
leaves the process group the system controls, so the group cannot find it again. Every
attempt of a role gets one environment variable instead, and every child inherits it,
wherever it goes. This module builds that value, parses it and decides whether an
environment matches a scope. It is the only place the mark is built or parsed, and the
only file under `agentic/` that spells the variable's name. Pure: the reading of
`/proc` and the signals are the adapter's.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, NamedTuple, TypedDict

# Deliberately outside the `AGENTIC_*` prefix: the scrubbers of the environment remove
# that prefix, and the mark has to reach the grandchildren of a check a role ran.
MARK_NAME = "ROLE_PROCESS_OWNER"

# Declared, not measured: seconds a process gets after SIGTERM before SIGKILL, seconds
# the system waits after SIGKILL before it calls a process a survivor, and the longest
# command line a record keeps.
REAP_GRACE_S = 5
REAP_KILL_WAIT_S = 2
COMMAND_MAX = 120

ReapOutcome = Literal["term", "kill", "survived", "error"]


class ReapedProcess(TypedDict):
    """One process the reaper signalled. An `error` entry has `pid` 0 and the exception's
    class name as `command`."""

    pid: int
    command: str
    outcome: ReapOutcome


@dataclass(frozen=True)
class ReapScope:
    """What a reap matches: a product and a task, narrowed to one run (the pid of the
    system process that ran the attempt) and then to one attempt (its token)."""

    product: str
    task: str
    run: int | None
    token: str | None


class ParsedMark(NamedTuple):
    product: str
    task: str
    run: int
    token: str


def product_key(product_dir: str) -> str:
    """First 12 hex characters of the sha256 of the resolved product path."""
    return hashlib.sha256(product_dir.encode("utf-8")).hexdigest()[:12]


def mark(product: str, task: str, run: int, token: str) -> str:
    return f"{product}/{task}/{run}/{token}"


def parse_mark(value: str) -> ParsedMark | None:
    parts = value.split("/")
    if len(parts) != 4:
        return None
    product, task, run, token = parts
    if not (product and task and token and run.isascii() and run.isdigit()):
        return None
    return ParsedMark(product, task, int(run), token)


def _mark_value(environ: bytes) -> str | None:
    wanted = MARK_NAME.encode("ascii") + b"="
    for entry in environ.split(b"\0"):
        if entry.startswith(wanted):
            return entry[len(wanted) :].decode("utf-8", errors="replace")
    return None


def matches(environ: bytes, scope: ReapScope) -> bool:
    """Whether the NUL-separated `KEY=VALUE` block carries a mark inside `scope`. The
    name is matched whole and the value segment by segment, never as a substring. A task
    scope also covers the task's subtasks (`GH-12` takes `GH-12.sub`, never `GH-1`)."""
    value = _mark_value(environ)
    if value is None:
        return False
    parsed = parse_mark(value)
    if parsed is None or parsed.product != scope.product:
        return False
    if not (parsed.task == scope.task or parsed.task.startswith(scope.task + ".")):
        return False
    if scope.run is not None and parsed.run != scope.run:
        return False
    return scope.token is None or parsed.token == scope.token


def truncate_command(cmdline: bytes) -> str:
    """The NUL-separated arguments joined by spaces, decoded leniently, cut to `COMMAND_MAX`."""
    arguments = [part for part in cmdline.split(b"\0") if part]
    return " ".join(part.decode("utf-8", errors="replace") for part in arguments)[:COMMAND_MAX]
