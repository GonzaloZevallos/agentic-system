"""The landing steps that move the pull request's head (GH-239): `sync_master` and
`re_review`. Inert in this subtask: each logs that it did nothing and changes nothing, so
the dispatch in `landing.land` is complete before any executor is. Wave 2 replaces the
bodies and adds the kinds to `landing_plan.LIVE_STEPS`."""

from __future__ import annotations

from .landing_plan import LandingContext


def sync_master(context: LandingContext) -> None:
    context.log(f"agentic: land: {context.task_id}: sync_master is not built yet")


def re_review(context: LandingContext) -> None:
    context.log(f"agentic: land: {context.task_id}: re_review is not built yet")
