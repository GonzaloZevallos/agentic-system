"""Opening the follow-up issue of a depth-2 carve-out (GH-719, ADR-0093 addendum): the first
three steps of `apply_carve`, which end once the follow-up exists and its URL is saved.

Idempotent the way `split.py` is: the record `carve_out` of the task document is saved before
each effect (`data`, `fit` and `at` before any Inbox or Tracker call, `issue_url` the moment the
issue exists), and the follow-up's body carries the marker `carve_out.marker`, looked for among
the open issues before one is opened, so a run cut anywhere completes what is missing and opens
nothing twice.

No caller exists until part 3 of GH-719, and `plan.py` must not name `apply_carve`. The record
is left without an `outcome`, so `carve_out_pending` stays true: that is safe only because
nothing calls this module yet. The re-point of the later parts' `Blocked by:` lines, the comment
on the part and `outcome: applied` are #737's, and `finish_carve` is the only writer of
`applied`.

Known limits of this part: a follow-up closed while its record was lost is not found among the
open issues, so it can be opened again; and a record that already holds `issue_url` is trusted
as written, with no look at the tracker.

What reaches the document, `pause_log` and `escalation_cause` is only fixed phrases, numbers and
exception class names. The reasons of a refusal may quote model text and are never persisted;
a port exception's own message is never copied either (it can echo what was sent), which is
why `_stopped` differs on purpose from `split._stopped`."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any, TypedDict, Unpack, cast

from ..domain import carve_out, document, rules
from ..domain.carve_out import CarveOut
from ..domain.roles import TECH_LEAD
from ..domain.schemas import FitDecision
from ..domain.task import WAITING_ON_HUMAN, CarveRecord, TaskState, apply
from .ports import Ports
from .run_role import finish

# The label of the queue itself: `copied_labels` adds it and the repository always has it.
_QUEUE_LABEL = "agentic"
_LEFT = "the follow-up, its re-point and the comment on the part are left"


class CarveRefused(Exception):
    """The carve-out was refused before any port was called. `reasons` may quote model text
    and must never be persisted or sent anywhere: `str(exc)` is only a fixed line with their
    number."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons: tuple[str, ...] = tuple(reasons)
        super().__init__(f"carve-out refused: {len(self.reasons)} reason(s)")


class CarveData(TypedDict):
    """What the record saves as `data`: the declared carve-out and the plan it was made for."""

    carve: CarveOut
    plan_output: dict[str, Any]


def refusals(
    data: CarveOut,
    plan_output: Mapping[str, object],
    fits: Callable[[Mapping[str, object]], bool],
) -> list[str]:
    """Every reason `data` is refused for `plan_output`; empty when it can be applied. `fits`
    is the caller's closure over `rules.estimate_fits`: no size number lives here."""
    found = carve_out.validate(data, plan_output)
    if (text := carve_out.text_refused(data["title"], data["body"])) is not None:
        found.append(text)
    if (rest := carve_out.remainder_refusal(plan_output, data, fits)) is not None:
        found.append(rest)
    return found


def apply_carve(
    state: TaskState,
    data: CarveData,
    ports: Ports,
    *,
    fit: FitDecision,
    fits: Callable[[Mapping[str, object]], bool],
) -> TaskState:
    """Opens the follow-up of `data` (or finds the one a cut run opened) and saves its URL; the
    patch it returns carries the whole record. `fit` is the judgement the carve-out answers and
    `fits` the caller's size check. A record that already holds `data` is resumed from alone:
    `data`, `fit` and `fits` are then ignored and nothing is revalidated. Raises `CarveRefused`
    before any port call when the carve-out is refused."""
    task_id = state["id"]
    current = state
    record = state.get("carve_out", {})
    if "outcome" in record or "issue_url" in record:
        return _carried(current)
    if "data" not in record:
        if found := refusals(data["carve"], data["plan_output"], fits):
            raise CarveRefused(found)
        if not carve_out.writes_tracker(state):
            return _save(current, ports, outcome="refused", cause="evaluation")
        current = _record(current, ports, data=dict(data), fit=_figures(fit), at=ports.now())
    saved = _read(current["carve_out"])
    if saved is None:
        return _stopped(current, "reading the saved record", ValueError(), ports)
    part = ports.inbox.issue_of(task_id)
    if part is None:
        return _save(current, ports, outcome="refused", cause="no_issue")
    try:
        bodies = ports.inbox.open_issues()
    except (ValueError, OSError) as exc:
        return _stopped(current, "reading the open issues", exc, ports)
    wanted = (task_id, part)
    url = next((found for found, body in bodies if carve_out.marked_by(body) == wanted), None)
    if url is None:
        url, current = _open(current, saved, part, ports)
        if url is None:
            return current
    return _save(current, ports, issue_url=url)


def _open(
    state: TaskState, saved: CarveData, part: int, ports: Ports
) -> tuple[str | None, TaskState]:
    """Opens the follow-up with the part's labels: `(url, state)`, or `(None, the paused
    state)` when the tracker failed. Never retried without labels."""
    task_id = state["id"]
    carve = saved["carve"]
    current = state
    try:
        labels = ports.tracker.issue_state(part).labels
    except (ValueError, OSError):
        labels = None
    unread = labels is None
    if unread != state.get("carve_out", {}).get("labels_unread", False):
        current = _record(current, ports, labels_unread=unread)
    copied = carve_out.copied_labels(labels)
    try:
        for label in copied:
            if label != _QUEUE_LABEL:
                ports.tracker.ensure_label(label)
    except (ValueError, OSError) as exc:
        return None, _stopped(current, "creating a label", exc, ports)
    steps = set(carve["steps"])
    extracted = [s for s in saved["plan_output"].get("subtasks", []) if s.get("id") in steps]
    body = carve_out.follow_up_body(
        carve["body"],
        extracted,
        part,
        f"Carved out of {task_id}, whose plan did not fit. The plan and its interview are in "
        f"`{document.relative_path(task_id)}`.",
        carve["kind"],
        task_id=task_id,
    )
    try:
        return ports.tracker.open_issue(carve["title"], body, labels=copied), current
    except (ValueError, OSError) as exc:
        return None, _stopped(current, "opening the follow-up", exc, ports)


def _read(record: CarveRecord) -> CarveData | None:
    """The saved `data` as `CarveData`, or `None` when the document holds a record that does not
    have its shape (the document only checks that `data` is a mapping)."""
    raw = record.get("data", {})
    carve, plan = raw.get("carve"), raw.get("plan_output")
    if not (isinstance(carve, dict) and isinstance(plan, dict)):
        return None
    if not all(key in carve for key in ("steps", "title", "body", "kind")):
        return None
    return cast(CarveData, raw)


def _figures(fit: FitDecision) -> dict[str, object]:
    """The figures of `fit` as plain data the document can keep (tuples become lists)."""
    figures: dict[str, object] = json.loads(json.dumps(asdict(fit)))
    return figures


def _record(state: TaskState, ports: Ports, **fields: Unpack[CarveRecord]) -> TaskState:
    """`state` with `fields` folded into its record, saved to the document before any effect."""
    patch = _base(state)
    patch["carve_out"] = fields
    finish(state, patch, ports)
    return apply(state, patch)


def _save(state: TaskState, ports: Ports, **fields: Unpack[CarveRecord]) -> TaskState:
    """Saves `fields` and answers the patch with the whole record."""
    return _carried(_record(state, ports, **fields))


def _carried(state: TaskState) -> TaskState:
    patch = _base(state)
    patch["carve_out"] = state.get("carve_out", {})
    return patch


def _stopped(state: TaskState, step: str, exc: Exception, ports: Ports) -> TaskState:
    """The pause of a tracker failure, the shape `split._stopped` gives: `current_role`,
    `status`, the `failure` escalation and its `pause_log` entry stamped `ports.now()`. It names
    the exception's class, never its text."""
    paused = _paused(
        state,
        rules.logged(
            {
                "current_role": TECH_LEAD.name,
                "status": WAITING_ON_HUMAN,
                **rules.escalation(
                    "failure",
                    f"carve-out: {step} failed ({type(exc).__name__}); {_LEFT}, resume to finish",
                ),
            },
            at=ports.now(),
        ),
    )
    return finish(state, paused, ports)


# `_paused` and `_base` are copied from `split.py`, which stays untouched: the pause that
# `rules.logged` composed, key by key into a typed patch, and the three keys `TaskInput`
# requires to type any patch.
def _paused(state: TaskState, logged: dict[str, Any]) -> TaskState:
    patch = _base(state)
    patch["current_role"] = logged["current_role"]
    patch["status"] = logged["status"]
    patch["escalated"] = logged["escalated"]
    patch["escalation_class"] = logged["escalation_class"]
    patch["escalation_cause"] = logged["escalation_cause"]
    patch["pause_log"] = logged["pause_log"]
    return patch


def _base(state: TaskState) -> TaskState:
    return {"id": state["id"], "title": state["title"], "description": state["description"]}
