"""The owner's `Decision:` comment on a task stopped by hand (GH-873, apostrophe #726): which
comments count, where the decision goes back to, and the patch that consumes it. Pure: no ports,
no clock, no I/O. It is inert until GH-873 part 2 wires it: nothing outside tests imports it.

This module is the only reader of the `Decision:` prefix in a comment (display strings in prompts
apart; `spec_approval.read_approval` keeps its own reading of the spec-draft answer) and the only
one that compares a timestamp against `owner_decision_seen`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from . import restart, rules, task
from .task import OwnerDecisionCause, TaskState

# Its own copy of `spec_approval.DECISION`: importing that module would pull in `spec_delivery`,
# and the reverse would make `spec_approval` import this one. A test pins the two together.
DECISION = "Decision:"

# The comments the system posts after it has read the issue's answers (`escalate.ESCALATED`,
# `resume.RESUMED`; a test pins them). Retake and poller notes such as `Resumed by the poller:`
# are deliberately not here: they read nothing, so a decision before them is still unread.
FLOOR_PREFIXES = ("Escalated: ", "Resumed: ")


class Comment(Protocol):
    """What `newer` reads of an issue comment."""

    @property
    def body(self) -> str: ...

    @property
    def created_at(self) -> str: ...


def _is_decision(body: str) -> bool:
    """The grammar: the first non-blank line of the body starts with `Decision:` at column 0 (the
    body stripped of blank lines around it, but not of the indent of its first line)."""
    return next((line for line in body.splitlines() if line.strip()), "").startswith(DECISION)


def _instant(text: str | None) -> datetime | None:
    """An aware ISO 8601 instant (`Z` accepted), `None` when it does not parse or is naive."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def newer[C: Comment](
    comments: Sequence[C],
    seen: str | None,
    system_prefixes: Sequence[str] = FLOOR_PREFIXES,
) -> list[C]:
    """The `Decision:` comments posted strictly after the floor, in list order. The floor is
    `seen` (the newest decision already consumed) and, with no usable `seen`, the newest
    parseable `created_at` among the comments that start with a system prefix (the maximum, not
    the last in list order), or none at all. A comment whose timestamp does not parse (or is
    naive) never counts; an unparseable `seen` reads as absent. A decision in the same second as
    the floor is not newer: that loss is accepted. The clock is never read."""
    floor = _instant(seen)
    if floor is None:
        floors = [
            at
            for c in comments
            if c.body.strip().startswith(tuple(system_prefixes))
            and (at := _instant(c.created_at)) is not None
        ]
        floor = max(floors, default=None)
    kept: list[C] = []
    for comment in comments:
        at = _instant(comment.created_at)
        if at is None or not _is_decision(comment.body):
            continue
        if floor is None or at > floor:
            kept.append(comment)
    return kept


def reseed_due(
    state: TaskState,
    comments: Sequence[Comment],
    system_prefixes: Sequence[str] = FLOOR_PREFIXES,
) -> bool:
    """Whether an owner decision is waiting that `owner_decision_seen` has not consumed."""
    return bool(newer(comments, state.get("owner_decision_seen"), system_prefixes))


@dataclass(frozen=True)
class RouteVerdict:
    """Where a decision goes: `developer`, `pm`, or `escalate` (with the `cause`, set only then)."""

    route: Literal["developer", "pm", "escalate"]
    cause: OwnerDecisionCause | None = None


def decision_route(state: TaskState, task_id: str) -> RouteVerdict:
    """The first rule that matches: a `spec` is the owner's file (escalate, `spec_owned`); a
    human decision still pending (escalate, `owner_decision`); a PRD, so the decision may change
    the definition (`pm`); one unit, the reviewed one, on the task branch (`developer`); any
    other shape (escalate, `owner_decision`)."""
    if state.get("spec"):
        return RouteVerdict("escalate", "spec_owned")
    if not rules.no_human_decision_pending(state):
        return RouteVerdict("escalate", "owner_decision")
    if state.get("sections", {}).get(rules.PRODUCT_ANALYST.name):
        return RouteVerdict("pm")
    subtasks = state.get("subtasks", {})
    if len(subtasks) == 1:
        (name, sub) = next(iter(subtasks.items()))
        if name == rules.reviewed_unit_name(state) and sub.get("branch") == rules.task_branch(
            task_id
        ):
            return RouteVerdict("developer")
    return RouteVerdict("escalate", "owner_decision")


@dataclass(frozen=True)
class Consumed:
    """What a role run consumed: the `route` it took and `newest`, the `created_at` of the newest
    decision it was given."""

    route: Literal["developer", "pm"]
    newest: str


def _without_full_checks_rows(state: TaskState) -> TaskState:
    """`state` with no `full_checks:` finding: those rows carry the `full_checks` counter's version,
    not a review pass's, so they must not set the bar a re-created unit's review starts above,
    and `rules.findings_rows` never discards them for an owner decision."""
    kept = [
        f
        for f in state.get("findings", [])
        if not f["location"].startswith(rules.FULL_CHECKS_PREFIX)
    ]
    return {**state, "findings": kept}


def consumed_patch(state: TaskState, result: Consumed | None) -> dict[str, Any]:
    """The patch that records a decision as consumed. `None` gives `{}`. Every route moves
    `owner_decision_seen` to the later of its current value and `newest` (never back; an
    unparseable current value reads as absent) and clears `owner_decision_route`: this is the
    only writer of the first and the only clearer of the second.

    Route `pm` also resets the plan: `subtasks_reset()`, no `task_head`, `restart_pending` or
    `pending_close_docs`, no `plan_due`, every unit's open findings settled as `owner_decision`
    through `restart.settle_open` (a `full_checks:` row apart: only a green run closes it, and its
    version is no review pass), and `status: "returned"`. `define` reruns the whole
    definition interview unless the status is `returned` (it then rewrites over the existing
    transcript), and should the process die after the route is cleared, the plain status fork
    still reaches `define`, so PM cannot be skipped. `full_checks` is never cleared (its version
    counter would restart and new rows would read as dismissed), and `restarts` and
    `changes_requested` are not touched.

    Part 2 contract: merge this only into the patch of the role run that receives the decision
    text (`define` for `pm`, the integration Developer for `developer`), never into the resume
    patch."""
    if result is None:
        return {}
    current = state.get("owner_decision_seen")
    now = _instant(current)
    new = _instant(result.newest)
    seen = current if now is not None and (new is None or now >= new) else result.newest
    patch: dict[str, Any] = {"owner_decision_seen": seen}
    if state.get("owner_decision_route"):
        patch["owner_decision_route"] = None
    if result.route == "pm":
        patch.update(
            {
                "subtasks": task.subtasks_reset(),
                "task_head": None,
                "restart_pending": None,
                "pending_close_docs": None,
                "plan_due": False,
                "status": "returned",
                "restart_settled": restart.settle_open(
                    _without_full_checks_rows(state),
                    list(state.get("subtasks", {})),
                    "owner_decision",
                ),
            }
        )
    return patch
