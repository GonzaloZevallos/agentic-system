"""The task and its state, as the graph carries it. Imports nothing from the project.

Reducers:
- `add` accumulates cost across roles.
- `merge` is the per-key override for dict fields. Resetting a pair's counter
  when the human answers is an explicit `{pair: 0}` update, not a copied dict.
- `operator.add` keeps every role failure, retried or not, and every role run,
  for PjM's metrics and for reopening a role's session.

`flags` has no reducer on purpose: the validated set replaces the proposed one.

`apply` folds a partial patch into a state with those same reducers. The graph
engine does the same on its side; use cases need it to save the document with
the reducers already applied.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict, get_args, get_origin, get_type_hints

Status = Literal[
    "open",
    "defined",
    "returned",
    "definition_ready",
    "planned",
    "developing",
    "ready_for_review",
    "waiting_on_human",
]
WAITING_ON_HUMAN: Status = "waiting_on_human"
# The system's own subtask: the integrator joins the others on the task branch
# (ADR-0011). Only when there is more than one.
INTEGRATION = "integration"
SubtaskStatus = Literal["pending", "delivered", "approved", "returned"]


def add(current: float, new: float) -> float:
    return current + new


def last[T](current: T, new: T) -> T:
    """The latest write wins. Developers running in parallel each stamp the role and
    the status; without a reducer the graph refuses two writes in one step."""
    return new


def merge[T](current: dict[str, T] | None, update: dict[str, T] | None) -> dict[str, T]:
    return {**(current or {}), **(update or {})}


class TaskInput(TypedDict):
    """What enters the graph. `id` is also the thread id (ADR-0003)."""

    id: str
    title: str
    description: str


class Failure(TypedDict):
    role: str
    attempt: int
    error: str
    # A failed attempt still cost money; PjM's metrics need it.
    cost_usd: float
    # The session the attempt left, if the harness got as far as opening one: the
    # retry continues it, and `claude -r <id>` reopens it for the human.
    session_id: str


class RoleRun(TypedDict):
    """One successful run of a role. `section` is what it wrote (a role may write
    several: one per subtask, one per interview); `version` numbers the deliveries of
    that section, so a superseded `<section> (vN)` maps back to its run."""

    role: str
    section: str
    version: int
    session_id: str
    model: str
    cost_usd: float
    turns: int


class Subtask(TypedDict, total=False):
    """A part of the task assigned to one Developer (README.md, 4). Lives in its own
    branch; the pair Tech Lead→Developer counts returns per subtask."""

    id: str
    title: str
    brief: str
    seams: list[str]
    depends_on: list[str]
    status: SubtaskStatus
    branch: str
    # Commit the branch started from: the review diffs from it.
    base: str
    # The last review's reason, what the Developer fixes on a return.
    reason: str


class TaskState(TaskInput, total=False):
    type: str
    status: Annotated[Status, last]
    current_role: Annotated[str, last]
    flags: dict[str, bool]
    escalated: Annotated[str | None, last]
    # One Markdown section per role, keyed by role name, holding the latest delivery.
    # A delivery superseded by a return stays under "<role> (vN)": the document is
    # the raw record of the task, and PjM escalates with the history (ADR-0006).
    sections: Annotated[dict[str, str], merge]
    # Returns used per pair, keyed by `Pair.key`, like "PM→Product Analyst".
    returns: Annotated[dict[str, int], merge]
    failures: Annotated[list[Failure], operator.add]
    runs: Annotated[list[RoleRun], operator.add]
    # Short hash of each prompt that ran, keyed by role slug (ADR-0005).
    prompts: Annotated[dict[str, str], merge]
    cost_usd: Annotated[float, add]
    # The technical stage (ADR-0011): subtasks by id, each Developer patches its own.
    subtasks: Annotated[dict[str, Subtask], merge]
    # The task branch, and the pull request once delivered.
    branch: str
    pull_request: str | None
    # What an interviewee could not decide (ADR-0014); escalates with the task.
    questions_for_human: list[str]
    # Which subtask a `develop` node is running: set by the fan-out, never persisted.
    subtask_id: str


def _reducers() -> dict[str, object]:
    hints = get_type_hints(TaskState, include_extras=True)
    return {
        name: get_args(hint)[1] for name, hint in hints.items() if get_origin(hint) is Annotated
    }


REDUCERS = _reducers()


def apply(state: TaskState, patch: TaskState) -> TaskState:
    """`state` with `patch` folded in, reducer by reducer; plain fields are overridden."""
    merged: dict = dict(state)
    for key, value in patch.items():
        reducer = REDUCERS.get(key)
        if reducer is None or key not in merged:
            merged[key] = value
        else:
            merged[key] = reducer(merged[key], value)  # type: ignore[operator]
    return merged  # type: ignore[return-value]
