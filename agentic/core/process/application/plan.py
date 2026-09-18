"""The technical solution (README.md, 4; ADR-0014): the ephemeral Tech Lead
interviews Tech Lead, who then writes the plan from its own session. The ADRs the
interview leaves are committed on the task branch (ADR-0011); the subtasks enter the
state pending, each to get its branch when a Developer starts it.

The plan also leaves the invariants, the rules every subtask honours (issue #42), and
what the subtasks need beyond a merge to work together. The invariants are the one
channel between reviews that works in the first wave: the findings table is empty
while the first Developers build, and these are written before anybody codes. The
ephemeral proposes them from what the grill uncovered; the Tech Lead adopts them or
rejects them with a reason in the plan, which is what makes the ephemeral's grilling
measurable (issue #28)."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import schemas
from ..domain.roles import INTERVIEW_SOLUTION, INTERVIEW_SOLUTION_PLATFORM, Interview
from ..domain.task import Subtask, TaskState, apply
from .checkouts import SetupFailed, task_checkout
from .interview import interview
from .ports import Ports
from .run_role import finish, run_role


async def plan(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    spec = solution_interview(state)
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(spec.author), ports)
    transcript, patch = await interview(state, spec, cfg, ports, cwd=task.path)
    ports.workspace.commit_all(task.path, f"{state['id']}: solution interview")
    if transcript is None:
        return finish(state, patch, ports)
    output, written = await run_role(
        apply(state, patch),
        spec.author,
        cfg,
        ports,
        check=schemas.plan_check,
        cwd=task.path,
        resume=transcript.sessions.get(spec.author_side.slug),
        proposed_invariants=_proposed(transcript.invariants),
    )
    if output is not None:
        written["subtasks"] = {s["id"]: _pending(s) for s in output.data["subtasks"]}
        written["invariants"] = output.data["invariants"]
        written["integration_brief"] = output.data["integration"].strip()
        written["status"] = "planned"
    return finish(state, apply(patch, written), ports)


def solution_interview(state: TaskState) -> Interview:
    """Which solution interview this task gets. A feature has a PM behind it to consult;
    a platform enhancement has none, so the same two Tech Leads run with the human as
    the only door for a question that is really about the product (issue #46)."""
    return INTERVIEW_SOLUTION if state.get("type") == "feature" else INTERVIEW_SOLUTION_PLATFORM


def _proposed(invariants: list[str]) -> str:
    """What the ephemeral proposed, for the plan's prompt. The Tech Lead decides on each
    one; the empty case says so plainly instead of leaving a dangling heading."""
    return "\n".join(f"- {line}" for line in invariants) or "- none"


def _pending(item: dict) -> Subtask:
    return {
        "id": item["id"],
        "title": item["title"],
        "brief": item["brief"],
        "seams": item["seams"],
        "depends_on": item["depends_on"],
        "status": "pending",
    }
