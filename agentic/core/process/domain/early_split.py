"""The early size check's decision: a cheap verdict, right after PM approves a PRD, on whether the
task is so clearly over the size budget that it should split before the design ladder and the
Tech Lead.

Pure, and read by no node, edge or router yet. `verdict` is the closed decision on a declared
size. Whether the check is still due is `rules.early_size_due`, and the only reader of the record
the check leaves in the state (`early_split`) is `rules.early_measured`. Every number of the
budget lives in `size_budget`; none is written here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from . import size_budget
from .size_budget import PlanSize
from .task import EarlySplitCause

Cause = EarlySplitCause


@dataclass(frozen=True)
class Verdict:
    """What the early check decided and why: `split` or `go`, and one cause of the closed set."""

    verdict: Literal["split", "go"]
    cause: Cause


def verdict(size: PlanSize | None, parts_ok: bool) -> Verdict:
    """The decision on a declared `size`, the first row that holds:

    | # | size                                   | verdict | cause          |
    |---|----------------------------------------|---------|----------------|
    | 1 | unreadable (`None`, or a negative field) | go    | sizer_failed   |
    | 2 | not over any cap                       | go      | fits           |
    | 3 | over a cap, not clearly over           | go      | close          |
    | 4 | clearly over, `parts_ok`               | split   | over           |
    | 5 | clearly over, not `parts_ok`           | go      | parts_refused  |

    `parts_ok` (the parts a split would write were accepted) is read only in rows 4 and 5."""
    if size is None or min(size.hand_files, size.hand_lines, size.test_lines) < 0:
        return Verdict("go", "sizer_failed")
    if not size_budget.crossed(size):
        return Verdict("go", "fits")
    if not size_budget.clearly_over(size):
        return Verdict("go", "close")
    if parts_ok:
        return Verdict("split", "over")
    return Verdict("go", "parts_refused")
