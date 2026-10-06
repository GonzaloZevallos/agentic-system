"""The coverage guard (GH-174): the in-turn refusal of a Developer delivery whose
coverage map leaves out one of the plan's invariants. A `SchemaGuard`-shaped callable
with its own counter, composed after the schema guard by `run_with_retry`
(`delivery_check`) so the presubmit only ever sees a delivery both accepted. Never a
`check=` hook: it denies in the turn and never raises, so it cannot become an
`AttemptFailed` or a role failure."""

from __future__ import annotations

from typing import Any

from ..domain import coverage


class InvariantCoverageGuard:
    """Built once per role run with the number of plan invariants. Denies with
    `coverage.missing_invariants`'s own reason at most `coverage.REFUSALS` times; past
    that the delivery passes with the gap, and the Developer's section says which
    invariants were not mapped (`coverage.render`)."""

    def __init__(self, count: int):
        self.count = count
        self.refusals = 0

    def __call__(self, tool_input: dict[str, Any]) -> str | None:
        reason = coverage.missing_invariants(tool_input, self.count)
        if reason is None or self.refusals >= coverage.REFUSALS:
            return None
        self.refusals += 1
        return reason
