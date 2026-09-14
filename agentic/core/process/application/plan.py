"""The technical solution (README.md, 4; ADR-0014): the ephemeral Tech Lead
interviews Tech Lead, who then writes the plan from its own session. The ADRs the
interview leaves are committed on the task branch (ADR-0011); the subtasks enter the
state pending, each to get its branch when a Developer starts it."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import schemas
from ..domain.roles import INTERVIEW_SOLUTION, TECH_LEAD
from ..domain.task import Subtask, TaskState, apply
from .checkouts import task_checkout
from .interview import interview
from .ports import Ports
from .run_role import finish, run_role


async def plan(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    task = task_checkout(state, ports)
    transcript, patch = await interview(state, INTERVIEW_SOLUTION, cfg, ports, cwd=task.path)
    ports.workspace.commit_all(task.path, f"{state['id']}: solution interview")
    if transcript is None:
        return finish(state, patch, ports)
    output, written = await run_role(
        apply(state, patch),
        TECH_LEAD,
        cfg,
        ports,
        check=schemas.plan_check,
        cwd=task.path,
        resume=transcript.sessions.get(INTERVIEW_SOLUTION.author_side.slug),
    )
    if output is not None:
        written["subtasks"] = {s["id"]: _pending(s) for s in output.data["subtasks"]}
        written["status"] = "planned"
    return finish(state, apply(patch, written), ports)


def _pending(item: dict) -> Subtask:
    return {
        "id": item["id"],
        "title": item["title"],
        "brief": item["brief"],
        "seams": item["seams"],
        "depends_on": item["depends_on"],
        "status": "pending",
    }
