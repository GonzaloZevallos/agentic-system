"""Gate duration as concurrency grows (GH-361, ADR-0065): the table that measures the check
scheduler, the same way before and after it.

Usage: `python scripts/gate_concurrency.py [--transcripts DIR] [--documents DIR]`. The script
is read-only, needs no network and adds no signal.

`overlap_peers` is the only code that computes "other gates alongside": for each gate
interval, the time-weighted average number of other gate intervals overlapping it. Two
loaders feed it, and each source is reported on its own table:

- `from_transcripts(dir)`: Claude session JSONL transcripts (every `*.jsonl` under `dir`).
  A gate runs from the delivery tool's call to its result, and it passed when the result was
  not an error. A call shorter than `MIN_GATE_S` ran no checks (a refusal for the coverage
  map, a role with no gate) and is not a gate. This is the issue's own source, so it is the
  one that reads the same on both sides of the comparison.
- `from_documents(dir)`: the task documents' `gates` (on each role run), `merge_checks.gate`
  and `full_checks.gate` records, from `started_at` for `wait_s + run_s`. They give the wait
  and the run as separate columns and the share the gate held. A role run's last gate is
  the one that delivered, so it passed and the earlier ones were refused; a merge or full
  checks record passed when its own `passed` says so.

The table counts only passing gates, but refused gates still hold cores, so they count as
peers. Buckets are the rounded average: ~0, ~1, ~2, ~3 and 4+.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

import yaml

DELIVERY_TOOL = "StructuredOutput"
# A delivery call shorter than this ran no checks: it is not a gate.
MIN_GATE_S = 5.0
LAST_BUCKET = 4

Interval = tuple[float, float]

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


@dataclass(frozen=True)
class Gate:
    """One gate: its interval in epoch seconds and whether it passed. `wait_s`, `run_s` and
    `share` are known only from a task document."""

    start: float
    end: float
    passed: bool
    wait_s: float | None = None
    run_s: float | None = None
    share: int | None = None

    @property
    def minutes(self) -> float:
        return (self.end - self.start) / 60


def overlap_peers(intervals: Sequence[Interval]) -> list[float]:
    """For each interval, the time-weighted average number of the other intervals alongside
    it: the seconds the others overlap it, summed, over its own length. An empty interval
    has none, and intervals that only touch do not overlap."""
    peers: list[float] = []
    for i, (start, end) in enumerate(intervals):
        length = end - start
        if length <= 0:
            peers.append(0.0)
            continue
        shared = sum(
            max(0.0, min(end, other_end) - max(start, other_start))
            for j, (other_start, other_end) in enumerate(intervals)
            if j != i
        )
        peers.append(shared / length)
    return peers


def _epoch(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def _blocks(record: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                yield block


def from_transcripts(directory: Path) -> list[Gate]:
    """The gates of every session transcript under `directory`: a delivery-tool call paired
    with its result by `tool_use_id`. A call with no result, or shorter than `MIN_GATE_S`, is
    not a gate."""
    gates: list[Gate] = []
    for path in sorted(directory.rglob("*.jsonl")):
        calls: dict[str, float] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            at = _epoch(record.get("timestamp", ""))
            if at is None:
                continue
            for block in _blocks(record):
                if block.get("type") == "tool_use" and block.get("name") == DELIVERY_TOOL:
                    calls[str(block.get("id"))] = at
                elif block.get("type") == "tool_result":
                    began = calls.pop(str(block.get("tool_use_id")), None)
                    if began is not None and at - began >= MIN_GATE_S:
                        gates.append(Gate(began, at, passed=not block.get("is_error")))
    return sorted(gates, key=lambda gate: (gate.start, gate.end))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _gate_of(record: object, passed: bool) -> Gate | None:
    if not isinstance(record, dict):
        return None
    started = _epoch(record.get("started_at", ""))
    wait_s = _number(record.get("wait_s"))
    run_s = _number(record.get("run_s"))
    if started is None or wait_s is None or run_s is None:
        return None
    share = record.get("share")
    return Gate(
        started,
        started + timedelta(seconds=wait_s + run_s).total_seconds(),
        passed,
        wait_s,
        run_s,
        share if isinstance(share, int) and not isinstance(share, bool) else None,
    )


def _document_gates(data: Mapping[str, Any]) -> Iterator[Gate | None]:
    for run in data.get("runs") or []:
        recorded = run.get("gates") if isinstance(run, dict) else None
        if isinstance(recorded, list):
            for index, record in enumerate(recorded):
                yield _gate_of(record, passed=index == len(recorded) - 1)
    for key in ("merge_checks", "full_checks"):
        checks = data.get(key)
        if isinstance(checks, dict):
            yield _gate_of(checks.get("gate"), passed=checks.get("passed") is True)


def from_documents(directory: Path) -> list[Gate]:
    """The gates the task documents under `directory` recorded, in start order. A document
    without a record (written before GH-361) or that does not parse adds none."""
    gates: list[Gate] = []
    for path in sorted(directory.glob("*.md")):
        matched = _FRONTMATTER.match(path.read_text(encoding="utf-8", errors="replace"))
        if matched is None:
            continue
        try:
            data = yaml.safe_load(matched.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            gates.extend(gate for gate in _document_gates(data) if gate is not None)
    return sorted(gates, key=lambda gate: (gate.start, gate.end))


def bucket(peers: float) -> int:
    """The rounded average (halves go up), capped at `LAST_BUCKET`."""
    return min(int(peers + 0.5), LAST_BUCKET)


def _label(index: int) -> str:
    return f"{index}+" if index == LAST_BUCKET else f"~{index}"


def _median(values: Iterable[float]) -> str:
    listed = list(values)
    return f"{statistics.median(listed):.1f}" if listed else "-"


def render(source: str, gates: Sequence[Gate], *, detailed: bool) -> str:
    """The table of one source. `detailed` adds the median wait and run and the median by
    share, which only a task document knows."""
    peers = overlap_peers([(gate.start, gate.end) for gate in gates])
    grouped: dict[int, list[Gate]] = {index: [] for index in range(LAST_BUCKET + 1)}
    for gate, alongside in zip(gates, peers, strict=True):
        if gate.passed:
            grouped[bucket(alongside)].append(gate)
    header = ["other gates alongside", "gates measured", "median min"]
    if detailed:
        header += ["median wait min", "median run min"]
    rows = [header]
    for index, members in grouped.items():
        row = [_label(index), str(len(members)), _median(g.minutes for g in members)]
        if detailed:
            row += [
                _median(g.wait_s / 60 for g in members if g.wait_s is not None),
                _median(g.run_s / 60 for g in members if g.run_s is not None),
            ]
        rows.append(row)
    lines = [f"source: {source}", *("| " + " | ".join(row) + " |" for row in rows)]
    if detailed:
        by_share: dict[int, list[Gate]] = {}
        for gate in gates:
            if gate.passed and gate.share is not None:
                by_share.setdefault(gate.share, []).append(gate)
        lines += ["", "| share | gates measured | median min |"]
        lines += [
            f"| {share} | {len(members)} | {_median(g.minutes for g in members)} |"
            for share, members in sorted(by_share.items())
        ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str], stdout: TextIO) -> int:
    parser = argparse.ArgumentParser(prog="gate_concurrency", description=__doc__)
    parser.add_argument("--transcripts", type=Path, help="directory of session JSONL transcripts")
    parser.add_argument("--documents", type=Path, help="directory of task documents")
    args = parser.parse_args(list(argv))
    if args.transcripts is None and args.documents is None:
        parser.error("give --transcripts, --documents or both")
    tables = []
    if args.transcripts is not None:
        tables.append(render("transcripts", from_transcripts(args.transcripts), detailed=False))
    if args.documents is not None:
        tables.append(render("documents", from_documents(args.documents), detailed=True))
    stdout.write("\n".join(tables))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:], sys.stdout))
