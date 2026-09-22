"""Archivado (ADR-0011, sixth addendum; `CONTEXT.md`): what `--fresh` does with a
previous run's branches instead of building the next one on top of them.

`archive_previous_run` is the whole use case: commit what no Developer got to commit
in every worktree of the task, take the worktrees out, then rename the task branch and
its subtask branches under one `archive/run<N>-<system_commit>/` prefix, the task
branch first -- that order is what makes a cut-short archive legible to a retry
(`rules.next_archive_run`'s own three cases). Naming and the run number are pure
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

from ..domain import rules
from .ports import Ports

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
    branches left to rename, the document and the worktrees already someone else's
    doing. `document_kept` says whether the document that landed in the commit is
    still there once the product's own `.gitignore` is asked -- meaningless, and left
    at its default, whenever `written` is `False`."""

    prefix: str | None
    written: bool
    document_kept: bool = True


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

    if head is not None:
        checkout = workspace.task_checkout(task_id)
        doc_path = ports.documents.write_into(task_id, checkout.path)
        for worktree in workspace.worktrees(task_id):
            workspace.commit_all(worktree, f"Archive run {n} of {task_id}: {worktree.name}")
        if doc_path is not None:
            relative = doc_path.relative_to(checkout.path).as_posix()
            document_kept = not workspace.is_ignored(checkout.path, relative)
        workspace.cleanup(task_id)
        workspace.prune()
        workspace.rename_branch(task_branch, rules.archive_ref(task_branch, n, system_commit))
        written = True

    for branch in rules.branches_of_task(live, task_id):
        if branch == task_branch:
            continue
        workspace.rename_branch(branch, rules.archive_ref(branch, n, system_commit))

    return ArchiveResult(prefix=prefix, written=written, document_kept=document_kept)
