"""The discard of a bug (README.md, 2 and 3; issue #65): PM ruled the behaviour
expected, so there is nothing to build. The issue is told why, with the task document
next to it, and the run ends. The human closes the issue, as the human merges the
pull request of a delivered task (ADR-0011): the system reports, the human decides
what the issue does. No label: the task is not waiting on anybody. The task's
worktree goes, as it goes on a delivery: an end has no resume to keep it for.

No role runs here; it is the system talking to the human, which ADR-0001 allows."""

from __future__ import annotations

from datetime import UTC, datetime

from agentic.shared.configuration import Configuration

from ..domain import document, rules
from ..domain.roles import PJM, PM
from ..domain.task import TaskState
from .ports import Ports
from .release import release_task
from .run_role import finish

# How the discard comment starts: a resume looks past the system's comments.
DISCARDED = "Discarded: "


async def discard(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    ports.tracker.comment(
        state["id"],
        f"{DISCARDED}{discard_reason(state)}\n\n"
        f"Task document: `{document.relative_path(state['id'])}`",
    )
    reaped = release_task(ports, state["id"])
    return finish(
        state,
        {
            "current_role": PJM.name,
            **rules.finished("discarded", datetime.now(UTC).isoformat(timespec="seconds")),
            **({"reaped_at_cleanup": list(reaped)} if reaped else {}),
        },
        ports,
    )


def discard_reason(state: TaskState) -> str:
    """Why, in PM's words: the reason its ruling carried, or, for a state that lost it,
    the first line of PM's section that is not a heading."""
    if kept := (state.get("discard_reason") or "").strip():
        return kept
    section = state.get("sections", {}).get(PM.name, "")
    lines = (line.strip() for line in section.splitlines())
    return next((line for line in lines if line and not line.startswith("#")), "expected behaviour")
