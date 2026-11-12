"""How a role's `promote` request reaches the document (GH-591, part 2; glossary: Promoción).

A role never changes the path: it leaves a request that serialized system code applies through
`task_path.promote`. These are the three small writers around that: the request a fan-out branch
leaves on its own subtask, the one the plan node leaves at the top level, and the notice the
system adds to the asking role's section when `promote` refused (the owner label `path:light`).
Pure: each returns a value or a patch, none writes the document."""

from __future__ import annotations

from ..domain import task_path
from ..domain.task import Subtask, TaskState, apply


def requested(subtask: Subtask, from_role: str, text: str | None) -> Subtask:
    """`subtask` with the request a role asked for (`text`, its `promote` line) recorded under
    its own key. Nothing asked, or a blank line, is `subtask` itself; a `risk_area` request
    already stored is never replaced (`task_path.record_request`)."""
    if not text or not text.strip():
        return subtask
    new: task_path.PromoteRequest = {"kind": "role", "from_role": from_role, "text": text}
    return {
        **subtask,
        "promote_request": task_path.record_request(subtask.get("promote_request"), new),
    }


def plan_requested(state: TaskState, from_role: str, text: str | None) -> TaskState:
    """The patch for the request the plan node's Tech Lead asked for (`plan_promote_request`),
    or `{}` when it asked nothing. The plan node is serialized, so the key needs no reducer."""
    if not text or not text.strip():
        return {}
    new: task_path.PromoteRequest = {"kind": "role", "from_role": from_role, "text": text}
    return {
        "plan_promote_request": task_path.record_request(state.get("plan_promote_request"), new)
    }


def refusal_notice(state: TaskState, section: str, notice: str | None) -> TaskState:
    """The patch that appends `notice`, the line `task_path.promote` returned when it applied
    nothing, to `section`, the section of the role that asked, so that role reads that a
    refused promotion is not asked for again. `{}` when there is no notice."""
    if not notice:
        return {}
    text = state.get("sections", {}).get(section, "").rstrip()
    return {"sections": {section: f"{text}\n\n{notice}" if text else notice}}


# The section a notice goes to when the asker has none (the system's `risk_hits` net): the plan's.
NOTICE_FALLBACK = "Tech Lead"


def applied(state: TaskState, at: str) -> TaskState:
    """The patch the serialized fan-in adds for every pending request (GH-591): the one
    `task_path.select_request` picks goes through `task_path.promote` once, with `at`, and EVERY
    request is cleared in the same patch, so a refused one (the owner label `path:light`) is not
    asked again. A refusal's notice goes to the asker's section. `{}` when nothing is pending."""
    subtasks = state.get("subtasks", {})
    requests = {
        sid: s["promote_request"] for sid, s in subtasks.items() if s.get("promote_request")
    }
    plan_request = state.get("plan_promote_request")
    if not requests and plan_request is None:
        return {}
    chosen = task_path.select_request(plan_request, requests)
    patch: TaskState = {}
    notice = None
    if chosen is not None:
        moved, notice = task_path.promote(
            state, chosen["from_role"], chosen["kind"], at, chosen["text"]
        )
        patch = dict(moved)
    if requests:
        patch["subtasks"] = {
            sid: {k: v for k, v in subtasks[sid].items() if k != "promote_request"}
            for sid in requests
        }
    if plan_request is not None:
        patch["plan_promote_request"] = None
    if chosen is not None and notice:
        asker = chosen["from_role"]
        section = asker if asker in state.get("sections", {}) else NOTICE_FALLBACK
        patch = apply(patch, refusal_notice(apply(state, patch), section, notice))
    return patch
