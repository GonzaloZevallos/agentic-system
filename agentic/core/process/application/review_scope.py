"""What one review pass covers (GH-589, ADR-0089): the git facts `landing.review_scope`
decides from, read through the `Workspace` port, and the system section a resolution-only pass
puts in front of the reviewer. The decision itself is the domain's; nothing here tests
cleanliness or ancestry to carry an approval."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ..domain import landing, rules
from ..domain.docs import Docs
from ..domain.task import TaskState
from .checkouts import task_base
from .ports import GitError, Ports, RemergeDiffUnsupported, Workspace

StepKind = landing.StepKind
UnreadableCause = Literal["unreadable", "no_remerge_diff"]

RESOLUTION_SECTION = "Review scope: resolution"
"""The system section of a `resolution` pass (a key of the reading state, never persisted into
the document's sections), like `ci_review.CI_SECTION`."""

_INSTRUCTIONS = (
    "This is a resolution-only review. The system merged the upstream into the branch; the "
    "part of the branch you approved earlier stands.",
    "Your verdict covers only the hunks below: the merge resolutions, where the merge commit "
    "differs from what git's own merge of its parents gives, and the commits made after them.",
    "Read the whole file of each hunk at the head with your tools: a hunk alone may hide the "
    "code around it.",
    "What the upstream brought in cleanly is not yours to read: the checks on the head judge it.",
)


def section_text(verdict_text: str) -> str:
    """The body of `RESOLUTION_SECTION`: the three instructions, then `verdict_text`, the
    resolution text, whole."""
    instructions = "\n".join(f"- {line}" for line in _INSTRUCTIONS)
    return f"{instructions}\n\n{verdict_text}"


def accepted_tip(state: TaskState, unit: str, head: str, path: Path, ports: Ports) -> str | None:
    """The head an earlier pass of `unit` accepted, or `None` for a first review: the
    close-time docs commit when `rules.head_reviewed` holds for it and it is an ancestor of
    `head`, else the unit's `reviewed_head`. Raises `GitError`."""
    reviewed = state["subtasks"][unit].get("reviewed_head")
    if not reviewed:
        return None
    close_docs = state.get("close_docs")
    docs_head = close_docs.get("head") if close_docs else None
    if (
        docs_head
        and rules.head_reviewed(reviewed, close_docs, docs_head)
        and ports.workspace.is_ancestor(path, docs_head, head)
    ):
        return docs_head
    return reviewed


def scope_of(
    state: TaskState, unit: str, head: str, path: Path, ports: Ports
) -> tuple[landing.ScopeVerdict, str | None]:
    """What the pass over `head` covers and the accepted tip it starts from (`None` for a
    first review). A `GitError` anywhere gives `full` with `unreadable`, or `no_remerge_diff`
    when git cannot print a remerge-diff; never `carried` or `resolution`."""
    # When `accepted_tip` itself fails the tip is not known; the unit's own `reviewed_head` stands
    # in, so a unit that was reviewed before is never recorded as a `first` review.
    tip: str | None = state["subtasks"][unit].get("reviewed_head") or None
    try:
        tip = accepted_tip(state, unit, head, path, ports)
        facts = _facts(state, tip, head, path, ports)
    except RemergeDiffUnsupported:
        facts = _unreadable(tip, "no_remerge_diff")
    except GitError:
        facts = _unreadable(tip, "unreadable")
    return landing.review_scope(facts), tip


def _unreadable(tip: str | None, cause: UnreadableCause) -> landing.ScopeFacts:
    return landing.ScopeFacts(
        reviewed=tip,
        readable=False,
        unreadable_cause=cause,
        reviewed_is_ancestor=False,
        system_merge=False,
        steps=(),
    )


def _facts(
    state: TaskState, tip: str | None, head: str, path: Path, ports: Ports
) -> landing.ScopeFacts:
    """The facts of the pass over `head`, read in this order: the tip and its ancestry, the
    chain and the kind of each commit, the texts (`merge_resolution`, which can raise
    `RemergeDiffUnsupported`, so that wins over a later `GitError`), the paths of the one-parent
    commits (`touched_paths`), and last the docs block, read once at the task's base and only
    when a step is a regeneration or a merge with text. Any `GitError` is the caller's."""
    if tip is None:
        return landing.ScopeFacts(None, True, None, False, False, ())
    workspace = ports.workspace
    if not workspace.is_ancestor(path, tip, head):
        return landing.ScopeFacts(tip, True, None, False, False, ())
    commits = workspace.first_parent_commits(tip, head)
    upstream = _upstream(state, ports) if any(len(c.parents) > 1 for c in commits) else None
    kinds = [_kind(state, commit, upstream, path, ports) for commit in commits]
    # Text and paths are read only when a resolution or a regeneration could be sent: a chain
    # with neither (or with a foreign merge) is decided before any text, so an old git cannot
    # turn it into `no_remerge_diff`.
    wanted = "foreign_merge" not in kinds and ("upstream_merge" in kinds or "regeneration" in kinds)
    texts = [workspace.merge_resolution(commit.sha) if wanted else "" for commit in commits]
    steps = tuple(
        landing.ScopeStep(
            commit.sha,
            kind,
            text,
            _paths(workspace, commit, text) if wanted else (),
        )
        for commit, kind, text in zip(commits, kinds, texts, strict=True)
    )
    return landing.ScopeFacts(tip, True, None, True, False, steps, _docs(state, steps, ports))


def _paths(workspace: Workspace, commit: rules.Commit, text: str) -> tuple[str, ...]:
    """What the step adds beyond git's own merge of its parents: the paths of a merge's
    remerge-diff (`()` when clean or when they cannot be told), the paths a one-parent commit
    touches."""
    if len(commit.parents) > 1:
        return landing.diff_paths(text) or ()
    return workspace.touched_paths(commit.parents[0], commit.sha)


def _docs(state: TaskState, steps: tuple[landing.ScopeStep, ...], ports: Ports) -> Docs | None:
    """The product's docs block at the task's base, never at the head: a branch cannot widen
    what it is allowed to regenerate. Read only when a step could be covered by it."""
    if not any(
        step.kind == "regeneration" or (step.kind == "upstream_merge" and step.text)
        for step in steps
    ):
        return None
    return ports.stack.docs(task_base(state, ports))


def _upstream(state: TaskState, ports: Ports) -> str:
    """The upstream the open landing pass merged, else the base branch's tip as the landing
    reads it. Raises `GitError` when there is none to compare against."""
    entries = rules.live_landing(state)
    if entries and not rules.entry_closed(entries[-1]):
        recorded = entries[-1].get("upstream")
        if isinstance(recorded, str) and recorded:
            return recorded
    tip = ports.workspace.upstream().sha
    if tip is None:
        raise GitError("the upstream tip could not be read")
    return tip


def _kind(
    state: TaskState, commit: rules.Commit, upstream: str | None, path: Path, ports: Ports
) -> StepKind:
    """A one-parent commit is a `regeneration` only when the system recorded its sha as the
    regeneration of a reintegration (`rules.same_commit`); who wrote it, what it says and the
    paths it touches decide nothing here."""
    if len(commit.parents) < 2:
        recorded = (row.get("regeneration") for row in state.get("reintegrations", []))
        if any(rules.same_commit(commit.sha, sha) for sha in recorded):
            return "regeneration"
        return "own_commit"
    if upstream is not None and all(
        ports.workspace.is_ancestor(path, parent, upstream) for parent in commit.parents[1:]
    ):
        return "upstream_merge"
    return "foreign_merge"
