"""Rules of the process that the use cases apply (README.md, 3 and 6).

Pure functions over the task state, so each one is a unit test and not a graph run.
"""

from __future__ import annotations

from .roles import Pair
from .task import TaskState


def remaining_returns(state: TaskState, pair: Pair, limit: int) -> int:
    """Returns `pair` may still use before PjM escalates."""
    return limit - state.get("returns", {}).get(pair.key, 0)


def record_return(state: TaskState, pair: Pair) -> dict[str, int]:
    """The `returns` patch after one more return of `pair`."""
    return {pair.key: state.get("returns", {}).get(pair.key, 0) + 1}


def limit_reached(pair: Pair, limit: int, reason: str) -> str:
    """The escalation text when `pair` used up its returns: the pair, the limit and the
    validator's reason, written for the human."""
    return f"{pair.key} hit {limit} returns: {reason}"


def next_version(state: TaskState, role_name: str) -> int:
    """The number this delivery of the role gets: one more than its runs so far."""
    return 1 + sum(1 for r in state.get("runs", []) if r["role"] == role_name)


def superseded(role_name: str, version: int) -> str:
    """Section name a delivery keeps once a return replaces it."""
    return f"{role_name} (v{version})"


def sections_for(state: TaskState, role_name: str, text: str) -> dict[str, str]:
    """The role's new section, plus its previous delivery as history on a return."""
    sections = {role_name: text}
    previous = state.get("sections", {}).get(role_name)
    if previous is not None:
        sections[superseded(role_name, next_version(state, role_name) - 1)] = previous
    return sections


# Routing of the feature path, first slice: PjM -> Product Analyst -> PM. `None` is
# the end of the run; the graph adapter maps names to its nodes.


def after_classify(state: TaskState) -> str | None:
    return None if state.get("escalated") else "define"


def after_define(state: TaskState) -> str | None:
    return None if state.get("escalated") else "validate"


def after_validate(state: TaskState) -> str | None:
    if state.get("escalated"):
        return None
    return "define" if state.get("status") == "returned" else None
