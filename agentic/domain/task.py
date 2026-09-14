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

Status = Literal["open", "defined", "returned", "definition_ready", "waiting_on_human"]
WAITING_ON_HUMAN: Status = "waiting_on_human"


def add(current: float, new: float) -> float:
    return current + new


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


class RoleRun(TypedDict):
    """One successful run of a role. `version` numbers that role's deliveries in the
    task, so a superseded section `<role> (vN)` maps back to its run."""

    role: str
    version: int
    session_id: str
    model: str
    cost_usd: float
    turns: int


class TaskState(TaskInput, total=False):
    type: str
    status: Status
    current_role: str
    flags: dict[str, bool]
    escalated: str | None
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
