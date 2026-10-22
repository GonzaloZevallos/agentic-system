"""Per-role token and turn breakdown over task documents' `runs[]` (GH-292).

A read over documents, nothing recorded: no metric, label, span attribute or document
key is added (ADR-0037). Pure domain -- `eval compare` groups the documents with
`scorecard.bucket_by_baseline` and hands the runs of one bucket here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoleUsage:
    """What one `Role.slug` spent across the runs given: how many runs, the recorded
    `turns` summed, the four token kinds the runner records, and `cost_usd`."""

    runs: int
    turns: int
    input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    cost_usd: float


def by_role(runs: Iterable[Mapping[str, Any]]) -> dict[str, RoleUsage]:
    """Sums `runs` per `slug`, in order of first appearance.

    A field missing from a run recorded before it existed reads as 0 through `.get`:
    `tokens` (and each kind inside it), `turns` and `cost_usd` alike; a run with no
    `slug` is bucketed under `unknown`, the same default `RoleRun.slug` documents.
    """
    totals: dict[str, RoleUsage] = {}
    for run in runs:
        tokens = run.get("tokens") or {}
        before = totals.get(run.get("slug") or "unknown")
        totals[run.get("slug") or "unknown"] = RoleUsage(
            runs=(before.runs if before else 0) + 1,
            turns=(before.turns if before else 0) + run.get("turns", 0),
            input_tokens=(before.input_tokens if before else 0) + tokens.get("input", 0),
            cache_read_tokens=(before.cache_read_tokens if before else 0)
            + tokens.get("cache_read", 0),
            cache_write_tokens=(before.cache_write_tokens if before else 0)
            + tokens.get("cache_write", 0),
            output_tokens=(before.output_tokens if before else 0) + tokens.get("output", 0),
            cost_usd=(before.cost_usd if before else 0.0) + run.get("cost_usd", 0.0),
        )
    return totals
