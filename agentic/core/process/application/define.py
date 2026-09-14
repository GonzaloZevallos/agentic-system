"""The definition stage (README.md, 4; ADR-0014): the Product Analyst interviews PM,
then writes the behaviour definition from its own session of the interview. On a
return from PM the interview is not repeated: the Product Analyst rewrites over the
transcript it already has.

The interview runs in the task's checkout (ADR-0011): what it writes to the glossary
and the ADRs is committed there, so it travels with the code to the pull request."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain.roles import INTERVIEW_DEFINITION, PRODUCT_ANALYST
from ..domain.task import TaskState, apply
from .checkouts import task_checkout
from .interview import interview
from .ports import Ports
from .run_role import finish, run_role


async def define(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    task = task_checkout(state, ports)
    patch: TaskState = {"branch": task.branch}
    resume = None
    if state.get("status") != "returned":
        transcript, interviewed = await interview(
            state, INTERVIEW_DEFINITION, cfg, ports, cwd=task.path
        )
        ports.workspace.commit_all(task.path, f"{state['id']}: definition interview")
        patch = apply(patch, interviewed)
        if transcript is None:
            return finish(state, patch, ports)
        resume = transcript.sessions.get(INTERVIEW_DEFINITION.author_side.slug)
    output, written = await run_role(
        apply(state, patch), PRODUCT_ANALYST, cfg, ports, cwd=task.path, resume=resume
    )
    if output is not None:
        written["flags"] = output.data["flags"]
        written["status"] = "defined"
    # Two patches in a row fold like the graph would fold them: reducers add up.
    return finish(state, apply(patch, written), ports)
