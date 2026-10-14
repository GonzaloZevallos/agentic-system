"""The landing steps that act without moving the head by themselves (GH-239): `fix`,
`rerun`, `file_followups_and_land` and `diagnose`. Inert in this subtask, like
`landing_sync`."""

from __future__ import annotations

from .landing_plan import LandingContext


def fix(context: LandingContext) -> None:
    context.log(f"agentic: land: {context.task_id}: fix is not built yet")


def rerun(context: LandingContext) -> None:
    context.log(f"agentic: land: {context.task_id}: rerun is not built yet")


def file_followups_and_land(context: LandingContext) -> None:
    context.log(f"agentic: land: {context.task_id}: file_followups_and_land is not built yet")


def diagnose(context: LandingContext) -> None:
    context.log(f"agentic: land: {context.task_id}: diagnose is not built yet")
