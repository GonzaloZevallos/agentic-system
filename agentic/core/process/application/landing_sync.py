"""The landing steps that move the pull request's head (GH-239, ADR-0048): `sync_master`
and `re_review`, and the `reopen` they and `landing_fix` share.

`sync_master` merges the upstream into the task branch in the task's worktree, never the
product checkout (ADR-0039). A clean merge whose paths are disjoint from the branch's own
carries the Tech Lead's verdict (`landing.review_carries`): `reviewed_head` advances,
`reviewed_head_carried_from` says where it came from, and the push lets the merge gate
re-run the checks and the CI on the new head. Anything else reopens the technical stage
under the existing pull request: the reviewed unit goes back to its Developer (a conflict)
or to its review (`re_review`), the task document says so, and `agentic resume` is spawned
to run it. `deliver` re-delivers to the same pull request and closes the landing entry."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from ..domain import landing, rules
from ..domain.roles import pair_for
from ..domain.task import INTEGRATION, ChangeCategory, ChangesRequested, Subtask, TaskState
from . import resume as resuming
from .checkouts import SetupFailed, task_base, task_checkout
from .landing_plan import LandingContext, entries
from .ports import GitError

CROSSCUT: ChangeCategory = "integration-crosscut"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def reopen(
    state: TaskState,
    units: Sequence[str],
    reason: str,
    category: ChangeCategory,
    status: Literal["returned", "delivered"],
) -> TaskState:
    """The state with the technical stage reopened under its pull request (the whole state,
    not a patch to `apply`: the reducers are folded here, so applying it again would count
    the history twice): `units` become
    `status` (`returned` with `reason` and `category` for a Developer pass, `delivered` for
    a review alone), an `integration` subtask that is not among them goes back to
    `pending`, the task is `developing`, and every unit gets a `return_history` entry
    marked `by: "landing"`. `changes_requested`, the pair's limit, is never in the patch:
    the landing is bounded by its own budgets. `pull_request` is kept."""
    subtasks = state.get("subtasks", {})
    reopened: dict[str, Subtask] = {}
    for unit in units:
        sub = subtasks[unit]
        if status == "returned":
            reopened[unit] = {
                **sub,
                "status": status,
                "reason": reason,
                "change_category": category,
            }
        else:
            reopened[unit] = {**sub, "status": status}
    if INTEGRATION in subtasks and INTEGRATION not in units:
        reopened[INTEGRATION] = {**subtasks[INTEGRATION], "status": "pending"}
    history: list[ChangesRequested] = [
        {**rules.change_entry(pair_for(unit), category, reason), "by": "landing"} for unit in units
    ]
    return {
        **state,
        "status": "developing",
        "subtasks": {**subtasks, **reopened},
        "change_history": [*state.get("change_history", []), *history],
    }


def _state(context: LandingContext) -> TaskState | None:
    raw = context.ports.documents.load(context.task_id)
    return resuming.state_from(raw) if raw is not None else None


def _spawn(context: LandingContext) -> None:
    number = context.ports.inbox.issue_of(context.task_id)
    if context.resume is None or number is None:
        context.log(f"agentic: land: {context.task_id}: reopened, nothing to resume it with")
        return
    pid = context.resume(number)
    context.log(f"agentic: land: {context.task_id}: reopened, resume -> pid {pid}")


def _reopened(
    context: LandingContext,
    state: TaskState,
    unit: str,
    reason: str,
    status: Literal["returned", "delivered"],
) -> None:
    """Writes the reopened document -- the landing entry carrying the cost it starts from --
    and resumes the task."""
    reopened = reopen(state, [unit], reason, CROSSCUT, status)
    marked = landing.with_cost_base(
        entries(state), context.head, context.step.kind, state.get("cost_usd", 0.0)
    )
    context.ports.documents.save({**reopened, "landing": marked})
    _spawn(context)


def _unit(state: TaskState) -> str | None:
    unit = rules.reviewed_unit(state)
    return unit["id"] if unit is not None and "id" in unit else None


def _bring_in(context: LandingContext, state: TaskState) -> tuple[str, tuple[str, ...]]:
    """The task branch with the pull request's head in its worktree, and the paths a
    conflict left (none for a clean one). A foreign push never reached the local branch;
    merging the head in makes the next push a fast-forward."""
    ports = context.ports
    checkout = task_checkout(state, context.cfg, ports)
    if ports.workspace.full_head(checkout.path) == context.head:
        return checkout.branch, ()
    ports.workspace.fetch_commit(context.head)
    merged = ports.workspace.merge_upstream_into(checkout.path, context.head)
    return checkout.branch, merged.conflicted


def _conflict_reason(paths: Sequence[str], source: str) -> str:
    listed = ", ".join(paths)
    return (
        f"Merging {source} into the task branch conflicted in: {listed}. The merge is in "
        "progress in the worktree: resolve every conflict marker, `git add` the files and "
        "commit the merge."
    )


def re_review(context: LandingContext) -> None:
    """A head the Tech Lead did not review (or an approval that is not one): the reviewed
    unit goes back to its review over the head as it is, never carried."""
    state = _state(context)
    unit = _unit(state) if state is not None else None
    if state is None or unit is None:
        context.log(f"agentic: land: {context.task_id}: re_review: no reviewed unit")
        return
    try:
        _, conflicted = _bring_in(context, state)
    except (SetupFailed, GitError) as failed:
        reason = f"the pull request head could not be brought in: {failed}"
        _reopened(context, state, unit, reason, "returned")
        return
    if conflicted:
        _reopened(context, state, unit, _conflict_reason(conflicted, context.head), "returned")
        return
    reason = (
        f"The pull request head is {context.head}, which the Tech Lead has not reviewed "
        f"({context.step.refusal}): review it."
    )
    _reopened(context, state, unit, reason, "delivered")


def sync_master(context: LandingContext) -> None:
    """Merge the upstream into the task branch (see the module docstring)."""
    ports, sha = context.ports, context.upstream.sha
    state = _state(context)
    unit = _unit(state) if state is not None else None
    if state is None or unit is None or sha is None:
        context.log(f"agentic: land: {context.task_id}: sync_master: nothing to sync")
        return
    reviewed = state["subtasks"][unit].get("reviewed_head")
    try:
        branch, conflicted = _bring_in(context, state)
        if conflicted:
            _reopened(context, state, unit, _conflict_reason(conflicted, context.head), "returned")
            return
        checkout = task_checkout(state, context.cfg, ports)
        ports.workspace.fetch_commit(sha)
        merged = ports.workspace.merge_upstream_into(checkout.path, sha)
    except (SetupFailed, GitError) as failed:
        _reopened(context, state, unit, f"syncing {sha} failed: {failed}", "returned")
        return
    if not merged.clean:
        _reopened(context, state, unit, _conflict_reason(merged.conflicted, sha), "returned")
        return
    if not _carries(context, state, reviewed, merged.head):
        reason = f"Master ({sha}) was merged into the branch; review the result as it is."
        _reopened(context, state, unit, reason, "delivered")
        return
    try:
        ports.workspace.push(branch)
    except GitError as failed:
        _reopened(context, state, unit, f"pushing the synced branch failed: {failed}", "delivered")
        return
    advanced: Subtask = {
        **state["subtasks"][unit],
        "reviewed_head": merged.head,
        "reviewed_head_carried_from": reviewed or "",
    }
    resolved = landing.resolve_open(entries(state), _now(), state.get("cost_usd", 0.0))
    subtasks = {**state["subtasks"], unit: advanced}
    ports.documents.save({**state, "subtasks": subtasks, "landing": resolved})
    context.log(
        f"agentic: land: {context.task_id}: master synced, verdict carried to {merged.head}"
    )


def _carries(context: LandingContext, state: TaskState, reviewed: str | None, head: str) -> bool:
    """Whether the verdict on `reviewed` stands for `head`, the merge commit: the paths the
    merge brought in against the paths of the branch's own work. An unreadable diff, or a
    unit never reviewed, carries nothing."""
    if not reviewed:
        return False
    workspace = context.ports.workspace
    try:
        merge_paths = workspace.changed_paths(reviewed, head)
        branch_paths = workspace.changed_paths(task_base(state, context.ports), reviewed)
    except GitError:
        return False
    return landing.review_carries(False, merge_paths, branch_paths)
