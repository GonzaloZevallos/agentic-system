"""The `questions` rows for a drawing departure (GH-766 part 2a): present, shaped, unselected."""

from typing import get_args

from agentic.core.process.domain.escalation_prompts import (
    CLOSE_UNPLANNED,
    ESCALATION_PROMPTS,
    AskedCause,
    prompt_for,
)
from agentic.core.process.domain.task import MechanicalCause

_DEPARTURE = ESCALATION_PROMPTS[("questions", "drawing_departure")]
_CAP = ESCALATION_PROMPTS[("questions", "drawing_cap")]


def test_both_rows_tell_the_owner_the_comment_line_and_offer_the_drawing_commands():
    for question, recommended, options in (_DEPARTURE, _CAP):
        assert "Drawing <n>: accept" in question and "Drawing <n>: reject" in question
        assert recommended in options
        assert "agentic resume --issue {issue} --drawing <n>=accept" in options
        assert "agentic resume --issue {issue} --drawing <n>=reject" in options


def test_only_the_cap_row_offers_to_close_the_issue():
    assert CLOSE_UNPLANNED not in _DEPARTURE[2]
    assert CLOSE_UNPLANNED in _CAP[2]


def test_the_rows_are_reached_through_prompt_for():
    assert prompt_for("questions", "drawing_departure") == _DEPARTURE
    assert prompt_for("questions", "drawing_cap") == _CAP


def test_an_asked_cause_is_never_a_mechanical_one():
    assert {"drawing_departure", "drawing_cap"} <= set(get_args(AskedCause))
    assert not set(get_args(AskedCause)) & set(get_args(MechanicalCause))
