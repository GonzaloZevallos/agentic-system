"""The history a plan's estimate is calibrated against (ADR-0053): what closed tasks
cost, by how many subtasks they had, and how their estimates compared with what they
spent. Pure over the frontmatter dicts of the closed documents; the Tech Lead reads it
as the value of the `{{cost_history}}` placeholder, never as part of the prompt text
that is hashed."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .task import INTEGRATION

# The bucket of every task with this many subtasks or more.
LAST_BUCKET = 6
# A bucket speaks for the next plan only with this many tasks behind it.
MIN_BUCKET = 5
# How many of the latest estimate/actual pairs the Tech Lead sees.
PAIRS = 20

EMPTY = "No closed task has a recorded cost yet: calibrate on the scope alone."


@dataclass(frozen=True)
class Bucket:
    n: int
    median: float
    p90: float


@dataclass(frozen=True)
class Pair:
    estimated_usd: float
    actual_usd: float
    estimated_files: int
    actual_files: int | None


@dataclass(frozen=True)
class History:
    buckets: dict[int, Bucket]
    pairs: tuple[Pair, ...]

    def text(self) -> str:
        return render(self)


def _bucket_of(n_subtasks: int) -> int:
    return min(max(n_subtasks, 1), LAST_BUCKET)


def _p90(sorted_costs: Sequence[float]) -> float:
    return sorted_costs[math.ceil(0.9 * len(sorted_costs)) - 1]


def build(documents: Sequence[Mapping[str, Any]]) -> History:
    """The buckets and the pairs of the closed documents, in the order given (oldest
    first: the pairs keep the latest `PAIRS`). A document with no `cost_usd` is skipped."""
    costs: dict[int, list[float]] = {}
    pairs: list[Pair] = []
    for document in documents:
        if document.get("cost_usd") is None:
            continue
        cost = float(document["cost_usd"])
        count = len([sid for sid in document.get("subtasks") or {} if sid != INTEGRATION])
        costs.setdefault(_bucket_of(count), []).append(cost)
        estimate = document.get("estimate")
        if estimate:
            files = document.get("actual_files")
            pairs.append(
                Pair(
                    float(estimate["cost_usd"]),
                    cost,
                    int(estimate["files"]),
                    None if files is None else int(files),
                )
            )
    buckets = {
        key: Bucket(len(values), statistics.median(values), _p90(sorted(values)))
        for key, values in sorted(costs.items())
    }
    return History(buckets, tuple(pairs[-PAIRS:]))


def reference_usd(history: History, n_subtasks: int) -> float | None:
    """The p90 of the bucket of this many subtasks, when it has at least `MIN_BUCKET`
    tasks; `None` otherwise (a thin bucket is no reference)."""
    bucket = history.buckets.get(_bucket_of(n_subtasks))
    return bucket.p90 if bucket and bucket.n >= MIN_BUCKET else None


def bucket_median(history: History, n_subtasks: int) -> float | None:
    """The median cost of the bucket, under the same `MIN_BUCKET` rule as the reference."""
    bucket = history.buckets.get(_bucket_of(n_subtasks))
    return bucket.median if bucket and bucket.n >= MIN_BUCKET else None


def render(history: History) -> str:
    """The text of the `{{cost_history}}` placeholder: one fixed line when there is no
    history at all."""
    if not history.buckets:
        return EMPTY
    lines = ["Closed tasks by number of subtasks (cost in USD):"]
    for key, bucket in history.buckets.items():
        label = f"{key}+" if key == LAST_BUCKET else str(key)
        lines.append(
            f"- {label} subtasks: n={bucket.n}, median {bucket.median:.2f}, p90 {bucket.p90:.2f}"
        )
    if history.pairs:
        lines.append("Latest estimates against what happened:")
        for pair in history.pairs:
            files = "?" if pair.actual_files is None else str(pair.actual_files)
            lines.append(
                f"- estimated {pair.estimated_usd:.2f} USD / {pair.estimated_files} files, "
                f"actual {pair.actual_usd:.2f} USD / {files} files"
            )
    return "\n".join(lines)
