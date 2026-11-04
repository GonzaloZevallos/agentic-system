"""The terminal status `evaluated` (GH-470, part 2): where an evaluation run ends instead of
`deliver`. Every closed map keyed by the status vocabulary must hold it, and the routing sends
a task carrying `evaluation` there after its full checks -- never a real task."""

from typing import get_args

from agentic.core.process.domain import board, rules
from agentic.core.process.domain.board import IssueFacts
from agentic.core.process.domain.task import Status

EVALUATION = {"issue": 7, "base": "a" * 40, "ci": "local"}


def _facts(status: str) -> IssueFacts:
    return IssueFacts(
        number=7,
        closed=False,
        closed_at=None,
        labels=frozenset({"agentic"}),
        live=False,
        blocker=None,
        document={"status": status},
        in_eval_set=False,
    )


def test_evaluated_is_part_of_the_status_vocabulary():
    assert "evaluated" in get_args(Status)


def test_every_status_of_the_vocabulary_is_known_to_the_board_map():
    # An unknown status reads "documento con estado desconocido": a status the map forgot.
    for status in get_args(Status):
        assert board.card(_facts(status)).bloqueo != board.UNKNOWN_STATUS, status


def test_evaluated_ends_the_run_and_has_a_rank_of_its_own_in_closeness():
    assert rules.run_ended("evaluated") is True
    assert "evaluated" in rules.CLOSENESS
    assert rules.CLOSENESS.index("evaluated") == rules.CLOSENESS.index("delivered") + 1
    assert rules.closeness({"status": "evaluated"}) == rules.CLOSENESS.index("evaluated")


def test_every_other_status_is_unchanged_by_the_new_terminal():
    ended = {s for s in get_args(Status) if rules.run_ended(s)}
    assert ended == {"ready_for_review", "discarded", "split", "tracker_only", "evaluated"}


def test_a_finished_evaluation_is_stamped_evaluated_with_its_clock():
    assert rules.finished("evaluated", "2026-11-04T10:00:00") == {
        "status": "evaluated",
        "finished_at": "2026-11-04T10:00:00",
    }


def test_finished_still_refuses_a_status_that_is_not_an_end_without_delivery():
    import pytest

    with pytest.raises(ValueError):
        rules.finished("developing", "2026-11-04T10:00:00")  # type: ignore[arg-type]
