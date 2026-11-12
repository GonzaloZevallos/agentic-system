"""The `risk_hits` net in a fan-out branch (GH-591, part 2; glossary: Red de riesgo).

A system step between a Developer's delivery and its review: the subtask's diff paths are matched
against the product's `risk_areas` globs, with no model spend. A hit, or a diff that could not be
read, leaves a request on the subtask's own key; it never changes the path (serialized system
code does, through `task_path.promote`). The product's risk id and `unreadable` go only in the
request's `text`, never in a span attribute."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import task_path
from ..domain.task import TaskState
from .checkouts import task_base
from .ports import GitError, Ports
from .run_role import finish


def _verdict(state: TaskState, subtask_id: str, ports: Ports) -> task_path.RiskVerdict:
    subtask = state["subtasks"][subtask_id]
    base, branch = subtask.get("base"), subtask.get("branch")
    try:
        areas = [(area.id, area.paths) for area in ports.stack.risk_areas(task_base(state, ports))]
        touched = ports.workspace.touched_paths(base, branch) if base and branch else None
    except (GitError, ValueError, OSError):
        touched = None
        areas = []
    return task_path.risk_hits(areas, touched)


async def risk_net(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    """The branch step: `{}` when the diff touches no declared area, else the subtask carrying
    its `risk_area` request. A request already stored on the subtask stays (`record_request`)."""
    subtask_id = state["subtask_id"]
    subtask = state["subtasks"][subtask_id]
    verdict = _verdict(state, subtask_id, ports)
    if isinstance(verdict, task_path.RiskNone):
        return {}
    text = verdict.id if isinstance(verdict, task_path.RiskHit) else "unreadable"
    new: task_path.PromoteRequest = {"kind": "risk_area", "from_role": "system", "text": text}
    stored = task_path.record_request(subtask.get("promote_request"), new)
    patch: TaskState = {"subtasks": {subtask_id: {**subtask, "promote_request": stored}}}
    return finish(state, patch, ports)
