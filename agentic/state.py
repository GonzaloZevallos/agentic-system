"""Graph state. Imports nothing from the project (DISENO.md, 14).

Reducers:
- `add` accumulates cost across roles.
- `merge` is the per-key override for dict fields. Resetting a pair's counter
  when the human answers is an explicit `{pair: 0}` update, not a copied dict.
- `append` keeps every role failure, retried or not, for PjM's metrics.

`flags` has no reducer on purpose: the validated set replaces the proposed one.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

Status = Literal["open", "defined", "returned", "definition_ready", "waiting_on_human"]


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


class TaskState(TaskInput, total=False):
    type: str
    status: Status
    current_role: str
    flags: dict[str, bool]
    escalated: str | None
    # One Markdown section per role, keyed by role name. Holds the latest version.
    sections: Annotated[dict[str, str], merge]
    # Returns used per validator→thinker pair, keyed like "PM→Product Analyst".
    returns: Annotated[dict[str, int], merge]
    failures: Annotated[list[Failure], operator.add]
    # Short hash of each prompt that ran, keyed by role slug (ADR-0005).
    prompts: Annotated[dict[str, str], merge]
    cost_usd: Annotated[float, add]
