"""Rules of the process that the use cases apply (README.md, 3 and 6).

Pure functions over the task state, so each one is a unit test and not a graph run.
"""

from __future__ import annotations

from .roles import Pair
from .task import INTEGRATION, FindingCategory, Return, ReturnCategory, TaskState

# The category the system stamps on PM's returns of the definition (ADR-0016).
DEFINITION_RETURN: ReturnCategory = "definition"


def remaining_returns(state: TaskState, pair: Pair, limit: int) -> int:
    """Returns `pair` may still use before PjM escalates."""
    return limit - state.get("returns", {}).get(pair.key, 0)


def record_return(state: TaskState, pair: Pair) -> dict[str, int]:
    """The `returns` patch after one more return of `pair`."""
    return {pair.key: state.get("returns", {}).get(pair.key, 0) + 1}


def return_entry(pair: Pair, category: ReturnCategory, reason: str) -> Return:
    """One entry of the return history: who returned, in which category, and the reason,
    so README §3's "same motive between the same roles" is computable."""
    return {"pair": pair.key, "category": category, "reason": reason}


def return_category(findings: list[dict]) -> FindingCategory | None:
    """The verdict of a review, derived (ADR-0016): the category of the return, the
    first blocking finding's since the reviewer lists them most severe first, or None
    when nothing blocks and the review approves."""
    return next((f["category"] for f in findings if f["severity"] == "blocking"), None)


def limit_reached(pair: Pair, limit: int, reason: str) -> str:
    """The escalation text when `pair` used up its returns: the pair, the limit and the
    validator's reason, written for the human."""
    return f"{pair.key} hit {limit} returns: {reason}"


def next_version(state: TaskState, section: str) -> int:
    """The number this delivery of the section gets: one more than its runs so far.
    Older runs without `section` wrote the section named after their role."""
    runs = state.get("runs", [])
    return 1 + sum(1 for r in runs if r.get("section", r["role"]) == section)


def superseded(section: str, version: int) -> str:
    """Section name a delivery keeps once a return replaces it."""
    return f"{section} (v{version})"


def last_session(state: TaskState, role_name: str, section: str) -> str | None:
    """The session id of the latest run of `role_name` that wrote `section`, to resume
    it; None when that role never wrote there."""
    for run in reversed(state.get("runs", [])):
        if run["role"] == role_name and run.get("section", run["role"]) == section:
            return run.get("session_id") or None
    return None


def sections_for(state: TaskState, section: str, text: str) -> dict[str, str]:
    """The new section, plus its previous delivery as history on a return."""
    sections = {section: text}
    previous = state.get("sections", {}).get(section)
    if previous is not None:
        sections[superseded(section, next_version(state, section) - 1)] = previous
    return sections


# Subtasks (ADR-0011): each in its own branch, sections named after it.


def developer_section(subtask_id: str) -> str:
    return f"Developer: {subtask_id}"


def review_section(subtask_id: str) -> str:
    return f"Tech Lead: review {subtask_id}"


def task_branch(task_id: str) -> str:
    return f"agentic/{task_id}"


def subtask_branch(task_id: str, subtask_id: str) -> str:
    # A dot, not a slash: git cannot hold `agentic/GH-12` and `agentic/GH-12/api` at once.
    return f"{task_branch(task_id)}.{subtask_id}"


def ready_subtasks(state: TaskState) -> list[str]:
    """Subtasks to build now: pending or returned, with every dependency approved."""
    subtasks = state.get("subtasks", {})
    return [
        sid
        for sid, sub in subtasks.items()
        if sub.get("status") in ("pending", "returned")
        and all(
            subtasks.get(dep, {}).get("status") == "approved" for dep in sub.get("depends_on", [])
        )
    ]


def delivered_subtasks(state: TaskState) -> list[str]:
    return [
        sid for sid, sub in state.get("subtasks", {}).items() if sub.get("status") == "delivered"
    ]


def all_approved(state: TaskState) -> bool:
    subtasks = state.get("subtasks", {})
    return bool(subtasks) and all(sub.get("status") == "approved" for sub in subtasks.values())


def needs_integration(state: TaskState) -> bool:
    """Several subtasks approved and no integration yet: the integrator joins them."""
    subtasks = state.get("subtasks", {})
    return INTEGRATION not in subtasks and len(subtasks) > 1 and all_approved(state)


# Routing of the feature path (README.md, 2): PjM -> Product Analyst -> PM -> Tech Lead
# -> Developers -> Tech Lead -> delivery. `None` is the end of the run; the graph
# adapter maps names to its nodes and fans `develop` out per ready subtask.


def after_classify(state: TaskState) -> str | None:
    return None if state.get("escalated") else "define"


def after_define(state: TaskState) -> str | None:
    return None if state.get("escalated") else "validate"


def after_validate(state: TaskState) -> str | None:
    if state.get("escalated"):
        return None
    return {"returned": "define", "definition_ready": "plan"}.get(state.get("status", ""))


def after_plan(state: TaskState) -> str | None:
    return None if state.get("escalated") else "develop"


def after_develop(state: TaskState) -> str | None:
    return None if state.get("escalated") else "review"


def after_review(state: TaskState) -> str | None:
    if state.get("escalated"):
        return None
    if ready_subtasks(state):
        return "develop"
    return "deliver" if all_approved(state) else None


def after_deliver(state: TaskState) -> str | None:
    return None
