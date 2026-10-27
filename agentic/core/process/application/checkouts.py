"""Where each role works (ADR-0011): the task's checkout for the definition, the
plan and the integration; a subtask's own checkout for its Developer, unless the
task has a single subtask, whose branch is then the task branch.

A checkout is not ready when git finishes with it. A worktree has no environment of its
own, and a tool that looks for one walks up until it finds the product's: a Developer's
`pytest` then imports the code of whatever checkout wrote that environment last, and a
`uv pip install -e .` inside a worktree repoints it for every sister branch at once
(issue #37). So the product's `setup` runs here, once, on the checkout that was just
made, before any role sees it. What the product declares is the product's business
(ADR-0016); a product that declares none is a product with nothing to prepare, and the
system says at startup that it cannot promise isolation (`preflight`)."""

from __future__ import annotations

import anyio

from agentic.shared.configuration import Configuration

from ..domain import presubmit, rules
from ..domain.roles import Role
from ..domain.task import INTEGRATION, TaskState
from .ports import Checkout, Ports, RunStopped
from .release import release_checkout


class SetupFailed(Exception):
    """The product's setup did not prepare a checkout the system just made. The step,
    the command and its output travel; `rules.setup_unprepared` turns them into the
    patch, because which role was about to start is the caller's to say."""

    def __init__(self, branch: str, step: str, command: str, output: str):
        self.branch, self.step, self.command, self.output = branch, step, command, output
        super().__init__(f"setup step {step!r} failed on {branch!r}")

    def patch(self, role: Role) -> TaskState:
        return rules.setup_unprepared(role, self.branch, self.step, self.command, self.output)


def task_base(state: TaskState, ports: Ports) -> str:
    """The commit the task started from: what its stack is read at, checks and setup
    alike, never the branch's own (ADR-0016, addendum). The product's HEAD when the
    branch carries no base, which only a branch the system did not create lacks."""
    return ports.workspace.base_of(rules.task_branch(state["id"])) or "HEAD"


def task_checkout(state: TaskState, cfg: Configuration, ports: Ports) -> Checkout:
    return _prepared(ports.workspace.task_checkout(state["id"]), state, cfg, ports)


def subtask_checkout(
    state: TaskState, subtask_id: str, cfg: Configuration, ports: Ports
) -> Checkout:
    # The task's checkout first, always: a subtask branches off the task branch, so the
    # workspace makes that one on the way whether or not this call wanted it, and a
    # worktree nobody asked for is a worktree nobody prepares.
    task = task_checkout(state, cfg, ports)
    real = [sid for sid in state.get("subtasks", {}) if sid != INTEGRATION]
    if subtask_id == INTEGRATION or len(real) == 1:
        return task
    made = ports.workspace.subtask_checkout(state["id"], subtask_id)
    return _prepared(made, state, cfg, ports)


async def task_checkout_in_thread(state: TaskState, cfg: Configuration, ports: Ports) -> Checkout:
    """`task_checkout` for a node of the graph: making a checkout runs the product's setup,
    a command that can last minutes, and the event loop must stay free to hear a signal
    (GH-355). Nothing else differs."""
    return await anyio.to_thread.run_sync(task_checkout, state, cfg, ports)


async def subtask_checkout_in_thread(
    state: TaskState, subtask_id: str, cfg: Configuration, ports: Ports
) -> Checkout:
    """`subtask_checkout` off the event loop, for the same reason."""
    return await anyio.to_thread.run_sync(subtask_checkout, state, subtask_id, cfg, ports)


def _prepared(checkout: Checkout, state: TaskState, cfg: Configuration, ports: Ports) -> Checkout:
    """The checkout with the product's setup run in it, when this call is the one that
    made it. Asking again gets the one that exists: a subtask that adds a dependency
    installs it in its own environment, and that is its own work, not the system's.

    A checkout whose setup failed is discarded, branch and commits untouched. Otherwise
    it would sit there half built and every later ask would report it as existing, so
    the resume the escalation asks for would run roles in the very environment the human
    was told to fix."""
    if not checkout.created:
        return checkout
    for step, command in ports.stack.setup(task_base(state, ports)).items():
        try:
            result = ports.commands.run(checkout.path, command, cfg.check_timeout_s)
        except RunStopped:
            # The run was told to stop mid-setup (GH-355): the checkout is as good as a
            # failed one, a half-built tree a later `created=False` would take for ready.
            # Not a `SetupFailed`: nothing escalates, the resume makes it again.
            release_checkout(ports, checkout.path)
            ports.workspace.discard(checkout.path)
            raise
        if not result.passed:
            release_checkout(ports, checkout.path)
            ports.workspace.discard(checkout.path)
            said = presubmit.said(result.output, result.timed_out, cfg.check_timeout_s)
            raise SetupFailed(checkout.branch, step, command, said)
    # Setup passed on this tree: the record a stored pass needs (ADR-0065).
    ports.workspace.record_setup_tree(checkout.path)
    return checkout
