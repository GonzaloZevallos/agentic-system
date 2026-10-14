"""PjM classifies the task (README.md, 2 and 4)."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import PJM
from ..domain.task import TaskState
from .ports import Ports
from .preflight import product_name
from .run_role import finish, run_role


async def classify(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    # The declared name as data, like `{{date}}` (ADR-0019): `product_name` is the path
    # that answers `unknown` when the stack cannot be read, never the bare port call
    # whose `ValueError` would kill this node mid-run.
    output, patch = await run_role(state, PJM, cfg, ports, product=product_name(ports))
    if output is not None:
        guess = output.data["type"]
        patch["type"] = guess
        if output.data["confidence"] == "low":
            # Low confidence is an assumption, not an escalation (ADR-0048): the task
            # continues on the most likely path, and the PjM section says what was
            # guessed, what it resolved to and why, so a reviewer can undo it.
            resolved = rules.low_confidence_type(guess)
            patch["type"] = resolved
            assumption = (
                f"Assumption (low confidence): PjM guessed `{guess}` and the task "
                f"continues as `{resolved}`. Reason: {output.data['reason']}"
            )
            section = patch.get("sections", {}).get(PJM.name, "")
            patch["sections"] = {PJM.name: f"{section}\n\n{assumption}".strip()}
    return finish(state, patch, ports)
