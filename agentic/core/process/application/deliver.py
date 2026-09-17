"""The delivery to the human (ADR-0011): the task branch is pushed, a pull request
opened with the definition and the plan, and the issue told where both the pull
request and the task document are. The task is then ready for review: the human's
merge is the closing while the path has no QA.

No role runs here; it is the system talking to the human, which ADR-0001 allows."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import document
from ..domain.roles import DEVELOPER_INTEGRATOR, PJM, PM, TECH_LEAD
from ..domain.task import TaskState
from .checkouts import SetupFailed, task_checkout
from .ports import Ports
from .run_role import finish

# How the delivery comment starts: a resume looks past the system's comments.
READY = "Ready for review: "


async def deliver(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(DEVELOPER_INTEGRATOR), ports)
    ports.workspace.push(task.branch)
    url = ports.tracker.open_pull_request(state["id"], task.branch, state["title"], _body(state))
    ports.tracker.comment(
        state["id"],
        f"{READY}{url}\n\nTask document: `{document.relative_path(state['id'])}`",
    )
    ports.workspace.cleanup(state["id"])
    patch: TaskState = {
        "status": "ready_for_review",
        "pull_request": url,
        "branch": task.branch,
        "current_role": PJM.name,
    }
    return finish(state, patch, ports)


def _body(state: TaskState) -> str:
    """The definition and the plan, as the human reads them before the diff."""
    sections = state.get("sections", {})
    parts = [
        f"## {name}\n\n{sections[name]}" for name in (PM.name, TECH_LEAD.name) if name in sections
    ]
    return "\n\n".join(parts) or state["title"]
