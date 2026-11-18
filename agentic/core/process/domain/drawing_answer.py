"""The owner's answers to drawing departures and the escalation that asks them (GH-766), pure.

`unanswered` and `applicable` are the only rule of which row an answer reaches;
`escalation_due` and `escalation_patch` the only place the escalation of departures is decided
and worded; `prompt_cause` the only reading of which drawing cause a pause carries; and
`section_lines` the only rendering of the rows a role reads; `classify` the only rule that
crosses a `departs` check with `departures` and with `drawing_departures`; `plan_split` the only
reading of the owner's verdict on a departure the plan declares; `plan_resume` and `resume_patch`
the only composition of the patch that resumes a pause the owner answered. Imports only the
domain's `task`, `rules`, `drawing`, `escalation_prompts` and `schemas`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from . import drawing, escalation_prompts, rules, task
from .schemas import DrawingCheck, DrawingDeparture

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
    stage: Literal["plan", "delivery"] = "delivery",
) -> task.DrawingEscalationPatch:
    """The patch that escalates `departures` (and every row still open) to the owner: the new
    rows recorded (also when the cap is reached, never dropped), the round, and a `questions`
    escalation worded here (the commands are `prompt_lines`, shown by the one that renders the
    comment). Raises `ValueError` when no number remains unanswered.

    `stage` says which gate asked: `plan` is written to the record, `delivery` (the default) is
    what an absent one reads as.

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
    record = task.DrawingEscalation(round=due.round, numbers=numbers, cause=due.cause)
    if stage == "plan":
        record["stage"] = "plan"
    return {**escalation, "drawing_departures": new_rows, "drawing_escalation": record}


@dataclass(frozen=True)
class PlanSplit:
    """The departures a plan declares, by the owner's verdict on each: `accepted`, `rejected`,
    and `open` (no row yet, or a row with no answer), each deduplicated by key in order of
    appearance. `rejected_numbers` maps the key of every rejected one to its number, which is
    what the plan gate takes; the key never reaches a text."""

    accepted: tuple[drawing.Departure, ...]
    rejected: tuple[drawing.Departure, ...]
    open: tuple[drawing.Departure, ...]
    rejected_numbers: Mapping[str, int]


def plan_split(state: task.TaskState, departures: Sequence[drawing.Departure]) -> PlanSplit:
    """Split `departures` by the verdict held in the `drawing_departures` rows, compared by
    `drawing.departure_key`. The only place the verdict of a departure of the plan is read."""
    rows = {r["key"]: r for r in state.get("drawing_departures", [])}
    accepted: dict[str, drawing.Departure] = {}
    rejected: dict[str, drawing.Departure] = {}
    open_: dict[str, drawing.Departure] = {}
    numbers: dict[str, int] = {}
    seen: set[str] = set()
    for d in departures:
        key = drawing.departure_key(d.drawing, d.element)
        if key in seen:
            continue
        seen.add(key)
        row = rows.get(key)
        answer = row["answer"] if row is not None else None
        if row is None or answer is None:
            open_[key] = d
        elif answer["verdict"] == "accept":
            accepted[key] = d
        else:
            rejected[key] = d
            numbers[key] = row["number"]
    return PlanSplit(
        accepted=tuple(accepted.values()),
        rejected=tuple(rejected.values()),
        open=tuple(open_.values()),
        rejected_numbers=MappingProxyType(numbers),
    )


def plan_resume(state: task.TaskState, answered: Sequence[task.DepartureRecord]) -> bool:
    """Whether the owner's answer reopens the plan: the pause is a live drawing one
    (`prompt_cause`), the resumption answered at least one row, and the gate that asked was the
    plan's. A stale `plan` record under another pause never does."""
    record = state.get("drawing_escalation")
    return (
        prompt_cause(state) is not None
        and bool(answered)
        and record is not None
        and record.get("stage", "delivery") == "plan"
    )


def resume_patch(
    state: task.TaskState,
    decision: str | None,
    estimate: float | None,
    answered: Sequence[task.DepartureRecord],
    *,
    at: str,
) -> dict[str, Any]:
    """The patch that resumes a paused task given the rows the owner `answered` (none for any
    other pause): `rules.resumed`, told by `plan_resume` whether the plan is owed, plus the
    answered rows when there are some. The only composition of that patch and the only caller
    of `plan_resume`, so the only source of `rules.resumed`'s `drawing_plan`."""
    patch = rules.resumed(
        state, decision, estimate, at=at, drawing_plan=plan_resume(state, answered)
    )
    if answered:
        patch["drawing_departures"] = list(answered)
    return patch


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


Outcome = Literal["raise", "settled_accept", "fix_due", "return_due"]


class ClassifyMismatch(ValueError):
    """The checks and the departures of a final review do not belong together. `cause` is
    `departs_unraised` (a `departs` check no departure raises and no row answered) or
    `departure_without_check` (a departure no `departs` check names)."""

    def __init__(
        self, cause: Literal["departs_unraised", "departure_without_check"], identity: str
    ) -> None:
        self.cause = cause
        self.identity = identity
        if cause == "departs_unraised":
            message = (
                f"drawing {identity!r} has a `departs` check but no departure: add a "
                f"departure for it to `departures`, or change the check's verdict"
            )
        else:
            message = (
                f"drawing {identity!r} has a departure but no `departs` check: add a "
                f"`departs` drawing check for it, or drop the departure"
            )
        super().__init__(message)


@dataclass(frozen=True)
class Classified:
    """What `classify` decided: `raised` the departures with no row yet, `outcomes` one per
    `departs` check keyed by `drawing.departure_key`, and `open` the checks that still block
    (the `unverified` ones and the `departs` ones not `settled_accept`), in input order."""

    raised: list[drawing.Departure]
    outcomes: dict[str, Outcome]
    open: list[DrawingCheck]


def _check_identity(check: DrawingCheck) -> str:
    if check.drawing is None or check.element is None:
        raise ValueError(f"the departs check {check.ref!r} names no drawing or element")
    return drawing.departure_key(check.drawing, check.element)


def _outcome(row: task.DepartureRecord) -> Outcome:
    answer = row["answer"]
    if answer is None:
        return "raise"
    if answer["verdict"] == "accept":
        return "settled_accept"
    return "fix_due" if row["fix_passes"] == 0 else "return_due"


def classify(
    state: task.TaskState,
    checks: Sequence[DrawingCheck],
    departures: Sequence[DrawingDeparture],
) -> Classified:
    """The only rule that crosses a `departs` check with `departures` and `drawing_departures`.

    The outcome of a `departs` check depends on the row of its key: no row is `raise` (and
    its departure joins `raised`); an unanswered row is `raise` too, never duplicated; an
    accepted one is `settled_accept`; a rejected one is `fix_due` while it has had no fix
    pass and `return_due` after. Raises `ClassifyMismatch` for a `departs` check with no
    departure and no answered row, and for a departure with no `departs` check."""
    rows = {r["key"]: r for r in state.get("drawing_departures", [])}
    entries: dict[str, DrawingDeparture] = {}
    for declared in departures:
        entries.setdefault(drawing.departure_key(declared.drawing, declared.element), declared)
    named = {_check_identity(c) for c in checks if c.verdict == "departs"}
    for identity in entries:
        if identity not in named:
            raise ClassifyMismatch("departure_without_check", identity)
    raised: dict[str, drawing.Departure] = {}
    outcomes: dict[str, Outcome] = {}
    for check in checks:
        if check.verdict != "departs":
            continue
        identity = _check_identity(check)
        row = rows.get(identity)
        if row is not None and row["answer"] is not None:
            outcomes[identity] = _outcome(row)
            continue
        entry = entries.get(identity)
        if entry is None:
            raise ClassifyMismatch("departs_unraised", identity)
        outcomes[identity] = "raise"
        if row is None:
            raised.setdefault(identity, drawing.Departure(**entry.model_dump()))
    return Classified(
        raised=list(raised.values()),
        outcomes=outcomes,
        open=[
            c
            for c in checks
            if drawing.row_is_open(
                c.verdict,
                c.verdict == "departs" and outcomes[_check_identity(c)] == "settled_accept",
            )
        ],
    )


_GRAVITY: Mapping[str, int] = MappingProxyType({"matches": 0, "unverified": 1, "departs": 2})


def merge_checks(ux: Sequence[DrawingCheck], ui: Sequence[DrawingCheck]) -> list[DrawingCheck]:
    """The checks of the two final leads as one row per `ref` (the image): the gravest verdict
    wins, `departs` over `unverified` over `matches`, whatever the order of the arguments; at
    equal gravity the first seen stays. The only place that decides which verdict wins."""
    merged: dict[str, DrawingCheck] = {}
    for check in (*ux, *ui):
        held = merged.get(check.ref)
        if held is None or _GRAVITY[check.verdict] > _GRAVITY[held.verdict]:
            merged[check.ref] = check
    return list(merged.values())


def fix_pass_rows(
    state: task.TaskState, outcomes: Mapping[str, Outcome]
) -> list[task.DepartureRecord]:
    """The rows of the `fix_due` outcomes with `fix_passes` at least 1: the return that asks for
    the fix is its first pass, so the next rejected pass of the row is `return_due`."""
    return [
        task.DepartureRecord(**{**row, "fix_passes": max(row["fix_passes"], 1)})
        for row in state.get("drawing_departures", [])
        if outcomes.get(row["key"]) == "fix_due"
    ]


def fold_raised(state: task.TaskState, merged: task.TaskState, at: str) -> task.TaskState:
    """`merged` (the patch a verifier set returns, on the snapshot `state`) without the
    transient `drawing_raised` the final leads left: the escalation of those departures built by
    `escalation_patch`, applied on top of `merged` so a sibling's deltas survive (ADR-0084).

    Decided by whether the key is present, never by whether the list is empty: a raise with no
    new departure still escalates the row left open. Without the key `merged` is returned as it
    is. When a sibling already escalated, this pass's departures are dropped: they are found
    again on the head the resume verifies, never remembered. A raise always leaves an open
    number, so the `ValueError` of `escalation_patch` is unreachable here and is not caught."""
    if "drawing_raised" not in merged:
        return merged
    folded = merged.copy()
    raised = folded.pop("drawing_raised")
    held = task.apply(state, folded)
    if held.get("escalated"):
        return folded
    return task.apply(folded, escalation_patch(held, raised, at))
