"""One Developer builds one subtask in its own checkout (README.md, 4; ADR-0011).
The integration is a subtask like the others, built by the integrator on the task
branch once every real subtask is approved.

A delivery is a commit on a clean tree: a Developer that ends its turn with the
branch where it started has delivered nothing, whatever its section says, and one
that leaves changes uncommitted delivered something else than what it tested (run 4:
the suite passed against a working tree the branch did not hold). Either is a role
failure, retried once like any other, so the model is told what was missing. The
delivery gate of issue #22 will catch both before the turn ends; this stays as its
backstop."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR
from ..domain.task import INTEGRATION, TaskState
from .checkouts import subtask_checkout
from .ports import Ports
from .run_role import finish, run_role


async def develop(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    subtask_id = state["subtask_id"]
    subtask = state["subtasks"][subtask_id]
    checkout = subtask_checkout(state, subtask_id, ports)
    role = DEVELOPER_INTEGRATOR if subtask_id == INTEGRATION else DEVELOPER
    # A dependency gates the order and provides the code: its approved branch is
    # brought in before the Developer starts (the integrator merges by itself).
    if subtask_id != INTEGRATION:
        for dep in subtask.get("depends_on", []):
            ports.workspace.merge(checkout.path, state["subtasks"][dep]["branch"])
    before = ports.workspace.head(checkout.path)

    def committed(_: dict) -> None:
        if ports.workspace.head(checkout.path) == before:
            raise ValueError(
                "no commit on the branch: a delivery is committed code, commit your "
                "work on the current branch and then deliver"
            )
        if not ports.workspace.is_clean(checkout.path):
            raise ValueError(
                "uncommitted changes in the working tree: the branch must hold exactly "
                "what you tested, commit them (or discard them) and then deliver"
            )

    section = rules.developer_section(subtask_id)
    output, patch = await run_role(
        state,
        role,
        cfg,
        ports,
        check=committed,
        section=section,
        extra_sections=(section, rules.review_section(subtask_id)),
        cwd=checkout.path,
        subtask=subtask_id,
        branches=_branches(state),
    )
    patch["subtasks"] = {
        subtask_id: {
            **subtask,
            "branch": checkout.branch,
            "base": checkout.base,
            "status": "delivered" if output is not None else subtask.get("status", "pending"),
        }
    }
    if output is not None:
        patch["status"] = "developing"
    return finish(state, patch, ports)


def _branches(state: TaskState) -> str:
    """The subtask branches, in dependency order, for the integrator's prompt."""
    subtasks = state.get("subtasks", {})
    lines = [
        f"- `{sub['branch']}` ({sid}: {sub.get('title', '')})"
        for sid, sub in subtasks.items()
        if sid != INTEGRATION and sub.get("branch")
    ]
    return "\n".join(lines) or "- none"
