"""An escalation reaches the human through the issue (README.md, 3; ADR-0011): the
`needs-human` label puts the task in the queue, and a comment carries the reason
and, after an interview, the questions to answer. The state does not change: it
already says why the run stopped."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import document
from ..domain.task import TaskState
from .ports import Ports

NEEDS_HUMAN = "needs-human"


async def escalate(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    reason = state.get("escalated")
    if not reason:
        return {}
    lines = [f"Escalated: {reason}"]
    questions = state.get("questions_for_human", [])
    if questions:
        lines.append("\nQuestions for you:\n")
        lines.extend(f"{n}. {q}" for n, q in enumerate(questions, 1))
    lines.append(f"\nTask document: `{document.relative_path(state['id'])}`")
    ports.tracker.label(state["id"], NEEDS_HUMAN)
    ports.tracker.comment(state["id"], "\n".join(lines))
    return {}
