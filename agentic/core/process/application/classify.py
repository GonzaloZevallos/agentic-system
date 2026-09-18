"""PjM classifies the task (README.md, 2 and 4)."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain.roles import PJM
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .ports import Ports
from .run_role import finish, run_role


async def classify(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    output, patch = await run_role(state, PJM, cfg, ports)
    if output is not None:
        task_type = output.data["type"]
        patch["type"] = task_type
        if output.data["confidence"] == "low":
            # Low confidence is an escalation, not a guess. PjM does not re-route
            # while a task waits on the human (README.md, 3).
            patch["escalated"] = f"PjM could not classify with confidence: {output.data['reason']}"
            patch["status"] = WAITING_ON_HUMAN
        elif task_type == "bug":
            # Of the three paths of README §2, the bug path is still the one nobody
            # built: the task left the inbox and the system cannot carry it, so the
            # human must see it. A platform enhancement goes to the plan from here,
            # which is the routing's business and not this node's (issue #46).
            patch["escalated"] = f"{task_type} path not implemented yet"
            patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)
