"""The speculative start of the full tier (GH-465): pure rules, no I/O.

`full_checks` publishes the integrated head to `agentic-ci/<id>` and waits for CI only after
every unit was approved. The final unit's review may publish that head first, so CI runs while
the Tech Lead reads it. Nothing is skipped: the verdict is still `ci.full_tier_verdict`'s, over
the same sha; only when the run starts changes. The state keeps the push fact
(`TaskState.ci_speculation`), never a verdict.

Three questions, each answered here and nowhere else: whether a review should push
(`push_due`), whether `full_checks` may count on the run already pushed (`consumable`), and
whether GitHub never started a run for it (`event_lost`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agentic.core.process.domain import rules
from agentic.core.process.domain.task import TaskState


def push_due(
    state: TaskState,
    unit: str,
    *,
    ci_names: Sequence[str],
    tiered: bool,
    untrusted: bool | None,
    head: str,
) -> bool:
    """Whether the review of `unit` at `head` publishes it to the CI ref before the Tech Lead
    reads it: `unit` is the final one (`rules.reviewed_unit_name`), the product declares `ci:`
    and `delivery_checks`, the head is trusted (`rules.ci_untrusted` is `False`; `True` and
    `None` both refuse), the task is not escalated, has no pull request, and has no passed
    `full_checks` on this head. A product with `ci_dispatch` is not excluded: its trusted heads
    speculate like any other."""
    if not ci_names or not tiered or untrusted is not False:
        return False
    if unit != rules.reviewed_unit_name(state):
        return False
    if state.get("escalated") or state.get("pull_request"):
        return False
    record = state.get("full_checks")
    return not (record and record["passed"] and rules.same_commit(record["head"], head))


def consumable(state: TaskState, head: str) -> bool:
    """Whether the run a review already pushed stands for `head`: the recorded push names a
    whole commit id and the same commit as `head`. Reads only `ci_speculation`; the delivery's
    own `pushed_head` never stands for it."""
    record = state.get("ci_speculation")
    if not record:
        return False
    pushed = record["head"]
    return rules.full_commit_id(pushed) and rules.same_commit(pushed, head)


def event_lost(pushed_at: str, now: datetime, grace_s: float, read_has_run: bool) -> bool:
    """Whether GitHub never started a run for a push: none is seen and `grace_s` seconds have
    passed since `pushed_at`. `now` is handed in, never read here. A `pushed_at` that is not an
    ISO instant with an offset cannot be measured, so the wait goes on as it does today."""
    if read_has_run:
        return False
    try:
        pushed = datetime.fromisoformat(pushed_at)
    except ValueError:
        return False
    if pushed.tzinfo is None or now.tzinfo is None:
        return False
    return (now - pushed).total_seconds() >= grace_s
