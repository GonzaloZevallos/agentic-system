"""The `session_stalled` row (GH-978 part 3): `stalled_batch` reads only the failure entries just
before the last escalation entry of the pause log, and the row is reached through `prompt_for`."""

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.escalation_prompts import (
    CLOSE_UNPLANNED,
    ESCALATION_PROMPTS,
    RESUME,
    SESSION_STALLED_PROMPT,
    prompt_for,
    stalled_batch,
)


def _f(reason: str, section: str = "S") -> dict:
    return {"kind": "failure", "value": reason, "section": section}


ESC = {"kind": "escalation", "value": "failure"}
RESUMED = {"kind": "resumed", "value": "human"}
LAPSE = {"kind": "lapse", "value": "x"}


@pytest.mark.parametrize(
    "log",
    [
        [_f("stall"), _f("stall"), ESC],
        [_f("max_turns"), _f("stall"), _f("stall"), ESC],
        [_f("stall"), _f("stall"), ESC, LAPSE],
        [_f("stall", "A"), _f("stall", "B"), ESC],
    ],
)
def test_two_stalls_closing_the_batch_are_a_stalled_batch(log):
    assert stalled_batch(log) is True


@pytest.mark.parametrize(
    "log",
    [
        [_f("stall"), _f("schema"), ESC],
        [_f("stall"), _f("schema"), _f("stall"), ESC],
        [_f("stall"), RESUMED, _f("stall"), ESC],
        [_f("stall"), ESC, _f("stall"), ESC],
        [_f("stall"), _f("stall")],
        [_f("stall"), ESC],
        [],
    ],
)
def test_anything_else_is_not_a_stalled_batch(log):
    assert stalled_batch(log) is False


def test_the_anchor_is_the_last_escalation_entry():
    older = [_f("stall"), _f("stall"), ESC]
    assert stalled_batch([*older, _f("schema"), _f("schema"), ESC]) is False
    assert stalled_batch([_f("schema"), ESC, _f("stall"), _f("stall"), ESC]) is True


def test_the_row_is_reached_through_the_prompt_table_and_offers_resume_or_close():
    assert prompt_for("failure", "session_stalled") is SESSION_STALLED_PROMPT
    assert ESCALATION_PROMPTS[("failure", "session_stalled")] is SESSION_STALLED_PROMPT
    question, recommended, options = SESSION_STALLED_PROMPT
    assert recommended == RESUME and options == (RESUME, CLOSE_UNPLANNED)
    assert prompt_for("failure") != SESSION_STALLED_PROMPT


def test_two_stalls_stay_a_humans_pause_by_the_unchanged_rules():
    log = [_f("stall"), _f("stall"), ESC]
    assert rules.auto_resumable({"pause_log": log}) is None
    assert rules.pause_kind(log) == "human"
