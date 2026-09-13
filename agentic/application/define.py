"""Product Analyst writes the definition and proposes the flags (README.md, 4)."""

from __future__ import annotations

from ..domain.configuration import Configuration
from ..domain.roles import PRODUCT_ANALYST
from ..domain.task import TaskState
from .ports import Ports
from .run_role import finish, run_role


async def define(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    output, patch = await run_role(state, PRODUCT_ANALYST, cfg, ports)
    if output is not None:
        patch["flags"] = output.data["flags"]
        patch["status"] = "defined"
    return finish(state, patch, ports)
