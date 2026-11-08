"""`apply_adr_decision` (GH-544, addendum to ADR-0049): the landing step that applies an owner's
decision on an `escalate_adr` question. The step is chosen by `landing_step`, released by
`landing_plan.release`, and recorded by `landing.land` before it is dispatched here.

The executor is the subtask of GH-544 that follows this wiring; until it lands the step only
logs, so a released decision is recorded and dispatched and nothing is pushed."""

from __future__ import annotations

from .landing_plan import LandingContext


def apply_adr_decision(context: LandingContext) -> None:
    """Carry out the recorded `apply_adr_decision` of `context.head`. A stub: it logs."""
    context.log(
        f"agentic: land: {context.task_id}: {context.head} apply_adr_decision: "
        "dispatched, nothing applies it yet"
    )
