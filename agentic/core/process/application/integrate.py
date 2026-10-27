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

**The findings table.** Every review appends its findings to the state and none of them
can fold the table, because the sister branches of its wave are reviewing at the same
time. Here the branches are merged, so here the table is written as the section the next
wave reads (issue #42): one row per finding, the first verdict kept, only what is open.

**The document, and the human.** Each branch saves the document with its own view of
the state (`finish`), so the last one to write loses its sisters' work until something
saves again; this is that something, with the state already merged. And an escalation
from any branch is told to the human once, from here, after every branch came back:
inside a wave a branch cannot know that its sister stopped, and that does not change.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import DEVELOPER
from ..domain.task import INTEGRATION, WAITING_ON_HUMAN, Subtask, TaskState, apply
from . import pre_pr_fix
from .checkouts import SetupFailed, subtask_checkout, task_base, task_checkout
from .ports import Checkout, Ports
from .run_role import finish


async def integrate(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    # Off the event loop, as the checkouts were (GH-355): making one runs the product's setup,
    # a command that can last minutes, and the loop must stay free to hear a signal. The
    # scheduling loop does the same with each turn it runs.
    return await asyncio.to_thread(integrate_turn, state, cfg, ports)


def integrate_turn(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    """The turn itself, synchronous (GH-364): the scheduling loop runs it in a worker
    thread, one at a time, so that nothing else touches the workspace meanwhile. Being in a
    thread already, it makes its checkouts directly: the `_in_thread` forms are for a node
    that runs on the event loop."""
    if state.get("escalated"):
        # `escalate` runs next and saves the merged state itself; nothing else may
        # touch the workspace while the task waits for the human. The status is settled
        # here because `status` is a "last write wins" field: the branch that escalated
        # wrote `waiting_on_human`, and a sister that delivered fine wrote `developing`
        # in the same superstep, so which one the merge kept is a coin toss. The reason
        # is not at risk (nothing but an escalation ever writes `escalated`), but a
        # document that says `developing` about a task waiting for a human is a lie.
        # The table still gets written: it is pure computation over the merged state, it
        # touches nothing, and the human reading the document sees what this wave found
        # instead of what the wave before it did.
        return {"status": WAITING_ON_HUMAN, "sections": rules.findings_section(state)}
    # Every checkout of the wave is born here, one at a time, so the product's setup
    # runs and a failing one stops the wave before a Developer is dispatched. Inside a
    # branch it could not be told to the human: a branch never routes to the escalation.
    try:
        task = task_checkout(state, cfg, ports)
        patch: TaskState = {"branch": task.branch, "sections": rules.findings_section(state)}
        conflicts = _merge_approved(state, task.path, ports)
        if (integration := _integration(state, conflicts, task)) is not None:
            patch = apply(patch, {"subtasks": {INTEGRATION: integration}})
        # A product that declares tiers is told the head the whole suite must cover (GH-358):
        # stamped on every pass, a resumed one included, after the merges and before the
        # next role, so the node that runs the suite and the delivery that pushes it compare
        # against what this pass left. Nothing is written for a product with no tiers.
        if ports.stack.delivery_checks(task_base(state, ports)):
            patch["task_head"] = ports.workspace.full_head(task.path)
        current = apply(state, patch)
        # The pass before the pull request is closed here, once the units it reopened and the
        # integration are approved again (GH-354): the one place that sees every branch.
        if (
            closed := pre_pr_fix.close_pass(
                current, datetime.now(UTC).isoformat(timespec="seconds")
            )
        ) is not None:
            patch["landing"] = closed
        for subtask_id in rules.dispatched_subtasks(current):
            subtask_checkout(current, subtask_id, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(DEVELOPER), ports)
    return finish(state, patch, ports)


def _integration(state: TaskState, conflicts: list[str], task: Checkout) -> Subtask | None:
    """The integration subtask as this wave leaves it, or None when nothing about it
    changed. It enters once every subtask is approved and there is more than one, whether
    or not a Developer has anything to do on it: the whole branch is reviewed either way,
    and it enters `delivered` when the merges were the whole job, so the review runs and
    no integrator is paid for a merge already done (README.md, 13).

    It carries the branches the merges could not settle, on whatever wave those appear:
    a conflict is not only a last-wave thing, and the integrator is told on the wave it
    shows up. The list is rewritten every time, so one the integrator resolved is gone."""
    existing = state.get("subtasks", {}).get(INTEGRATION)
    if existing is None:
        if not rules.needs_integration(state):
            return None
        # The branch is the task branch, stamped here and not by a Developer: when the
        # merges were the whole job no Developer runs on it, and the review, the
        # delivery and a resume checked against git all read it off the subtask.
        return {
            **rules.integration_subtask(state.get("integration_brief", ""), conflicts),
            "branch": task.branch,
            "base": task.base,
        }
    wanted = {**existing, **rules.integration_work(state.get("integration_brief", ""), conflicts)}
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
