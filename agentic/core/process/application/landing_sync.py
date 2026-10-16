"""The landing steps that move the pull request's head (GH-239, ADR-0048): `sync_master`
and `re_review`, and the `reopen` they and `landing_fix` share.

`sync_master` merges the upstream into the task branch in the task's worktree, never the
product checkout (ADR-0039). A clean merge whose paths are disjoint from the branch's own
carries the Tech Lead's verdict (`landing.review_carries`): `reviewed_head` advances,
`reviewed_head_carried_from` says where it came from, and the push lets the merge gate
re-run the checks and the CI on the new head. Anything else reopens the technical stage
under the existing pull request: the reviewed unit goes back to its Developer (a conflict)
or to its review (`re_review`), the task document says so, and `agentic resume` is spawned
to run it. `deliver` re-delivers to the same pull request and closes the landing entry.

No step ends, or reopens and spawns a resume, with a merge in progress in the task
worktree (GH-267): the resume starts at `integrate`, whose fan-in merge refuses on a
`MERGE_HEAD` and aborts it, throwing the conflicted master merge away unseen. The abort is
`merges_cleared`'s, around the body of each step, and the Developer is told to run the merge
itself."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from ..domain import adr_numbers, landing, rules
from ..domain.adr_numbers import Renaming
from ..domain.roles import pair_for
from ..domain.task import INTEGRATION, ChangeCategory, ChangesRequested, Subtask, TaskState
from . import resume as resuming
from .adr_collision import adr_collision
from .checkouts import Checkout, SetupFailed, task_base, task_checkout
from .landing_plan import LandingContext, entries
from .ports import GitError, Ports, UpstreamMerge, Workspace

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
    # A name that is not a subtask of the document (a blocking record owned by nothing the
    # document knows) reopens the reviewed unit instead.
    names = [unit for unit in units if unit in subtasks]
    if not names:
        reviewed = _unit(state)
        names = [reviewed] if reviewed is not None else []
    units = names
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


class ResumeContext(Protocol):
    """The part of a `LandingContext` that spawning a resume reads: `land`'s recovery branch
    has no step to hand, only the task, its ports and the spawner."""

    @property
    def task_id(self) -> str: ...

    @property
    def ports(self) -> Ports: ...

    @property
    def resume(self) -> Callable[[int], int | None] | None: ...

    @property
    def log(self) -> Callable[[str], None]: ...


def spawn_resume(context: ResumeContext) -> int | None:
    """The pid `agentic resume` was spawned with; `None` when nothing could spawn it (no
    resumer, no issue number) or the launcher answered no pid."""
    number = context.ports.inbox.issue_of(context.task_id)
    if context.resume is None or number is None:
        context.log(f"agentic: land: {context.task_id}: reopened, nothing to resume it with")
        return None
    pid = context.resume(number)
    context.log(f"agentic: land: {context.task_id}: reopened, resume -> pid {pid}")
    return pid


def spawn_and_mark(context: ResumeContext, clock: Callable[[], str] = _now) -> int | None:
    """Spawns the resume and, only when it answered a pid, counts it on the last `landing`
    entry (`resume_spawns`, `resume_spawned_at`): a spawn that never happened is not counted,
    so a later `land` still owes it (`landing.resume_owed`)."""
    pid = spawn_resume(context)
    if pid is None:
        return None
    written = [
        dict(entry) for entry in entries(context.ports.documents.load(context.task_id) or {})
    ]
    if not written:
        return pid
    last = written[-1]
    spawns = last.get("resume_spawns", 0)
    last["resume_spawns"] = (spawns if isinstance(spawns, int) else 0) + 1
    last["resume_spawned_at"] = clock()
    record_landing(context, [landing.entry_of(entry) for entry in written])
    return pid


def record_landing(context: ResumeContext, written: Sequence[Mapping[str, object]]) -> None:
    """The `landing` list as the closure key `land` first wrote it: whatever re-renders the
    document, the list that stands is this one."""
    context.ports.documents.record_closure(context.task_id, {"landing": list(written)})


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
    record_landing(context, marked)
    spawn_and_mark(context)


def _unit(state: TaskState) -> str | None:
    unit = rules.reviewed_unit(state)
    return unit["id"] if unit is not None and "id" in unit else None


class Merges:
    """The merges one landing pass makes in a task worktree: every sha is remembered, so
    `merges_cleared` knows which `MERGE_HEAD` the pass itself left."""

    def __init__(self, workspace: Workspace, path: Path) -> None:
        self._workspace, self._path = workspace, path
        self.passed: set[str] = set()

    def merge_in(self, sha: str) -> UpstreamMerge:
        """Fetch `sha` and merge it into the worktree; a conflict stays in the tree until
        the `merges_cleared` block exits."""
        self.passed.add(sha)
        self._workspace.fetch_commit(sha)
        return self._workspace.merge_upstream_into(self._path, sha)


@contextmanager
def merges_cleared(
    workspace: Workspace, path: Path, owned: Callable[[str], bool]
) -> Iterator[Merges]:
    """The worktree at `path` has no merge of the landing's in progress, on the way in
    (a `MERGE_HEAD` a killed pass left, when `owned` says it is the system's) and on every
    way out (one this pass made). The only caller of `Workspace.abort_merge`: the abort is a
    property of the steps, not of each branch of them. A merge that is not the system's is
    never touched. An abort that fails raises `GitError` out of the block, so nothing
    reopens or spawns on a tree still mid-merge."""
    stale = workspace.merge_head(path)
    if stale is not None and owned(stale):
        workspace.abort_merge(path)
    merges = Merges(workspace, path)
    try:
        yield merges
    finally:
        left = workspace.merge_head(path)
        if left is not None and (left in merges.passed or owned(left)):
            workspace.abort_merge(path)


def _owned(context: LandingContext, state: TaskState) -> Callable[[str], bool]:
    """Whether a `MERGE_HEAD` is one this task's landing merged (`landing.merge_owner`)."""
    return lambda sha: (
        landing.merge_owner(sha, context.upstream.sha, context.head, entries(state)) == "own"
    )


def _bring_in(
    context: LandingContext, state: TaskState, checkout: Checkout, merges: Merges
) -> tuple[str, ...]:
    """The pull request's head in the task worktree, and the paths a conflict left (none for
    a clean one). A foreign push never reached the local branch; merging the head in makes
    the next push a fast-forward."""
    if context.ports.workspace.full_head(checkout.path) == context.head:
        return ()
    return merges.merge_in(context.head).conflicted


def _conflict_reason(paths: Sequence[str], source: str) -> str:
    listed = ", ".join(paths)
    return (
        f"Merging {source} into the task branch conflicted in: {listed}. The merge was aborted "
        f"and the worktree is clean. In this worktree (the task checkout) run `git merge "
        f"{source}`, resolve the conflicts in {listed}, `git add` the files and commit the "
        "merge."
    )


def _failed_reason(what: str, failed: object, source: str) -> str:
    """A reopen reason for a step git or the setup refused: whatever merge was in progress
    was aborted, and the Developer brings `source` in itself, in the task checkout."""
    return (
        f"{what}: {failed}. Any merge in progress was aborted and the worktree is clean. In "
        f"this worktree (the task checkout) run `git merge {source}`, resolve any conflicts, "
        "`git add` the files and commit the merge."
    )


type Reopening = tuple[str, Literal["returned", "delivered"]]


def re_review(context: LandingContext) -> None:
    """A head the Tech Lead did not review (or an approval that is not one): the reviewed
    unit goes back to its review over the head as it is, never carried."""
    state = _state(context)
    unit = _unit(state) if state is not None else None
    if state is None or unit is None:
        context.log(f"agentic: land: {context.task_id}: re_review: no reviewed unit")
        return
    pending = _re_reviewed(context, state)
    # After the block: a reopen always starts from a tree with no merge in progress.
    _reopened(context, state, unit, *pending)


def _head_failed(context: LandingContext, failed: object) -> str:
    return _failed_reason("the pull request head could not be brought in", failed, context.head)


def _re_reviewed(context: LandingContext, state: TaskState) -> Reopening:
    try:
        checkout = task_checkout(state, context.cfg, context.ports)
        with merges_cleared(
            context.ports.workspace, checkout.path, _owned(context, state)
        ) as merges:
            try:
                conflicted = _bring_in(context, state, checkout, merges)
            except GitError as failed:
                return _head_failed(context, failed), "returned"
    except SetupFailed as failed:
        return _head_failed(context, failed), "returned"
    if conflicted:
        return _conflict_reason(conflicted, context.head), "returned"
    reason = (
        f"The pull request head is {context.head}, which the Tech Lead has not reviewed "
        f"({context.step.refusal}): review it."
    )
    return reason, "delivered"


def sync_master(context: LandingContext) -> None:
    """Merge the upstream into the task branch (see the module docstring)."""
    sha = context.upstream.sha
    state = _state(context)
    unit = _unit(state) if state is not None else None
    if state is None or unit is None or sha is None:
        context.log(f"agentic: land: {context.task_id}: sync_master: nothing to sync")
        return
    pending = _synced(context, state, unit, sha)
    if pending is not None:
        _reopened(context, state, unit, *pending)


def _synced(context: LandingContext, state: TaskState, unit: str, sha: str) -> Reopening | None:
    """The sync inside `merges_cleared`: `None` when master was merged, pushed and the
    entry resolved, else why the unit reopens (the caller does it, with the tree clean)."""
    ports = context.ports
    reviewed = state["subtasks"][unit].get("reviewed_head")
    try:
        checkout = task_checkout(state, context.cfg, ports)
    except SetupFailed as failed:
        return _failed_reason(f"syncing {sha} failed", failed, sha), "returned"
    with merges_cleared(ports.workspace, checkout.path, _owned(context, state)) as merges:
        try:
            if conflicted := _bring_in(context, state, checkout, merges):
                return _conflict_reason(conflicted, context.head), "returned"
            merged = merges.merge_in(sha)
        except GitError as failed:
            return _failed_reason(f"syncing {sha} failed", failed, sha), "returned"
        if not merged.clean:
            return _conflict_reason(merged.conflicted, sha), "returned"
        if not _sync_carries(context, state, reviewed, merged.head):
            return f"Master ({sha}) was merged into the branch; review the result as it is.", (
                "delivered"
            )
        try:
            ports.workspace.push(checkout.branch)
        except GitError as failed:
            return f"pushing the synced branch failed: {failed}", "delivered"
        resolved = landing.resolve_open(entries(state), _now(), state.get("cost_usd", 0.0))
        subtasks = _carries(state, unit, reviewed, merged.head, holds=True)
        ports.documents.save({**state, "subtasks": subtasks, "landing": resolved})
        record_landing(context, resolved)
        context.log(
            f"agentic: land: {context.task_id}: master synced, verdict carried to {merged.head}"
        )
    return None


def _carries(
    state: TaskState, unit: str, reviewed: str | None, head: str, *, holds: bool
) -> dict[str, Subtask]:
    """The only writer of `reviewed_head` and `reviewed_head_carried_from` (ADR-0048): the
    state's subtasks with `unit`'s verdict advanced to `head` when `holds` (the domain's
    verdict, `landing.review_carries` for a sync, `landing.renumber_carries` for a
    renumber) and it has one to carry; unchanged otherwise."""
    if not holds or not reviewed:
        return dict(state["subtasks"])
    advanced: Subtask = {
        **state["subtasks"][unit],
        "reviewed_head": head,
        "reviewed_head_carried_from": reviewed,
    }
    return {**state["subtasks"], unit: advanced}


def _sync_carries(
    context: LandingContext, state: TaskState, reviewed: str | None, head: str
) -> bool:
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


def _message(moves: Sequence[tuple[str, str]]) -> str:
    renamed = f"{', '.join(adr_numbers.old_numbers(dict(moves)))} -> "
    renamed += ", ".join(adr_numbers.new_numbers(dict(moves)))
    return (
        f"Renumber ADR {renamed}: master already took the number\n\n"
        "Renamed and rewritten by the landing (ADR-0049); only the ADR files and the lines "
        "the branch added that cite them."
    )


def _changes(workspace: Workspace, head: str, found: Renaming) -> list[landing.LineChange]:
    """The lines `found` changes against what `head` holds: a moved file against its old
    path, under its new one."""
    source = {new: old for old, new in found.moves}
    changes: list[landing.LineChange] = []
    for path, text in found.texts.items():
        before = workspace.show(head, source.get(path, path)) or ""
        changes.extend(landing.line_changes(path, before, text))
    return changes


def renumber_adr(context: LandingContext) -> None:
    """Rename the branch's ADR whose number master took and rewrite what cites it
    (GH-229, ADR-0049): the `Renaming` is `adr_collision`'s, recomputed here against the one
    master sha of the round, written in the task's worktree, committed on exactly its own
    paths and pushed with a lease on the head the decision saw. A lost lease, or anything
    that is no longer a clean renaming, records nothing: the next round decides again."""
    ports, head, sha = context.ports, context.head, context.upstream.sha
    state = _state(context)
    unit = _unit(state) if state is not None else None
    if state is None or unit is None or sha is None:
        context.log(f"agentic: land: {context.task_id}: renumber: nothing to renumber")
        return
    workspace = ports.workspace
    try:
        found = adr_collision(ports, sha, head)
    except GitError as failed:
        context.log(f"agentic: land: {context.task_id}: renumber: collision unreadable: {failed}")
        return
    if not isinstance(found, Renaming):
        context.log(f"agentic: land: {context.task_id}: renumber: no clean renaming any more")
        return
    reviewed = state["subtasks"][unit].get("reviewed_head")
    try:
        checkout = task_checkout(state, context.cfg, ports)
    except SetupFailed as failed:
        context.log(f"agentic: land: {context.task_id}: renumber: not pushed: {failed}")
        return
    with merges_cleared(workspace, checkout.path, _owned(context, state)) as merges:
        try:
            if _bring_in(context, state, checkout, merges):
                context.log(f"agentic: land: {context.task_id}: renumber: head conflicts locally")
                return
            changes = _changes(workspace, head, found)
            for old, _ in found.moves:
                (checkout.path / old).unlink(missing_ok=True)
            for path, text in found.texts.items():
                target = checkout.path / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            relatives = sorted({old for old, _ in found.moves} | set(found.texts))
            if workspace.commit_paths(checkout.path, relatives, _message(found.moves)) is None:
                context.log(f"agentic: land: {context.task_id}: renumber: nothing to commit")
                return
            new_head = workspace.full_head(checkout.path)
            workspace.push(checkout.branch, lease=head)
        except GitError as failed:
            context.log(f"agentic: land: {context.task_id}: renumber: not pushed: {failed}")
            return
    carried = landing.renumber_carries(changes) and rules.same_commit(reviewed, head)
    record: dict[str, str | bool] = {
        "from": ", ".join(adr_numbers.old_numbers(dict(found.moves))),
        "to": ", ".join(adr_numbers.new_numbers(dict(found.moves))),
        "head_before": head,
        "head_after": new_head,
        "carried": carried,
    }
    resolved = landing.resolve_open(entries(state), _now(), state.get("cost_usd", 0.0))
    subtasks = _carries(state, unit, reviewed, new_head, holds=carried)
    ports.documents.save(
        {
            **state,
            "subtasks": subtasks,
            "landing": resolved,
            "adr_renumbered": [*state.get("adr_renumbered", []), record],
        }
    )
    record_landing(context, resolved)
    context.log(
        f"agentic: land: {context.task_id}: renumbered {record['from']} -> {record['to']}, "
        f"pushed {new_head}" + ("" if carried else " (review not carried)")
    )
