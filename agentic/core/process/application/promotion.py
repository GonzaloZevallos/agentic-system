"""How a role's `promote` request reaches the document (GH-591, part 2; glossary: Promoción).

A role never changes the path: it leaves a request that serialized system code applies through
`task_path.promote`. These are the three small writers around that: the request a fan-out branch
leaves on its own subtask, the one the plan node leaves at the top level, and the notice the
system adds to the asking role's section when `promote` refused (the owner label `path:light`).
Pure: each returns a value or a patch, none writes the document."""

from __future__ import annotations

from ..domain import task_path
from ..domain.task import Subtask, TaskState


def requested(subtask: Subtask, from_role: str, text: str | None) -> Subtask:
    """`subtask` with the request a role asked for (`text`, its `promote` line) recorded under
    its own key. Nothing asked, or a blank line, is `subtask` itself; a `risk_area` request
    already stored is never replaced (`task_path.record_request`)."""
    if not text or not text.strip():
        return subtask
    new: task_path.PromoteRequest = {"kind": "role", "from_role": from_role, "text": text}
    return {**subtask, "promote_request": task_path.record_request(subtask.get("promote_request"), new)}


def plan_requested(state: TaskState, from_role: str, text: str | None) -> TaskState:
    """The patch for the request the plan node's Tech Lead asked for: `{"plan_promote_request":
    ...}`, or `{}` when it asked nothing. The plan node is serialized, so the key needs no reducer."""
    if not text or not text.strip():
        return {}
    new: task_path.PromoteRequest = {"kind": "role", "from_role": from_role, "text": text}
    return {"plan_promote_request": task_path.record_request(state.get("plan_promote_request"), new)}


def refusal_notice(state: TaskState, section: str, notice: str | None) -> TaskState:
    """The patch that appends `notice`, the line `task_path.promote` returned when it applied
    nothing, to `section`, the section of the role that asked, so that role reads that a
    refused promotion is not asked for again. `{}` when there is no notice."""
    if not notice:
        return {}
    text = state.get("sections", {}).get(section, "").rstrip()
    return {"sections": {section: f"{text}\n\n{notice}" if text else notice}}
