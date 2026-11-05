"""The plan route (GH-472): which stage of planning a task takes. Pure decisions, so the
plan node only calls them."""

import pytest

from agentic.core.process.domain.plan_route import regate, route, sizes

THRESHOLD = 55.0


def test_the_interview_switch_always_routes_to_the_interview():
    assert route("interview", 1.0, THRESHOLD) == "interview"


def test_a_failed_sizer_routes_to_the_interview():
    assert route("critic", None, THRESHOLD) == "interview"


@pytest.mark.parametrize(
    ("estimate", "expected"),
    [(54.99, "critic"), (55.0, "interview"), (55.01, "interview")],
    ids=["below", "equal", "above"],
)
def test_the_critic_arm_routes_by_the_threshold(estimate, expected):
    assert route("critic", estimate, THRESHOLD) == expected


@pytest.mark.parametrize(
    ("estimate", "expected"),
    [(10.0, "critic"), (55.0, "interview"), (80.0, "interview")],
    ids=["below", "equal", "above"],
)
def test_the_draft_estimate_regates_at_the_threshold(estimate, expected):
    assert regate(estimate, THRESHOLD) == expected


@pytest.mark.parametrize(
    ("assignment", "plan_review", "expected"),
    [
        ({"experiment": "e", "switches": {"plan_review": "interview"}}, "interview", True),
        ({"experiment": "e", "switches": {"plan_review": "critic"}}, "interview", True),
        ({"experiment": "e", "switches": {"review_mode": "light"}}, "critic", False),
        (None, "critic", True),
        (None, "interview", False),
        ({"experiment": "e"}, "critic", True),
    ],
    ids=[
        "assignment-names-interview",
        "assignment-names-critic",
        "assignment-without-the-switch",
        "no-assignment-critic",
        "no-assignment-interview",
        "assignment-without-switches",
    ],
)
def test_the_sizer_runs_when_the_assignment_carries_the_switch(assignment, plan_review, expected):
    assert sizes(assignment, plan_review) is expected
