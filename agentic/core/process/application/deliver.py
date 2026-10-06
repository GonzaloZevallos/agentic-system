"""The delivery to the human (ADR-0011): the task branch is pushed, a pull request
opened with the definition and the plan, and the issue told where both the pull
request and the task document are. The task is then ready for review: the human's
merge is the closing while the path has no QA.

No role runs here; it is the system talking to the human, which ADR-0001 allows."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import document, rules
from ..domain.pull_request import BoundedBody, bounded_body
from ..domain.roles import DEVELOPER_INTEGRATOR, PJM, PM, TECH_LEAD
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .checkouts import SetupFailed, task_checkout
from .ports import GitError, Ports
from .run_role import finish

# How the delivery comment starts: a resume looks past the system's comments.
READY = "Ready for review: "


async def deliver(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(DEVELOPER_INTEGRATOR), ports)
    relative = document.relative_path(state["id"]).as_posix()
    body = _bounded(state, task.branch, relative, ignored=False)
    if body.bounded and ports.workspace.is_ignored(task.path, relative):
        body = _bounded(state, task.branch, relative, ignored=True)
        committing = False
    else:
        committing = body.bounded
    # The commit point is the pull request: everything up to it is a visible escalation
    # the human resumes (the branch is integrated, so a resume only delivers again).
    try:
        step = "task document commit failed"
        if committing:
            ports.documents.save(state)
            ports.documents.write_into(state["id"], task.path)
            ports.workspace.commit_paths(task.path, [relative], f"{state['id']}: task document")
        step = "push failed"
        ports.workspace.push(task.branch)
        step = "gh pr create failed"
        url = ports.tracker.open_pull_request(state["id"], task.branch, state["title"], body.text)
    except (GitError, ValueError) as failed:
        return finish(state, _undelivered(step, failed), ports)
    patch: TaskState = {
        "status": "ready_for_review",
        "pull_request": url,
        "branch": task.branch,
        "current_role": PJM.name,
    }
    # Best effort from here: the pull request exists, so a crash would only make the
    # next attempt hit "already exists".
    try:
        ports.tracker.comment(state["id"], f"{READY}{url}\n\nTask document: `{relative}`")
    except ValueError:
        pass
    try:
        ports.workspace.cleanup(state["id"])
    except GitError:
        pass
    return finish(state, patch, ports)


def _undelivered(step: str, failed: Exception) -> TaskState:
    reason = f"{step}: {failed}; the branch is integrated, resume to deliver again"
    return rules.logged(
        {
            "current_role": PJM.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation("failure", reason),
        }
    )


def _bounded(state: TaskState, branch: str, relative: str, *, ignored: bool) -> BoundedBody:
    def pointer(omitted: tuple[str, ...]) -> str:
        named = ", ".join(omitted)
        if ignored:
            return (
                f"Full sections ({named}): the task document in the product checkout at "
                f"`{relative}`; this repository does not version it."
            )
        return (
            f"Full sections ({named}): the task document as of delivery, `{relative}` on "
            f"branch `{branch}`. Its status and later fields are kept in the product "
            f"checkout's copy."
        )

    return bounded_body(_parts(state), state["title"], pointer)


def _parts(state: TaskState) -> list[tuple[str, str]]:
    """The definition and the plan, as the human reads them before the diff, plus the
    design branch's own observations (ADR-0040) when there are any -- never when there
    are none, so a task this branch never touched renders byte for byte as it always
    has."""
    sections = state.get("sections", {})
    parts = [(name, sections[name]) for name in (PM.name, TECH_LEAD.name) if name in sections]
    observations = [i for i in state.get("design_items", []) if i["kind"] == "observation"]
    if observations:
        parts.append(
            ("Observations on the design", rules.design_observations_markdown(observations))
        )
    return parts
