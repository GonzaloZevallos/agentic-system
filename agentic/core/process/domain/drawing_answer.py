"""The owner's answers to drawing departures and the escalation that asks them (GH-766), pure.

`unanswered` and `applicable` are the only rule of which row an answer reaches;
`escalation_due` and `escalation_patch` the only place the escalation of departures is decided
and worded; `prompt_cause` the only reading of which drawing cause a pause carries; and
`section_lines` the only rendering of the rows a role reads. Imports only the domain's `task`,
`rules`, `drawing` and `escalation_prompts`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from . import drawing, escalation_prompts, rules, task

Cause = Literal["drawing_departure", "drawing_cap"]


def unanswered(state: task.TaskState) -> list[int]:
    """The numbers of the departure rows with no answer yet, sorted."""
    return sorted(r["number"] for r in state.get("drawing_departures", []) if r["answer"] is None)


def applicable(
    state: task.TaskState, given: Mapping[int, drawing.Verdict]
) -> tuple[Mapping[int, drawing.Verdict], frozenset[int]]:
    """Split `given` into the verdicts that reach an open row and the numbers ignored (no such
    row, or the row is already answered). The only rule of which row an answer reaches."""
    open_rows = set(unanswered(state))
    return (
        {n: v for n, v in given.items() if n in open_rows},
        frozenset(n for n in given if n not in open_rows),
    )


@dataclass(frozen=True)
class Due:
    """The escalation is due: round `round`, cause `drawing_departure`, asking `new`."""

    round: int
    new: Sequence[drawing.Departure]
    cause: Literal["drawing_departure"] = "drawing_departure"


@dataclass(frozen=True)
class Exhausted:
    """The cap of rounds was reached: round `round`, cause `drawing_cap`; `new` is still
    recorded, never dropped."""

    round: int
    new: Sequence[drawing.Departure]
    cause: Literal["drawing_cap"] = "drawing_cap"


def escalation_due(state: task.TaskState, new: Sequence[drawing.Departure]) -> Due | Exhausted:
    """The next round is the previous plus one (0 when none): `Due` while the previous round is
    under `drawing.DEPARTURE_ESCALATIONS_MAX` (rounds 1..3), `Exhausted` from round 4 on."""
    previous = state.get("drawing_escalation", {}).get("round", 0)
    if previous < drawing.DEPARTURE_ESCALATIONS_MAX:
        return Due(round=previous + 1, new=new)
    return Exhausted(round=previous + 1, new=new)


def _departure(row: task.DepartureRecord) -> drawing.Departure:
    return drawing.Departure(
        drawing=row["drawing"],
        element=row["element"],
        shows=row["shows"],
        would_ship=row["would_ship"],
        recommendation=row["recommendation"],
        reason=row["reason"],
        raised_by=row["raised_by"],
    )


def _history(due: Exhausted, rows: Sequence[task.DepartureRecord]) -> str:
    answered = sum(1 for r in rows if r["answer"] is not None)
    return (
        f"The task escalated drawing departures in {due.round - 1} rounds (the cap is "
        f"{drawing.DEPARTURE_ESCALATIONS_MAX}); {answered} departures are answered and "
        f"{len(rows) - answered} still open. Recommendation: close the issue, or rethink the "
        f"task so it stops departing from the approved drawings."
    )


def _reason(
    due: Due | Exhausted, rows: Sequence[task.DepartureRecord], contradicted: frozenset[int]
) -> str:
    lines = [_history(due, rows)] if isinstance(due, Exhausted) else []
    for row in sorted(rows, key=lambda r: r["number"]):
        if row["answer"] is not None:
            continue
        n = row["number"]
        lines.append(drawing.departure_question(_departure(row), n))
        if n in contradicted:
            lines.append(f"Drawing {n} got both accept and reject in one comment; answer it once.")
    return "\n".join(lines)


def prompt_lines(rows: Sequence[task.DepartureRecord], issue: int) -> list[str]:
    """The `agentic resume --drawing` commands of the open rows in number order, accept then
    reject each, with `issue` and the number filled. The only place those commands are built."""
    return [
        command.format(issue=str(issue)).replace("<n>", str(row["number"]))
        for row in sorted(rows, key=lambda r: r["number"])
        if row["answer"] is None
        for command in (escalation_prompts.DRAWING_ACCEPT, escalation_prompts.DRAWING_REJECT)
    ]


def escalation_patch(
    state: task.TaskState,
    departures: Sequence[drawing.Departure],
    at: str,
    *,
    contradicted: frozenset[int] = frozenset(),
) -> dict[str, object]:
    """The patch that escalates `departures` (and every row still open) to the owner: the new
    rows recorded (also when the cap is reached, never dropped), the round, and a `questions`
    escalation worded here (the commands are `prompt_lines`, shown by the one that renders the
    comment). Raises `ValueError` when no number remains unanswered.

    Called only from the system's serialized turn, never from a branch node: it reads the
    round the whole task has reached."""
    due = escalation_due(state, departures)
    new_rows = task.departure_records(state.get("drawing_departures"), departures, at)
    rows = task.add_drawing_departures(state.get("drawing_departures"), new_rows)
    numbers = sorted(r["number"] for r in rows if r["answer"] is None)
    if not numbers:
        raise ValueError("no departure is left unanswered: there is nothing to escalate")
    escalation = rules.escalation(
        "questions",
        _reason(due, rows, contradicted),
        spec=state.get("spec"),
        spec_cause="drawing" if rules.spec_delivery(state) else None,
    )
    return {
        **escalation,
        "drawing_departures": new_rows,
        "drawing_escalation": task.DrawingEscalation(
            round=due.round, numbers=numbers, cause=due.cause
        ),
    }


def prompt_cause(state: task.TaskState) -> Cause | None:
    """The cause the prompt of a pause carries: the recorded one, only while the pause is a
    `questions` one that is not on tracker items and some open row was asked. The landing's
    `api_break_missing` passes its own cause and wins."""
    record = state.get("drawing_escalation")
    if (
        record
        and state.get("escalation_class") == "questions"
        and not rules.tracker_item_pause(state)
        and set(unanswered(state)) & set(record["numbers"])
    ):
        return record["cause"]
    return None


def _flat(text: str) -> str:
    one = " ".join(text.split())
    limit = task.ASSUMPTION_MAX_CHARS
    return one if len(one) <= limit else one[: limit - 1] + "…"


def section_lines(rows: Sequence[task.DepartureRecord]) -> list[str]:
    """The lines a role reads for `rows`: one for an answered row, four for an open one (its
    text fields), every field with line breaks collapsed and cut to
    `task.ASSUMPTION_MAX_CHARS`. Never the internal key."""
    lines: list[str] = []
    for row in rows:
        n = row["number"]
        answer = row["answer"]
        if answer is not None:
            lines.append(
                f"#{n} · {_flat(row['drawing'])} · {_flat(row['element'])} · "
                f"{answer['verdict']} ({answer['by']}, {answer['at']})"
            )
            continue
        fields = (
            ("shows", row["shows"]),
            ("would_ship", row["would_ship"]),
            ("recommendation", row["recommendation"]),
            ("reason", row["reason"]),
        )
        lines.extend(f"#{n} · {name}: {_flat(text)}" for name, text in fields)
    return lines


def rejected_keys(state: task.TaskState) -> Mapping[str, int]:
    """Each key whose row the owner rejected, with that row's number: a role may not raise it
    again as a departure."""
    return {
        r["key"]: r["number"]
        for r in state.get("drawing_departures", [])
        if r["answer"] is not None and r["answer"]["verdict"] == "reject"
    }


def must_match(state: task.TaskState, seat_slug: str) -> Mapping[str, int]:
    """Each key the owner rejected on a departure `seat_slug` raised and that has had no fix
    pass yet, with its number: the seat's next answer must carry a `matches` check for it."""
    return {
        r["key"]: r["number"]
        for r in state.get("drawing_departures", [])
        if r["answer"] is not None
        and r["answer"]["verdict"] == "reject"
        and r["raised_by"] == seat_slug
        and r["fix_passes"] == 0
    }
