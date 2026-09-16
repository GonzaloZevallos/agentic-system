"""The system's turn between two waves of Developers (ADR-0004, fifth addendum).

Every wave passes through here: the plan hands it the first one, each branch of the
fan-out hands back what it built, and a return is a wave like any other. No role runs,
so it costs nothing, and it is the only thing in the technical stage that runs alone.
That is what it is for; three things need a moment where nothing else is running.

**The checkouts.** Creating them is `git branch` plus `git worktree add` on the same
`.git`; four Developers starting within 250 ms of each other raced for the lock, and a
lost lock was a crash with no escalation (run 4). Here they are created one at a time,
before the wave starts, and the Developer that asks again gets the one that exists.

**The task branch.** Each approved subtask is merged into it as soon as it is
approved, not by an integrator at the end (README.md, 13): the branch is always the
integrated piece so far, and a conflict surfaces one subtask at a time instead of all
at once. A merge the system cannot do on its own is left for the integrator, named in
the integration subtask, which is what that role is for.

**The document, and the human.** Each branch saves the document with its own view of
the state (`finish`), so the last one to write loses its sisters' work until something
saves again; this is that something, with the state already merged. And an escalation
from any branch is told to the human once, from here, after every branch came back:
inside a wave a branch cannot know that its sister stopped, and that does not change.
"""

from __future__ import annotations

from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, Subtask, TaskState, apply
from .checkouts import subtask_checkout, task_checkout
from .ports import Ports
from .run_role import finish


async def integrate(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    if state.get("escalated"):
        # `escalate` runs next and saves the merged state itself; nothing else may
        # touch the workspace while the task waits for the human. The status is settled
        # here because `status` is a "last write wins" field: the branch that escalated
        # wrote `waiting_on_human`, and a sister that delivered fine wrote `developing`
        # in the same superstep, so which one the merge kept is a coin toss. The reason
        # is not at risk (nothing but an escalation ever writes `escalated`), but a
        # document that says `developing` about a task waiting for a human is a lie.
        return {"status": WAITING_ON_HUMAN}
    task = task_checkout(state, ports)
    patch: TaskState = {"branch": task.branch}
    conflicts = _merge_approved(state, task.path, ports)
    if (integration := _integration(state, conflicts)) is not None:
        patch = apply(patch, {"subtasks": {INTEGRATION: integration}})
    current = apply(state, patch)
    for subtask_id in rules.dispatched_subtasks(current):
        subtask_checkout(current, subtask_id, ports)
    return finish(state, patch, ports)


def _integration(state: TaskState, conflicts: list[str]) -> Subtask | None:
    """The integration subtask as this wave leaves it, or None when nothing about it
    changed. It enters once every subtask is approved and there is more than one, and it
    carries the branches the merges could not settle, on whatever wave those appear:
    a conflict is not only a last-wave thing, and the integrator is told on the wave it
    shows up. The list is rewritten every time, so one the integrator resolved is gone."""
    existing = state.get("subtasks", {}).get(INTEGRATION)
    if existing is None and not rules.needs_integration(state):
        return None
    wanted = {**(existing or rules.integration_subtask()), "conflicts": conflicts}
    return None if wanted == existing else wanted


def _merge_approved(state: TaskState, task_path: Path, ports: Ports) -> list[str]:
    """Every approved subtask brought into the task branch, one at a time; the branches
    the merge could not settle come back for the integrator. Merging one that is
    already in is a no-op, so a wave may re-offer what an earlier one merged."""
    conflicts = []
    for subtask_id, subtask in state.get("subtasks", {}).items():
        branch = subtask.get("branch")
        if subtask_id == INTEGRATION or subtask.get("status") != "approved" or not branch:
            continue
        if ports.workspace.merge(task_path, branch) is not None:
            conflicts.append(branch)
    return conflicts
