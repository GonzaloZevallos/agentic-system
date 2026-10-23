"""QA Performance's node (GH-318, ADR-0060), run by the graph when
`rules.qa_performance_due`. This module holds the entry point the graph calls; the body
(measure at the integrated head, derive the verdict, reopen on a return) is the `qa-node`
subtask's, and until it lands a convened task fails loudly here instead of looping."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain.task import TaskState
from .ports import Ports


async def qa_performance(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    raise NotImplementedError("QA Performance's node body belongs to the qa-node subtask")
