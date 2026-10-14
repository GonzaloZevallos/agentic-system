"""The delivery to the human (ADR-0011): the task branch is pushed, a pull request
opened with the definition and the plan, and the issue told where both the pull
request and the task document are. The task is then ready for review: the human's
merge is the closing while the path has no QA.

No role runs here; it is the system talking to the human, which ADR-0001 allows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import assert_never

from agentic.shared.configuration import Configuration

from ..domain import document, landing, rules
from ..domain.pull_request import BoundedBody, bounded_body
from ..domain.roles import DEVELOPER_INTEGRATOR, PJM, PM, TECH_LEAD
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .checkouts import SetupFailed, task_base, task_checkout
from .ports import BranchPullRequest, GitError, Ports
from .run_role import finish

# How the delivery comment starts: a resume looks past the system's comments.
READY = "Ready for review: "


async def deliver(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(DEVELOPER_INTEGRATOR), ports)
    relative = document.relative_path(state["id"]).as_posix()
    recorded = state.get("replaced_remote_tip")
    # The commit point is the pull request: everything up to it is a visible escalation
    # the human resumes (the branch is integrated, so a resume only delivers again).
    # What is read of the remote is read here, once and in this order (GH-204): the tip,
    # then local reads, then the pull requests only when the remote diverged. Nothing
    # remote runs between the last of these and the push, so the plan stays true.
    try:
        step = "remote branch read failed"
        tip = ports.workspace.remote_tip(task.path, task.branch)
        in_head = tip is None or ports.workspace.is_ancestor(task.path, tip, "HEAD")
        in_archive = False
        prs: list[BranchPullRequest] = []
        if tip is not None and not in_head:
            archives = rules.archived_task_branches(
                ports.workspace.branches("archive/"), state["id"]
            )
            in_archive = any(ports.workspace.is_ancestor(task.path, tip, a) for a in archives)
            prs = ports.tracker.pull_requests_on(task.branch)
        plan = rules.push_plan(tip, in_head, prs, in_archive)
        kept = (
            plan.kind == "push"
            and tip is not None
            and recorded is not None
            and (recorded == tip or ports.workspace.is_ancestor(task.path, recorded, tip))
        )
    except (GitError, ValueError) as failed:
        return finish(state, _undelivered(step, failed, state, recorded), ports)
    # Exhaustive on purpose, so a new cell of the table cannot reach the push unhandled.
    match plan.kind:
        case "refuse":
            return _refused(state, plan, prs, task.branch, ports)
        case "push" | "replace":
            pass
        case _:
            assert_never(plan.kind)
    replaced = rules.replace_record(recorded, plan, kept)
    # Only the key's own value moves: a state with no replace keeps being the very object
    # the node received, and is written as it always has been.
    record: TaskState = {"replaced_remote_tip": replaced} if replaced or recorded else {}
    if record:
        state = {**state, **record}
    replaced_note = rules.replaced_note(replaced, prs, task.branch) if replaced else ""
    preface = "\n\n".join(
        part for part in (replaced_note, _guarded_preface(state, task.branch, ports)) if part
    )
    # A re-delivery under a landing pass closes its entry with what the pass cost.
    reopened = state.get("landing")
    if reopened:
        state = {
            **state,
            "landing": landing.resolve_open(
                reopened,
                datetime.now(UTC).isoformat(timespec="seconds"),
                state.get("cost_usd", 0.0),
            ),
        }
    body = _bounded(state, task.branch, relative, ignored=False, preface=preface)
    if body.bounded and ports.workspace.is_ignored(task.path, relative):
        body = _bounded(state, task.branch, relative, ignored=True, preface=preface)
        committing = False
    else:
        committing = body.bounded
    try:
        step = "task document commit failed"
        if committing:
            ports.documents.save(state)
            ports.documents.write_into(state["id"], task.path)
            ports.workspace.commit_paths(task.path, [relative], f"{state['id']}: task document")
        if replaced and not committing:
            # The sha is durable before the destructive step, whether or not the branch
            # carries the document.
            ports.documents.save(state)
        # The head the push carries, read after the last commit and before the push:
        # nothing is committed after it (GH-216), so the CI the push starts is of this sha.
        step = "head read failed"
        pushed_head = ports.workspace.full_head(task.path)
        step = "push failed"
        ports.workspace.push(task.branch, lease=plan.old_sha if plan.kind == "replace" else None)
        pushed_at = datetime.now(UTC).isoformat(timespec="seconds")
        step = "gh pr create failed"
        # A pull request the task already has is kept: the push alone re-delivers it.
        url = state.get("pull_request") or ports.tracker.open_pull_request(
            state["id"], task.branch, state["title"], body.text
        )
    except (GitError, ValueError) as failed:
        return finish(state, {**_undelivered(step, failed, state, replaced), **record}, ports)
    patch: TaskState = {
        "status": "ready_for_review",
        "pull_request": url,
        "branch": task.branch,
        "current_role": PJM.name,
        "pushed_head": pushed_head,
        "pushed_at": pushed_at,
        **record,
        **({"landing": state["landing"]} if state.get("landing") else {}),
    }
    # Best effort from here: the pull request exists, so a crash would only make the
    # next attempt hit "already exists".
    try:
        ready = f"{READY}{url}\n\nTask document: `{relative}`"
        if replaced:
            ready += f"\n\n{replaced_note}"
        ports.tracker.comment(state["id"], ready)
    except ValueError:
        pass
    try:
        ports.workspace.cleanup(state["id"])
    except GitError:
        pass
    return finish(state, patch, ports)


def _guarded_preface(state: TaskState, branch: str, ports: Ports) -> str:
    """The `Guarded changes` block that leads the pull request body (GH-239, ADR-0048): the
    system's own record, standards or workflows are in the diff and nobody gates them, so
    the body says so. Empty when the diff holds none -- or cannot be read, which the merge
    reads again from the same diff before it lands anything."""
    try:
        paths = ports.workspace.changed_paths(task_base(state, ports), branch)
    except GitError:
        return ""
    guarded = [path for path in paths if rules.guarded_path(path, state["id"])]
    if not guarded:
        return ""
    listed = "\n".join(f"- `{path}`" for path in guarded)
    return (
        "## Guarded changes\n\nThis pull request changes paths the system guards (its own "
        "record, the standards or the workflows). The landing merges it without a human "
        f"gate (ADR-0048):\n\n{listed}"
    )


def _refused(
    state: TaskState,
    plan: rules.PushPlan,
    prs: list[BranchPullRequest],
    branch: str,
    ports: Ports,
) -> TaskState:
    """Nothing is pushed: the reason names the branch, the remote sha and the pull
    requests that decided it."""
    reason = rules.push_refused_reason(plan, prs, branch)
    patch = _undelivered("push refused", reason, state, None)
    if state.get("replaced_remote_tip"):
        # A refusal clears the record (`replace_record`).
        patch["replaced_remote_tip"] = None
    return finish(state, patch, ports)


def _undelivered(
    step: str, failed: Exception | str, state: TaskState, replaced: str | None
) -> TaskState:
    reason = f"{step}: {failed}; the branch is integrated, resume to deliver again"
    if replaced:
        reason += f". {rules.replaced_note(replaced, [], rules.task_branch(state['id']))}"
    return rules.logged(
        {
            "current_role": PJM.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation("failure", reason),
        }
    )


def _bounded(
    state: TaskState, branch: str, relative: str, *, ignored: bool, preface: str
) -> BoundedBody:
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

    return bounded_body(_parts(state), state["title"], pointer, preface=preface)


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
