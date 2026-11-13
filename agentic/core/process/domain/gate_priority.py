"""Which `CheckPriority` a gate asks with (GH-680).

The head of the landing order unblocks every pull request behind it, so its checks must not
queue behind other runs' subtask checks (a `subtask` request never takes the core reserve,
`check_schedule`). This module is the one place that picks the priority from the step; the
gates built in `develop()` and in the landing steps ask it instead of naming a priority.
"""

from __future__ import annotations

from .check_schedule import CheckPriority
from .task import INTEGRATION


def gate_priority(unit: str, in_landing: bool) -> CheckPriority:
    """The priority of the delivery gate and the `run_check` presubmit of one `develop()` attempt.

    `in_landing` (a live landing pass, `rules.landing_pass_live`) gives `LAND` for any unit: the
    integrator or a real subtask the pass reopened. Otherwise the integration unit gives
    `INTEGRATION` and anything else `SUBTASK`.

    The criterion is a *live landing pass*, not *the head of the landing order*: a task that is
    not the head but keeps an open landing entry with a returned unit (after a manual release, or
    after the head yielded) also asks `LAND`. That is a known risk, bounded to one task and to
    the length of that pass. The pre-PR important-fix pass (`before_pr`) is not a landing pass."""
    if in_landing:
        return CheckPriority.LAND
    if unit == INTEGRATION:
        return CheckPriority.INTEGRATION
    return CheckPriority.SUBTASK


def landing_step_priority() -> CheckPriority:
    """The priority of every landing step that builds its own gate outside `develop()`
    (the gate of `apply_adr_decision`): always `LAND`."""
    return CheckPriority.LAND
