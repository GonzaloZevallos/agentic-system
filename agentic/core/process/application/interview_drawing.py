"""The drawing policy of the definition interview (GH-766 part 3b): which check each seat gets,
which departures an accepted answer raises, and how they reach the state.

`interview.py` only calls this module. `check_for` is the only place that composes
`drawing.interviewer_check` and `drawing.interview_check` for a role; which seat gets which
is `drawing.DRAWING_SEATS`, never the shape of the data. A `Collector` keeps what an accepted
interviewee answer raised: the system writes `raised_by` (the slug of the seat that answered)
and the numbers, never the model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..domain import drawing, drawing_answer, task
from ..domain.design import Design
from ..domain.roles import Role
from ..domain.schemas import DrawingCheck, DrawingDeparture
from ..domain.task import TaskState
from .run_role import Check


def _refusing(reason_of: Callable[[dict[str, Any]], str | None]) -> Check:
    """The one adapter of a reason (`str | None`) into the `ValueError` `run_role` retries on;
    the text is the reason's own, nothing added."""

    def check(data: dict[str, Any]) -> None:
        reason = reason_of(data)
        if reason is not None:
            raise ValueError(reason)

    return check


def check_for(
    role: Role, design: Design | None, state: TaskState, visible: Sequence[int]
) -> Check | None:
    """The drawing check of `role`'s turn, `None` without a design or for a seat that is not in
    `drawing.DRAWING_SEATS`. `visible` is what the interviewer marked in this round; `state`
    already holds the fix passes this invocation recorded, so a key matched once is not asked
    again."""
    seat = drawing.DRAWING_SEATS.get(role.slug)
    if design is None or seat is None:
        return None
    if seat == "interviewer":
        return _refusing(
            lambda data: drawing.interviewer_check(
                data.get("visible", []), data["done"], data["markdown"]
            )
        )
    rejected = drawing_answer.rejected_keys(state)
    owed = drawing_answer.must_match(state, role.slug)
    return _refusing(
        lambda data: drawing.interview_check(
            visible,
            [DrawingCheck(**c) for c in data.get("drawing_checks", [])],
            [DrawingDeparture(**d) for d in data.get("departures", [])],
            design,
            rejected,
            owed,
        )
    )


@dataclass
class Collector:
    """What the accepted answers of this invocation raised: the departures not yet a row
    (one per key), in order. Empty and inert without a design."""

    design: Design | None
    new: dict[str, drawing.Departure] = field(default_factory=dict)

    def collect(self, role: Role, data: Mapping[str, Any], state: TaskState) -> TaskState:
        """Take the departures of an accepted answer of `role` and return the patch that gives
        the rows its `matches` checks answer their first fix pass (the reducer keeps the
        maximum, so a retake never asks for it twice). `state` holds the rows so far."""
        if self.design is None or drawing.DRAWING_SEATS.get(role.slug) != "interviewee":
            return {}
        held = {r["key"] for r in state.get("drawing_departures", [])}
        for raw in data.get("departures", []):
            key = drawing.departure_key(raw["drawing"], raw["element"])
            if key not in held and key not in self.new:
                self.new[key] = drawing.Departure(**{**raw, "raised_by": role.slug})
        owed = drawing_answer.must_match(state, role.slug)
        matched = {
            key
            for c in data.get("drawing_checks", [])
            if c["verdict"] == "matches"
            and (key := drawing.departure_key(c["drawing"], c["element"])) in owed
        }
        rows = [
            task.DepartureRecord(**{**row, "fix_passes": max(row["fix_passes"], 1)})
            for row in state.get("drawing_departures", [])
            if row["key"] in matched
        ]
        return {"drawing_departures": rows} if rows else {}

    def pending(self, state: TaskState) -> list[drawing.Departure]:
        """The departures raised and not yet a row of `state`."""
        held = {r["key"] for r in state.get("drawing_departures", [])}
        return [d for key, d in self.new.items() if key not in held]

    def due(self, state: TaskState) -> bool:
        """Whether the normal exit escalates: a departure raised here, or a row nobody has
        answered. Never without a design."""
        return self.design is not None and bool(
            self.pending(state) or drawing_answer.unanswered(state)
        )

    def rows(self, state: TaskState, at: str) -> TaskState:
        """The patch that keeps the pending departures as rows with no answer, for the exit by
        failure: it composes no escalation, the next normal exit raises them."""
        pending = self.pending(state)
        if not pending:
            return {}
        return {
            "drawing_departures": task.departure_records(
                state.get("drawing_departures"), pending, at
            )
        }
