"""Which stage of planning a task takes (GH-472, ADR-0086).

Pure functions and the only place that compares the `plan_review` arm or the route names:
`plan.py` calls them and decides nothing itself. `critic` is the arm that plans cold and has
one adversarial critic review the draft; a task whose estimate is large still gets the full
interview (ADR-0014)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

Route = Literal["interview", "critic"]
CriticState = Literal["ran", "failed", "skipped"]


def route(plan_review: str, pre_estimate_usd: float | None, threshold: float) -> Route:
    """The route before the plan exists. The interview when the switch says so, when the
    sizer failed (`None`) or when the pre-estimate reaches `threshold`; the critic otherwise."""
    if plan_review == "interview" or pre_estimate_usd is None or pre_estimate_usd >= threshold:
        return "interview"
    return "critic"


def regate(draft_estimate_usd: float, threshold: float) -> Route:
    """The route once the cold draft has an estimate of its own: a draft that reaches
    `threshold` was misrouted and goes to the interview after all."""
    return "interview" if draft_estimate_usd >= threshold else "critic"


def takes_critic(route: Route) -> bool:
    """Whether `route` is the critic's: the one question `plan.py` asks of a route."""
    return route == "critic"


def sizes(assignment: Mapping[str, Any] | None, plan_review: str) -> bool:
    """Whether the sizer runs for this task. An assignment (ADR-0081) that names `plan_review`
    sizes every arm, so the control is measured on the same scale; without one, only the
    critic arm does."""
    switches = assignment.get("switches") if assignment is not None else None
    if isinstance(switches, Mapping):
        return "plan_review" in switches
    return plan_review == "critic"


def after_promotion(has_interview_section: bool) -> Literal["resume", "critic"]:
    """The planning stage of a task re-planned after a promotion (GH-591): the interview
    session that already exists resumes (with two rounds of margin), or the critic runs once.
    Never a new interview: a promotion revises the plan, it does not redo it."""
    return "resume" if has_interview_section else "critic"
