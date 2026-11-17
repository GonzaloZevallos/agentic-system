"""Reads the facts of a stacked child's parent from git and the tracker (GH-860).

`stacking.refusal` is the one truth table; this module only performs the reads it needs and hands
their results to `stacking.parent_state_of`: it never parses the document and never compares
shas itself. `stacking.facts_of` builds the unread facts, `stack_facts` upgrades them.

Every read is memoised in a `StackMemo`, so that N children of one parent cost one tracker read
and one existence check of the parent per decision window, never N. The memo is per window (a
poller round, one `assess_merge` call), never module-global and never carried between rounds: a
parent's issue and a tip change between rounds.

A missing object is never `False`. `Workspace.is_ancestor` answers `False` for a sha the checkout
has no object for, so every sha that goes into it was first confirmed by `fetch_commit`; when
either is missing the ancestry fact is `None`, which `stacking.refusal` reads as a refusal."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from agentic.shared.configuration import Configuration

from ..domain import stacking
from .ports import GitError, Ports, TrackerCallFailed

# What the memo keeps of an issue: its state and the merge commit that closed it. A read that
# failed is kept as `UNREAD`, so a failing tracker is asked once per window, not once per child.
_UNREAD: tuple[str, str | None] = ("unknown", None)


@dataclass
class StackMemo:
    """The reads of one decision window, keyed by what they asked. `exists` is `fetch_commit` by
    sha; `issue` is the parent issue's `(state, closing_merge_commit)`; the three ancestry maps
    are keyed `(parent_head, ref)` and hold `None` when git could not tell."""

    exists: dict[str, bool] = field(default_factory=dict)
    issue: dict[int, tuple[str, str | None]] = field(default_factory=dict)
    in_tip: dict[tuple[str, str], bool | None] = field(default_factory=dict)
    in_head: dict[tuple[str, str], bool | None] = field(default_factory=dict)
    in_merge: dict[tuple[str, str], bool | None] = field(default_factory=dict)


def _exists(ports: Ports, memo: StackMemo, sha: str) -> bool:
    if sha not in memo.exists:
        memo.exists[sha] = ports.workspace.fetch_commit(sha)
    return memo.exists[sha]


def _ancestor(
    ports: Ports,
    cfg: Configuration,
    seen: dict[tuple[str, str], bool | None],
    sha: str,
    ref: str,
) -> bool | None:
    """Whether `sha` is on `ref`; `None` for a git failure. Callers confirmed both objects."""
    key = (sha, ref)
    if key not in seen:
        try:
            seen[key] = ports.workspace.is_ancestor(cfg.product_dir, sha, ref)
        except GitError:
            seen[key] = None
    return seen[key]


def _issue(ports: Ports, memo: StackMemo, number: int) -> tuple[str, str | None]:
    if number not in memo.issue:
        try:
            state = ports.tracker.issue_state(number)
        except (TrackerCallFailed, ValueError, OSError, KeyError):
            memo.issue[number] = _UNREAD
        else:
            memo.issue[number] = (state.state, state.closing_merge_commit)
    return memo.issue[number]


def _closing(
    ports: Ports,
    cfg: Configuration,
    memo: StackMemo,
    parent_head: str,
    merge_commit: str | None,
) -> stacking.ClosingMerge:
    """Whether the merge commit that closed the parent holds the recorded head."""
    if merge_commit is None:
        return "none"
    if not _exists(ports, memo, merge_commit):
        return "unread"
    held = _ancestor(ports, cfg, memo.in_merge, parent_head, merge_commit)
    if held is None:
        return "unread"
    return "contains" if held else "excludes"


def stack_facts(
    ports: Ports,
    cfg: Configuration,
    unread: stacking.StackFacts,
    tip: str,
    pr_head: str,
    memo: StackMemo,
) -> stacking.StackFacts:
    """`unread` with its facts read: `parent_in_upstream` against `tip`, `child_has_parent`
    against `pr_head`, and the parent's state. A record that is `None` comes back as it was,
    with no read. The tracker is asked only when git does not already say the parent landed, and
    the closing merge only for a closed parent that is not in `tip`."""
    record = unread.record
    if record is None:
        return unread
    parent_head = record["head"]
    in_upstream: bool | None = None
    has_parent: bool | None = None
    # The child's history carries the recorded head, which is what makes an orphaned parent
    # detectable; `False` from either short-circuits, and no ancestry is asked of a missing sha.
    fetched = _exists(ports, memo, pr_head) and _exists(ports, memo, parent_head)
    if fetched:
        in_upstream = _ancestor(ports, cfg, memo.in_tip, parent_head, tip)
        if in_upstream is True:
            has_parent = _ancestor(ports, cfg, memo.in_head, parent_head, pr_head)
    state = "unknown"
    closing: stacking.ClosingMerge = "unread"
    if in_upstream is not True:
        state, merge_commit = _issue(ports, memo, record["issue"])
        if state == "CLOSED" and in_upstream is False:
            closing = _closing(ports, cfg, memo, parent_head, merge_commit)
    return replace(
        unread,
        parent_state=stacking.parent_state_of(state, closing),
        parent_in_upstream=in_upstream,
        child_has_parent=has_parent,
        heads_fetched=fetched,
    )
