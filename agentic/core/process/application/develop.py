"""One Developer builds one subtask in its own checkout (README.md, 4; ADR-0011).
The integration is a subtask like the others, built by the integrator on the task
branch once every real subtask is approved.

A delivery is a commit on a clean tree whose product checks pass. The presubmit
checks it when the model hands the delivery back, inside the turn, and refuses it up to
twice with the reason (`presubmit.py`); the checks' results open the Developer's section,
written by the system. A Developer that ends its turn with the branch where it started
has delivered nothing, whatever its section says, and one that leaves changes
uncommitted delivered something else than what it tested (run 4: the suite passed
against a working tree the branch did not hold). Past the presubmit, either is still a role
failure, retried once like any other: the backstop."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import presubmit, rules
from ..domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR
from ..domain.task import INTEGRATION, TaskState
from .checkouts import subtask_checkout
from .ports import Ports
from .presubmit import CheckoutPresubmit, declared_checks
from .run_role import finish, run_role


async def develop(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    subtask_id = state["subtask_id"]
    subtask = state["subtasks"][subtask_id]
    checkout = subtask_checkout(state, subtask_id, ports)
    role = DEVELOPER_INTEGRATOR if subtask_id == INTEGRATION else DEVELOPER
    # A dependency gates the order and provides the code: its approved branch is
    # brought in before the Developer starts. The integration skips this: the fan-in
    # already brought every approved branch into the task branch, and what it could not
    # merge is exactly the integrator's job. A dependency the system cannot merge stops
    # the subtask before it is paid for:
    # building on code that is not there is work thrown away, and untangling two
    # branches is the human's call, not this Developer's.
    if subtask_id != INTEGRATION:
        for dep in subtask.get("depends_on", []):
            branch = state["subtasks"][dep]["branch"]
            if conflict := ports.workspace.merge(checkout.path, branch):
                patch = rules.unmergeable(subtask_id, dep, conflict)
                return finish(state, patch, ports)
    before = ports.workspace.head(checkout.path)
    checks = declared_checks(state, ports)
    delivery_presubmit = CheckoutPresubmit(
        ports, checkout.path, before, checks, cfg.check_timeout_s
    )

    def committed(_: dict) -> None:
        moved = ports.workspace.head(checkout.path) != before
        if reason := presubmit.refusal(moved, ports.workspace.is_clean(checkout.path), []):
            raise ValueError(reason)

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
        presubmit=delivery_presubmit,
        subtask=subtask_id,
        branches=_branches(state),
        checks=presubmit.describe_checks(checks),
    )
    if output is not None and (record := delivery_presubmit.markdown()) is not None:
        patch["sections"] = {**patch["sections"], section: f"{record}\n\n{output.text}"}
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
    """The subtask branches, in dependency order, for the integrator's prompt, with the
    ones the fan-in could not bring into the task branch marked: those are the merges
    left to do by hand, and the rest are already in."""
    subtasks = state.get("subtasks", {})
    conflicts = subtasks.get(INTEGRATION, {}).get("conflicts", [])
    lines = [
        f"- `{sub['branch']}` ({sid}: {sub.get('title', '')})"
        + (" — **conflicts, not merged**" if sub["branch"] in conflicts else " — already merged")
        for sid, sub in subtasks.items()
        if sid != INTEGRATION and sub.get("branch")
    ]
    return "\n".join(lines) or "- none"
