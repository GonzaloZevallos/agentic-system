"""What the gates that judge a head record of its flakes (GH-464): the instant a record is
dated, and the records of the `agentic-flakes:` block the stack's CI jobs printed for that head.

Evidence, never a verdict: a block that is missing, unreadable or malformed records nothing and
changes no pass, fail or merge decision, because the gates decide only through
`presubmit.flake_verdict` and `presubmit.outcomes` (invariant 4). The checks a block may name are
the ones the stack declares at the task's base, never at the head under test (invariant 8)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from ..domain import ci
from ..domain.ci import CiUnreadable
from ..domain.presubmit import FlakeGate
from ..domain.task import FlakeRecord
from .ports import Ports


def now() -> str:
    """The instant a call dates its records with, ISO 8601 UTC: read once per call by the
    caller, so every record of one call carries the same `at`."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def dated(records: Sequence[FlakeRecord], at: str) -> list[FlakeRecord]:
    """`records` stamped with `at`, the figure the issue threshold counts."""
    return [{**record, "at": at} for record in records]


def block_records(
    ports: Ports,
    reference: str,
    head: str,
    base: str,
    jobs: Sequence[str],
    gate: FlakeGate,
    at: str,
) -> list[FlakeRecord]:
    """The records of the flake block of `jobs` on `head`, dated `at`. `reference` is the pull
    request or the CI ref the report is read from. Empty when the product has no `ci_logs`
    port or no job, when the CI cannot be read for `head`, or when no declared check has a
    well-formed block."""
    if ports.ci_logs is None or not jobs:
        return []
    read = ports.ci_logs.flake_block(reference, head, jobs)
    if isinstance(read, CiUnreadable):
        return []
    lines: dict[str, list[str]] = {}
    for job, found in read:
        lines.setdefault(job, []).extend(found)
    declared = frozenset(ports.stack.checks(base))
    return dated(ci.parse_flake_blocks(lines, declared, head, gate), at)
