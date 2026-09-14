"""Where each role works (ADR-0011): the task's checkout for the definition, the
plan and the integration; a subtask's own checkout for its Developer, unless the
task has a single subtask, whose branch is then the task branch."""

from __future__ import annotations

from ..domain.task import INTEGRATION, TaskState
from .ports import Checkout, Ports


def task_checkout(state: TaskState, ports: Ports) -> Checkout:
    return ports.workspace.task_checkout(state["id"])


def subtask_checkout(state: TaskState, subtask_id: str, ports: Ports) -> Checkout:
    real = [sid for sid in state.get("subtasks", {}) if sid != INTEGRATION]
    if subtask_id == INTEGRATION or len(real) == 1:
        return task_checkout(state, ports)
    return ports.workspace.subtask_checkout(state["id"], subtask_id)
