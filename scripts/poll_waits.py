"""Land and merge wait times from a poller journal (GH-362, rule 4): the "before and
after" measure of "finish before starting".

Usage: `journalctl --user -o short-iso -u <poller unit> | python scripts/poll_waits.py
--product NAME`. One journal unit is one product poller, so `--product` only labels the
output. The script is read-only, outside the measurement module, and adds no signal.

The decision is pure (`parse_line`, `measure`, `render`); `main` is the thin shell. It
reads the lines `poll.deferred_line` and `poll.execute` write:

- `agentic: poll: land #N waits: <reason>` and `agentic: poll: merge #N waits: <reason>`;
- `agentic: poll: land #N -> pid P` and `agentic: poll: merge #N -> pid P`, the starts;
- systemd's `Started <unit> - ...` line, which says the poller restarted.

A land wait opens at the first `land #N waits: no_slot|land_queued` (both reasons are
the same open wait) and closes at the next `land #N -> pid`. An exempt wait
(`exempt_busy`, merge or land) is the same shape in a block of its own. A wait still open
at the end of input, or cut by a restart, is reported as truncated and never enters the
median. `pause_waiting` is only counted.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TextIO

LAND_WAIT_REASONS = frozenset({"no_slot", "land_queued"})
EXEMPT_REASON = "exempt_busy"
PAUSE_REASON = "pause_waiting"

# `2026-10-16T08:34:16-03:00 host unit[pid]: message` (journalctl -o short-iso).
_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[.,]\d+)?(?:[+-]\d\d:?\d\d|Z)?)\s+\S+\s+(?P<rest>.*)$"
)
_WAIT = re.compile(r"agentic: poll: (?P<kind>land|merge) #(?P<issue>\d+) waits: (?P<reason>\w+)")
_START = re.compile(r"agentic: poll: (?P<kind>land|merge) #(?P<issue>\d+) -> pid \d+")
# systemd's own line for a (re)started unit: `systemd[3308]: Started <unit>.service - ...`.
_RESTART = re.compile(r"^systemd\[\d+\]: Started \S+\.service\b")

LAND = "land"
EXEMPT = "exempt"


@dataclass(frozen=True)
class Event:
    """One timestamped line: a `wait`, a `start`, a `restart` or an `other`."""

    at: datetime
    what: str
    kind: str = ""
    issue: int = 0
    reason: str = ""


@dataclass(frozen=True)
class Interval:
    """A wait: `closed` by a start, or truncated by a restart or by the end of input."""

    block: str
    kind: str
    issue: int
    start: datetime
    end: datetime
    closed: bool
    cut_by: str = ""

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass(frozen=True)
class Measure:
    intervals: tuple[Interval, ...]
    pause_waiting: int


def parse_line(line: str) -> Event | None:
    """The event a journal line carries: `other` for a timestamped line this measure does
    not read (it still moves the end of input), None for a line with no timestamp."""
    matched = _LINE.match(line.strip())
    if matched is None:
        return None
    try:
        at = datetime.fromisoformat(matched["ts"].replace(",", "."))
    except ValueError:
        return None
    rest = matched["rest"]
    if _RESTART.match(rest):
        return Event(at, "restart")
    wait = _WAIT.search(rest)
    if wait is not None:
        return Event(at, "wait", wait["kind"], int(wait["issue"]), wait["reason"])
    start = _START.search(rest)
    if start is not None:
        return Event(at, "start", start["kind"], int(start["issue"]))
    return Event(at, "other")


def _block(reason: str) -> str | None:
    if reason in LAND_WAIT_REASONS:
        return LAND
    if reason == EXEMPT_REASON:
        return EXEMPT
    return None


def measure(lines: Iterable[str]) -> Measure:
    """Pair waits with the starts that close them, per (block, kind, issue)."""
    open_waits: dict[tuple[str, str, int], datetime] = {}
    done: list[Interval] = []
    pauses = 0
    last: datetime | None = None

    def cut(cause: str, at: datetime) -> None:
        for (block, kind, issue), began in open_waits.items():
            done.append(Interval(block, kind, issue, began, at, False, cause))
        open_waits.clear()

    for line in lines:
        event = parse_line(line)
        if event is None:
            continue
        last = event.at
        if event.what == "restart":
            cut("restart", event.at)
        elif event.what == "start":
            for block in (LAND, EXEMPT):
                began = open_waits.pop((block, event.kind, event.issue), None)
                if began is not None:
                    done.append(Interval(block, event.kind, event.issue, began, event.at, True))
        elif event.what == "wait" and event.reason == PAUSE_REASON:
            pauses += 1
        elif event.what == "wait":
            opened = _block(event.reason)
            if opened is not None:
                open_waits.setdefault((opened, event.kind, event.issue), event.at)
    if last is not None:
        cut("end of input", last)
    ordered = sorted(done, key=lambda i: (i.block, i.start, i.kind, i.issue))
    return Measure(tuple(ordered), pauses)


def _fmt(minutes: float) -> str:
    return f"{minutes:.1f}"


def _block_lines(title: str, intervals: Sequence[Interval]) -> list[str]:
    closed = [i for i in intervals if i.closed]
    truncated = [i for i in intervals if not i.closed]
    minutes = [i.minutes for i in closed]
    out = [title]
    out.append(f"  intervals: {len(closed)}")
    out.append(f"  median_min: {_fmt(statistics.median(minutes)) if minutes else '-'}")
    out.append(f"  total_min: {_fmt(sum(minutes))}")
    out.append(f"  truncated: {len(truncated)}")
    for item in truncated:
        out.append(
            f"    {item.kind} #{item.issue} open {_fmt(item.minutes)} min "
            f"(cut by {item.cut_by}, since {item.start.isoformat()})"
        )
    return out


def render(product: str, result: Measure) -> str:
    land = [i for i in result.intervals if i.block == LAND]
    exempt = [i for i in result.intervals if i.block == EXEMPT]
    blocks = [
        [f"product: {product}"],
        _block_lines("land wait (no_slot, land_queued: open until `land #N -> pid`)", land),
        _block_lines(
            "not expected to move (exempt serialisation out of scope): exempt_busy, "
            "merges and lands",
            exempt,
        ),
        ["pause_waiting (counted only)", f"  count: {result.pause_waiting}"],
    ]
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def main(argv: Sequence[str], stdin: TextIO) -> int:
    parser = argparse.ArgumentParser(prog="poll_waits", description=__doc__)
    parser.add_argument("--product", default="-", help="label for the output")
    args = parser.parse_args(list(argv))
    sys.stdout.write(render(args.product, measure(stdin)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], sys.stdin))
