"""The sweep that closes the issue a task's own merge left open (GH-344, ADR-0062).

A pull request that merged without a closing line, or whose close failed, leaves its issue
open, and everything `Blocked by:` it stays blocked. Each `agentic close --all` pass asks
the tracker for the open `agentic` issues once and closes, as completed, the ones
`rules.close_after_merge` says are done. It writes nothing to the document: the tracker's
own reading of "open" is the retry, so a failed close is attempted again on the next pass.
"""

from __future__ import annotations

from ..domain import rules
from .ports import Ports


def sweep(ports: Ports) -> list[str]:
    """One line per open issue it tried to close, for the caller to print. Never raises."""
    try:
        numbers = ports.tracker.queue()
    except (ValueError, OSError) as failed:
        return [f"agentic: close: issue sweep skipped: {type(failed).__name__}"]
    lines: list[str] = []
    for number in numbers:
        task_id = ports.inbox.task_id(number)
        try:
            document = ports.documents.load(task_id)
        except (ValueError, OSError):
            continue
        if not document or not rules.close_after_merge(document):
            continue
        try:
            ports.tracker.close_issue(task_id)
        except (ValueError, OSError) as failed:
            lines.append(f"agentic: close: {task_id}: issue close failed: {type(failed).__name__}")
        else:
            lines.append(f"agentic: close: {task_id}: issue closed")
    return lines
