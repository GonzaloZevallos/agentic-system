"""The history a plan's estimate is calibrated against (ADR-0054): what closed tasks
cost, by how many subtasks they had, and how their estimates compared with what they
spent. Pure over the frontmatter dicts of the closed documents; the Tech Lead reads it
as the value of the `{{cost_history}}` placeholder, never as part of the prompt text
that is hashed."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agentic.shared.configuration import DEFAULT_PLAN_SHAPE

from . import size_budget
from .experiment import plan_shape_of
from .task import INTEGRATION

# The bucket of every task with this many subtasks or more.
LAST_BUCKET = 6
# A bucket speaks for the next plan only with this many tasks behind it.
MIN_BUCKET = 5
# How many of the latest estimate/actual pairs the Tech Lead sees.
PAIRS = 20

# The `plan_shape` whose closed tasks the history is made of.
FREE = DEFAULT_PLAN_SHAPE

EMPTY = "No closed task has a recorded cost yet: calibrate on the scope alone."


@dataclass(frozen=True)
class Bucket:
    n: int
    median: float
    p90: float


@dataclass(frozen=True)
class Floor:
    """The least a plan's estimate may be: `usd`, and which floor it is (`spent`: what the
    task already spent; `median`: half the median of closed tasks with as many subtasks)."""

    usd: float
    cause: Literal["spent", "median"]


@dataclass(frozen=True)
class Raise:
    """An estimate the system lifted: the Tech Lead's own figure, the one the task runs
    under, and the floor that forced it."""

    declared_usd: float
    cost_usd: float
    cause: Literal["spent", "median"]


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
    # The planned and the actual size of every closed task that recorded both (GH-592).
    sizes: tuple[tuple[size_budget.PlanSize, size_budget.ActualSize], ...] = ()

    def text(self) -> str:
        return render(self)


def _bucket_of(n_subtasks: int) -> int:
    return min(max(n_subtasks, 1), LAST_BUCKET)


def _p90(sorted_costs: Sequence[float]) -> float:
    return sorted_costs[math.ceil(0.9 * len(sorted_costs)) - 1]


def build(documents: Sequence[Mapping[str, Any]]) -> History:
    """The buckets and the pairs of the closed documents, in the order given (oldest
    first: the pairs keep the latest `PAIRS`). A document with no `cost_usd` is skipped, and
    so is one whose `plan_shape_of` is not `free` (GH-471): a single-subtask arm's costs are
    not the free shape's, and the reference a plan is judged against must not move with them."""
    costs: dict[int, list[float]] = {}
    pairs: list[Pair] = []
    sizes: list[tuple[size_budget.PlanSize, size_budget.ActualSize]] = []
    for document in documents:
        if plan_shape_of(document) != FREE:
            continue
        planned = size_budget.PlanSize.from_frontmatter(document)
        actual = size_budget.ActualSize.from_frontmatter(document)
        if planned is not None and actual is not None:
            sizes.append((planned, actual))
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
                    declared_estimate(estimate),
                    cost,
                    int(estimate["files"]),
                    None if files is None else int(files),
                )
            )
    buckets = {
        key: Bucket(len(values), statistics.median(values), _p90(sorted(values)))
        for key, values in sorted(costs.items())
    }
    return History(buckets, tuple(pairs[-PAIRS:]), tuple(sizes))


def size_ratio(history: History) -> tuple[float, float] | None:
    """The floor on the declared hand-written size once the history proves it (GH-592): the
    p90 of the actual over the planned size, as `(files, lines)`, each at least 1.0. `None`
    while fewer than `size_budget.PROVEN_PAIRS` tasks recorded both sizes -- the caller then
    uses `size_budget.UNPROVEN_RATIO` for both. A task that planned no hand-written file or
    no line has no ratio and is skipped."""
    usable = [
        (planned, actual)
        for planned, actual in history.sizes
        if planned.hand_files > 0 and planned.hand_lines > 0
    ]
    if len(usable) < size_budget.PROVEN_PAIRS:
        return None
    files = sorted(actual.hand_files / planned.hand_files for planned, actual in usable)
    lines = sorted(actual.hand_lines / planned.hand_lines for planned, actual in usable)
    return max(1.0, _p90(files)), max(1.0, _p90(lines))


def reference_usd(history: History, n_subtasks: int) -> float | None:
    """The p90 of the bucket of this many subtasks, when it has at least `MIN_BUCKET`
    tasks; `None` otherwise (a thin bucket is no reference)."""
    bucket = history.buckets.get(_bucket_of(n_subtasks))
    return bucket.p90 if bucket and bucket.n >= MIN_BUCKET else None


def bucket_median(history: History, n_subtasks: int) -> float | None:
    """The median cost of the bucket, under the same `MIN_BUCKET` rule as the reference."""
    bucket = history.buckets.get(_bucket_of(n_subtasks))
    return bucket.median if bucket and bucket.n >= MIN_BUCKET else None


def estimate_floor(history: History, spent_usd: float, n_subtasks: int | None) -> Floor | None:
    """The larger of the spent floor and half the bucket median, with the one that sets it
    (`spent` on a tie); `None` when no floor binds. `n_subtasks=None` is a tracker-only plan:
    the spent floor only."""
    median = None if n_subtasks is None else bucket_median(history, n_subtasks)
    half = 0.0 if median is None else 0.5 * median
    if max(spent_usd, half) <= 0:
        return None
    return Floor(spent_usd, "spent") if spent_usd >= half else Floor(half, "median")


def raised_estimate(
    declared_usd: float, spent_usd: float, history: History, n_subtasks: int | None
) -> Raise | None:
    """The estimate a plan runs under when its declared figure is below a floor, `None` when
    it clears both. Below what was spent the figure cannot be a total (it is the remaining
    work priced alone), so it becomes `spent + declared`, and never less than the bucket
    median; below half the median it becomes the median. Raising to a prediction and not to
    the bare floor keeps the task from hitting the estimate cap (twice the estimate) at once."""
    floor = estimate_floor(history, spent_usd, n_subtasks)
    if floor is None or declared_usd >= floor.usd:
        return None
    median = None if n_subtasks is None else bucket_median(history, n_subtasks)
    if declared_usd < spent_usd:
        return Raise(declared_usd, max(spent_usd + declared_usd, median or 0.0), "spent")
    return Raise(declared_usd, median or floor.usd, "median")


def declared_estimate(estimate: Mapping[str, Any]) -> float:
    """The figure the Tech Lead wrote, for every calibration reader: `declared_usd` when the
    system raised the estimate, else `cost_usd`."""
    declared = estimate.get("declared_usd")
    return float(estimate["cost_usd"] if declared is None else declared)


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
