"""PjM classifies the task (README.md, 2 and 4)."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import PJM
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .ports import Ports
from .preflight import product_name
from .run_role import finish, run_role


async def classify(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    # The declared name as data, like `{{date}}` (ADR-0019): `product_name` is the path
    # that answers `unknown` when the stack cannot be read, never the bare port call
    # whose `ValueError` would kill this node mid-run.
    output, patch = await run_role(state, PJM, cfg, ports, product=product_name(ports))
    if output is not None:
        patch["type"] = output.data["type"]
        if output.data["confidence"] == "low":
            # Low confidence is an escalation, not a guess. PjM does not re-route
            # while a task waits on the human (README.md, 3). Where a confident type
            # goes is the routing's business, not this node's: the three paths of
            # README §2 exist (issues #46, #65).
            patch |= rules.escalation(
                "classification", f"PjM could not classify with confidence: {output.data['reason']}"
            )
            patch = rules.logged(patch)
            patch["status"] = WAITING_ON_HUMAN
    return finish(state, patch, ports)
