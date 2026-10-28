"""Archivado (ADR-0011, sixth addendum; `CONTEXT.md`): what `--fresh` does with a
previous run's branches instead of building the next one on top of them.

`archive_previous_run` is the whole use case: commit what no Developer got to commit
in every worktree of the task, take the worktrees out, then rename the task branch, commit
the task document onto the archived task ref and rename its subtask branches, all under one
`archive/run<N>-<system_commit>/` prefix, the task branch first -- that order is what makes
a cut-short archive legible to a retry (`rules.next_archive_run`'s own three cases).
The document is the one path under `rules.UNSTAGEABLE` the system ever commits
(`Workspace.commit_archived_document`, plumbing onto a ref under `archive/`, GH-420): no
checkout is written and no live branch ever carries it. Naming and the run number are pure
(`domain/rules.py`); this module only decides which port verb runs when, over the
`Workspace` and `TaskDocuments` ports, never git or the filesystem directly.

The live task branch, never the document, is what says there is something to archive:
asking the document first would let `Workspace.task_checkout` fabricate the very branch
this archives, on a task that escalated before it had one. A document that cannot be
read does not refuse either -- `--fresh` is the command a fouled-up task reaches for,
and refusing over the file that describes the mess would only send the human back to a
hand-run script."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import document, rules
from ..domain.reaping import ReapedProcess
from .ports import GitError, Ports
from .release import release_task

# How the system's comment on a `--fresh` archive starts: a resume looks past it, the
# same as it does `escalate.ESCALATED`, `deliver.READY` and `discard.DISCARDED`.
STARTED_OVER = "Started over: "

# The namespace every archived ref lives under, whichever run or commit named it
# (`rules.archive_prefix`'s own "archive/run<N>-<commit>"): listing under this alone,
# never a per-run prefix, is what lets `rules.next_archive_run` see every run this task
# has archived before, to pick the next `N` or to reuse an unfinished one's.
_ARCHIVE_NAMESPACE = "archive/"


@dataclass(frozen=True)
class ArchiveResult:
    """What one call to `archive_previous_run` did. `prefix` is `None` for a task with
    nothing live to archive -- the only case that calls no port verb beyond a read.
    `written` is whether this call itself committed the document and the worktrees
    (the fresh-archive case); `False` also covers the retry that only had subtask
    branches left to rename, the worktrees already someone else's doing. `document_kept`
    says whether the archived task ref holds the task document after this call: `False`
    for a task with no live document and for a commit that failed. A retry that has no
    document to commit leaves it at its default. `reaped` is what the release stopped
    before the worktrees went (GH-282, ADR-0052); nothing is written to a document."""

    prefix: str | None
    written: bool
    document_kept: bool = True
    reaped: tuple[ReapedProcess, ...] = ()


def archive_previous_run(task_id: str, ports: Ports) -> ArchiveResult:
    """Archive what a previous run of `task_id` left, in the order the damage of an
    interruption is smallest to largest: commit every worktree, take the worktrees out,
    rename the task branch, then its subtask branches. Idempotent by construction
    (`Workspace.rename_branch`'s own contract): a retry after a `GitError` cut a rename
    short reuses the same `N` and finishes what is left live, never re-doing what
    already moved.

    A `ValueError` or an `OSError` loading the document leaves `system_commit`
    `"unknown"` and changes nothing else here: the name is all it is ever used for."""
    workspace = ports.workspace
    task_branch = rules.task_branch(task_id)

    try:
        raw = ports.documents.load(task_id)
    except (ValueError, OSError):
        raw = None
    system_commit = (raw or {}).get("system_commit") or "unknown"

    # `branch_head` proves the branch exists before anything below ever asks for its
    # checkout: `task_checkout` is idempotent-creating, and calling it on a branch that
    # is not there would mint the very branch this is meant to archive.
    head = workspace.branch_head(task_branch)
    live = workspace.branches(task_branch)
    archived_refs = workspace.branches(_ARCHIVE_NAMESPACE)
    n = rules.next_archive_run(archived_refs, live, task_id)
    if n is None:
        return ArchiveResult(prefix=None, written=False)

    prefix = rules.archive_prefix(n, system_commit)
    written = False
    document_kept = True
    reaped: tuple[ReapedProcess, ...] = ()

    archived_task = rules.archive_ref(task_branch, n, system_commit)
    if head is not None:
        for worktree in workspace.worktrees(task_id):
            workspace.commit_all(worktree, f"Archive run {n} of {task_id}: {worktree.name}")
        reaped = release_task(ports, task_id)
        workspace.prune()
        workspace.rename_branch(task_branch, archived_task)
        document_kept = _commit_document(task_id, n, archived_task, ports)
        written = True
    elif (earlier := _earlier_task_ref(archived_refs, task_id, n)) is not None:
        # The task branch moved in a cut-short attempt that never reached the document.
        text = ports.documents.text(task_id)
        if text is not None:
            document_kept = _commit_document(task_id, n, earlier, ports, text=text)

    for branch in rules.branches_of_task(live, task_id):
        if branch == task_branch:
            continue
        workspace.rename_branch(branch, rules.archive_ref(branch, n, system_commit))

    return ArchiveResult(prefix=prefix, written=written, document_kept=document_kept, reaped=reaped)


def _earlier_task_ref(archived_refs: list[str], task_id: str, n: int) -> str | None:
    """The archived task branch of run `n` that a previous, cut-short attempt renamed, under
    whatever system commit it read then."""
    for ref in rules.archived_task_branches(archived_refs, task_id):
        if ref.startswith(f"archive/run{n}-"):
            return ref
    return None


def _commit_document(
    task_id: str, n: int, ref: str, ports: Ports, *, text: str | None = None
) -> bool:
    """Whether `ref` holds the task's document after this: committed here, or already there.
    `False` for a task with no document and for a commit git refused -- the archive goes on,
    the live copy is still on disk."""
    relative = document.relative_path(task_id).as_posix()
    text = ports.documents.text(task_id) if text is None else text
    if text is None or not rules.archivable_document(relative, task_id):
        return False
    try:
        ports.workspace.commit_archived_document(
            ref,
            relative,
            text,
            f"Archive run {n} of {task_id}: the task document",
        )
    except GitError:
        return False
    return True
