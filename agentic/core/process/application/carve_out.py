"""The application of a depth-2 carve-out (GH-719, ADR-0093 addendum): `apply_carve` opens the
follow-up issue, re-points the later issues' `Blocked by:` lines onto it and comments on the
part, and `finish_carve` closes the record.

Idempotent the way `split.py` is, as a linear resume walk: the record `carve_out` of the task
document is saved before each effect (`data`, `fit` and `at` before any Inbox or Tracker call,
`issue_url` the moment the issue exists, `repointed_at` only after a complete re-point pass,
`commented_at` after the comment on the part), and each call skips every step whose key the
record already holds. The follow-up's body carries the marker `carve_out.marker`, looked for
among the open issues before one is opened, so a run cut anywhere completes what is missing and
opens, re-points and comments nothing twice. A record with `outcome` is returned as written.

No caller exists until part 3 of GH-719, and `plan.py` must not name `apply_carve`. `apply_carve`
never writes `outcome: applied`: `finish_carve` is its only writer, and persisting its patch is
the caller's job. Until then the record has no `outcome`, so `carve_out_pending` stays true: that
is safe only because nothing calls this module yet.

Known limits: a follow-up closed while its record was lost is not found among the open issues,
so it can be opened again; a dependent whose comment fails after its body was edited never gets
that comment on resume (`append_blocker` then finds nothing left to change, and `_repoint` is
shared with the split sweep); a comment written by hand holding the exact `Carved out:` line
suppresses the comment on the part; a dependent that fails the re-point post-condition pauses
the task on every resume, and the pause carries only a count; a lost `commented_at` can repeat
the comment once when the dedupe read cannot see it; and nothing resumes these pauses until
part 3, which must re-enter the carve-out node on a `failure` resume whose record has
`issue_url` and no `outcome`.

What reaches the document, `pause_log`, `escalation_cause` and every tracker comment is only
fixed phrases, numbers and exception class names, plus the model's own carve-out text in the
comment on the part. The reasons of a refusal may quote model text and are never persisted; a
port exception's own message is never copied either (it can echo what was sent), which is why
`_stopped` differs on purpose from `split._stopped`."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import Any, TypedDict, Unpack, cast

from ..domain import carve_out, document, rules
from ..domain.carve_out import CarveOut
from ..domain.roles import TECH_LEAD
from ..domain.schemas import FitDecision
from ..domain.task import WAITING_ON_HUMAN, CarveRecord, TaskState, apply
from . import split_sweep
from .ports import Ports
from .run_role import finish

# The label of the queue itself: `copied_labels` adds it and the repository always has it.
_QUEUE_LABEL = "agentic"
_LEFT = "the follow-up, its re-point and the comment on the part are left"
_LEFT_REPOINT = "its re-point and the comment on the part are left"
_LEFT_COMMENT = "the comment on the part is left"


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
    """Applies `data`: opens the follow-up (or finds the one a cut run opened), re-points the
    dependents of the part onto it and comments on the part; the patch it returns carries the
    whole record. `fit` is the judgement the carve-out answers and `fits` the caller's size
    check. A record that already holds `data` is resumed from alone: `data`, `fit` and `fits` are
    then ignored, nothing is revalidated and every step the record shows done is skipped. Raises
    `CarveRefused` before any port call when the carve-out is refused. Never writes `outcome:
    applied`: that is `finish_carve`'s."""
    task_id = state["id"]
    current = state
    record = state.get("carve_out", {})
    if "outcome" in record:
        return _carried(current)
    if "data" not in record:
        if found := refusals(data["carve"], data["plan_output"], fits):
            raise CarveRefused(found)
        if not carve_out.writes_tracker(state):
            return _save(current, ports, outcome="refused", cause="evaluation")
        current = _record(current, ports, data=dict(data), fit=_figures(fit), at=ports.now())
    saved = _read(current["carve_out"])
    if saved is None:
        return _stopped(current, "reading the saved record", "ValueError", ports)
    part = ports.inbox.issue_of(task_id)
    if part is None:
        if "issue_url" in current["carve_out"]:
            return _stopped(current, "finding the issue of the part", "no issue number", ports)
        return _save(current, ports, outcome="refused", cause="no_issue")
    if "issue_url" not in current["carve_out"]:
        try:
            bodies = ports.inbox.open_issues()
        except (ValueError, OSError) as exc:
            return _stopped(current, "reading the open issues", type(exc).__name__, ports)
        wanted = (task_id, part)
        url = next((found for found, body in bodies if carve_out.marked_by(body) == wanted), None)
        if url is None:
            url, current = _open(current, saved, part, ports)
            if url is None:
                return current
        current = _record(current, ports, issue_url=url)
    follow_up = _follow_up_number(current["carve_out"]["issue_url"])
    if follow_up is None:
        return _stopped(current, "reading the saved record", "ValueError", ports)
    if "repointed_at" not in current["carve_out"]:
        current, paused = _repoint(current, part, follow_up, ports)
        if paused is not None:
            return paused
    if "commented_at" not in current["carve_out"]:
        current, paused = _comment(current, saved, part, follow_up, ports)
        if paused is not None:
            return paused
    return _carried(current)


def finish_carve(state: TaskState) -> tuple[TaskState, dict[str, object]]:
    """Closes a complete record: `(patch, remainder)`, the patch holding the record with
    `outcome: applied` and the remainder being that of the SAVED plan (`data`), whatever plan
    `state` may carry. Pure: it saves nothing, and persisting the patch is the caller's job.
    Raises `ValueError`, with a fixed message, for a record that already has an `outcome`, lacks
    a non-empty `issue_url`, `repointed_at` or `commented_at`, or whose `data` cannot be read."""
    record = state.get("carve_out", {})
    if "outcome" in record:
        raise ValueError("carve-out: the record already has an outcome")
    for key in ("issue_url", "repointed_at", "commented_at"):
        value = record.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"carve-out: the record is not complete, it lacks {key}")
    saved = _read(record)
    if saved is None:
        raise ValueError("carve-out: the saved record cannot be read")
    try:
        rest = carve_out.remainder(saved["plan_output"], saved["carve"])
    except (KeyError, TypeError):
        raise ValueError("carve-out: the remainder of the saved plan cannot be built") from None
    patch = _base(state)
    patch["carve_out"] = {**record, "outcome": "applied"}
    return patch, rest


def _follow_up_number(url: str) -> int | None:
    """The issue number at the tail of the follow-up's URL. The same two lines as
    `split_sweep._issue_number`, which is private there and stays so."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _repoint(
    state: TaskState, part: int, follow_up: int, ports: Ports
) -> tuple[TaskState, TaskState | None]:
    """Step 4: `(state with repointed_at saved, None)` after a complete pass over the open
    issues, or `(state, the paused patch)` when the issues cannot be read or the pass was not
    complete; no comment on the part is posted then."""
    try:
        refs = ports.inbox.open_issue_refs()
    except (ValueError, OSError) as exc:
        paused = _stopped(
            state, "reading the open issues", type(exc).__name__, ports, _LEFT_REPOINT
        )
        return state, paused
    result = split_sweep.repoint_dependents(
        ports,
        part,
        [part, follow_up],
        refs,
        {follow_up, part},
        rewrite=carve_out.append_blocker,
        comment=_dependent_comment(follow_up),
    )
    if not result.complete:
        detail = f"{result.failed} failed" if result.failed else f"stopped by {result.stopped_by}"
        return state, _stopped(state, "re-pointing the dependents", detail, ports, _LEFT_REPOINT)
    return _record(state, ports, repointed_at=ports.now()), None


def _dependent_comment(follow_up: int) -> Callable[[int, Sequence[int]], str]:
    """The text each re-pointed dependent is told: numbers only."""

    def text(parent: int, targets: Sequence[int]) -> str:
        return (
            f"#{parent} now also waits on the carved-out follow-up #{follow_up}: this issue is "
            "unblocked when both are closed."
        )

    return text


def _comment(
    state: TaskState, saved: CarveData, part: int, follow_up: int, ports: Ports
) -> tuple[TaskState, TaskState | None]:
    """Step 5: one comment on the part, posted unless one of its comments already holds the
    exact `Carved out:` line (a line after `strip`, never a substring), then `commented_at`."""
    text = _part_comment(saved, follow_up, state["carve_out"].get("labels_unread") is True)
    task_id = state["id"]
    try:
        existing = ports.tracker.issue_comments(task_id)
    except (ValueError, OSError) as exc:
        paused = _stopped(
            state, "reading the comments of the part", type(exc).__name__, ports, _LEFT_COMMENT
        )
        return state, paused
    first = text.splitlines()[0]
    if not any(line.strip() == first for comment in existing for line in comment.splitlines()):
        try:
            ports.tracker.comment_on_issue(str(part), text)
        except (ValueError, OSError) as exc:
            paused = _stopped(
                state, "commenting on the part", type(exc).__name__, ports, _LEFT_COMMENT
            )
            return state, paused
    return _record(state, ports, commented_at=ports.now()), None


def _part_comment(saved: CarveData, follow_up: int, labels_unread: bool) -> str:
    """The comment on the part: the `Carved out:` line, the moved steps and criteria, the
    redeclared size and, when the part's labels could not be read, a line that says so."""
    carve = saved["carve"]
    steps = [str(step) for step in carve["steps"]]
    moved = [s for s in saved["plan_output"].get("subtasks", []) if s.get("id") in carve["steps"]]
    lines = [f"Carved out: {', '.join(steps)} -> #{follow_up}", ""]
    lines += [f"- {sub.get('id')}: {sub.get('title')}" for sub in moved]
    if carve.get("moved_criteria"):
        lines += ["", "Moved acceptance criteria:", *(f"- {c}" for c in carve["moved_criteria"])]
    lines += [
        "",
        f"Redeclared size of this issue: {carve.get('hand_files')} hand files, "
        f"{carve.get('hand_lines')} hand lines, {carve.get('test_lines')} test lines, estimated "
        f"cost {carve.get('estimated_cost_usd')} USD.",
    ]
    if labels_unread:
        lines += [
            "",
            "The labels of this issue could not be read, so the follow-up got only `agentic`.",
        ]
    return "\n".join(lines)


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
        return None, _stopped(current, "creating a label", type(exc).__name__, ports)
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
        return None, _stopped(current, "opening the follow-up", type(exc).__name__, ports)


def _read(record: CarveRecord) -> CarveData | None:
    """The saved `data` as `CarveData`, or `None` when the document holds a record that does not
    have its shape (the document only checks that `data` is a mapping)."""
    raw = record.get("data", {})
    if not isinstance(raw, dict):
        return None
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


def _stopped(
    state: TaskState, step: str, detail: str, ports: Ports, left: str = _LEFT
) -> TaskState:
    """The pause of a tracker failure, the shape `split._stopped` gives: `current_role`,
    `status`, the `failure` escalation and its `pause_log` entry stamped `ports.now()`. `detail`
    is built only from literals, numbers and exception class names, never an exception's text;
    `left` says what the resume still has to do."""
    paused = _paused(
        state,
        rules.logged(
            {
                "current_role": TECH_LEAD.name,
                "status": WAITING_ON_HUMAN,
                **rules.escalation(
                    "failure",
                    f"carve-out: {step} failed ({detail}); {left}, resume to finish",
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
