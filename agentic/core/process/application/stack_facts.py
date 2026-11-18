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
either is missing the ancestry fact is `None`, which `stacking.refusal` reads as a refusal.

`parent_link` and `parent_chain` (GH-898) read the same parent for `stacking.pause_cause`: the hold
part of the poller is their caller, none exists yet. They read the issue, the parent's document and
its run lock only when ancestry says the recorded head is not in the upstream, each once per issue
per memo. Whether a parent waits on a person is `landing.waits_on_human`'s alone. A parent whose
owner already answered stays `parent_escalated` until its own `land` step writes the release and
the next window frees the child: `poll._released` is deliberately not reproduced here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import cast

from agentic.shared.configuration import Configuration

from ..domain import landing, stacking
from ..domain.task import StackParent, TaskState
from . import escalate, landing_plan
from .ports import (
    GitError,
    IssueComment,
    IssueState,
    LockRecord,
    Ports,
    RunLock,
    TrackerCallFailed,
)


@dataclass(frozen=True)
class _Unread:
    """A read that failed or found nothing, kept in the memo so that a failing tracker, document
    or lock is asked once per window, not once per child."""


_UNREAD = _Unread()


@dataclass
class StackMemo:
    """The reads of one decision window, keyed by what they asked. `exists` is `fetch_commit` by
    sha; `issue` is the parent issue's whole state; `docs` and `locks` are the parent's document
    and run lock by parent issue; `comments` are the parent issue's comments, read only for a
    document of the stranded shape, by parent issue; `links` is `parent_link` by `(issue,
    recorded head)`, never by issue alone: two chains can name one issue at different heads. The
    three ancestry maps are
    keyed `(parent_head, ref)` and hold `None` when git could not tell."""

    exists: dict[str, bool] = field(default_factory=dict)
    issue: dict[int, IssueState | _Unread] = field(default_factory=dict)
    docs: dict[int, dict[str, object] | _Unread] = field(default_factory=dict)
    locks: dict[int, LockRecord | None | _Unread] = field(default_factory=dict)
    comments: dict[int, list[IssueComment] | _Unread] = field(default_factory=dict)
    links: dict[tuple[int, str], stacking.ParentLink] = field(default_factory=dict)
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


def _issue_state(ports: Ports, memo: StackMemo, number: int) -> IssueState | _Unread:
    """The issue as the tracker states it, read once per memo; `_UNREAD` for a failed read."""
    if number not in memo.issue:
        try:
            memo.issue[number] = ports.tracker.issue_state(number)
        except (TrackerCallFailed, ValueError, OSError, KeyError):
            memo.issue[number] = _UNREAD
    return memo.issue[number]


def _issue(ports: Ports, memo: StackMemo, number: int) -> tuple[str, str | None]:
    """What `stack_facts` keeps of an issue: its state and the merge commit that closed it;
    `unknown` and no merge for a read that failed."""
    read = _issue_state(ports, memo, number)
    if isinstance(read, _Unread):
        return ("unknown", None)
    return (read.state, read.closing_merge_commit)


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


def _document(ports: Ports, memo: StackMemo, record: StackParent) -> dict[str, object] | _Unread:
    """The parent's document, read once per parent issue; a missing or unreadable one is
    `_UNREAD`."""
    number = record["issue"]
    if number not in memo.docs:
        try:
            loaded = ports.documents.load(record["task_id"])
        except (OSError, ValueError):
            loaded = None
        memo.docs[number] = _UNREAD if loaded is None else loaded
    return memo.docs[number]


def _lock(lock: RunLock, memo: StackMemo, record: StackParent) -> LockRecord | None | _Unread:
    """The parent's run lock record, read once per parent issue; `None` is no lock."""
    number = record["issue"]
    if number not in memo.locks:
        try:
            memo.locks[number] = lock.read(record["task_id"])
        except OSError:
            memo.locks[number] = _UNREAD
    return memo.locks[number]


def _stranded(ports: Ports, memo: StackMemo, record: StackParent, doc: dict[str, object]) -> bool:
    """`landing_plan.stranded_now`, with its one tracker read memoised per parent issue: the
    comments are read only for a document that already has the stranded shape, and a tracker that
    cannot answer leaves the document escalated (not stranded)."""
    if not landing.stranded_diagnose(doc, False):
        return False
    number = record["issue"]
    if number not in memo.comments:
        try:
            memo.comments[number] = ports.tracker.timestamped_comments(record["task_id"])
        except (ValueError, OSError):
            memo.comments[number] = _UNREAD
    read = memo.comments[number]
    return not isinstance(read, _Unread) and landing_plan.stranded(doc, read)


def _waits_on_human(
    ports: Ports,
    memo: StackMemo,
    record: StackParent,
    issue: IssueState | _Unread,
    doc: dict[str, object] | _Unread,
) -> bool | None:
    """`landing.waits_on_human` of the parent: the one definition, the head being the last
    landing entry's. A stranded diagnose nobody was told of is not a person's: the document is
    read without `escalated`, as the landing order reads it. `None` when the document, the issue
    or its labels could not be read: an unread label set is not a removed `needs-human`."""
    if isinstance(doc, _Unread) or isinstance(issue, _Unread) or issue.labels is None:
        return None
    entries = landing_plan.all_entries(doc)
    last = entries[-1].get("head") if entries else None
    head = last if isinstance(last, str) else None
    view: Mapping[str, object] = doc
    if _stranded(ports, memo, record, doc):
        view = {key: value for key, value in doc.items() if key != "escalated"}
    return landing.waits_on_human(
        # `view` is the parent's own document (or it less `escalated`), which is what a
        # `TaskState` is; `TaskDocuments.load` hands it back as a plain dict.
        cast(TaskState, view),
        head,
        None,
        spent=landing_plan.spent(doc, head, None),
        stopped=False,
        needs_human=escalate.NEEDS_HUMAN in issue.labels,
        stranded=False,
    )


def _read_link(
    ports: Ports,
    cfg: Configuration,
    lock: RunLock,
    record: StackParent,
    tip: str,
    memo: StackMemo,
) -> stacking.ParentLink:
    parent_head = record["head"]
    in_upstream: bool | None = None
    if _exists(ports, memo, parent_head):
        in_upstream = _ancestor(ports, cfg, memo.in_tip, parent_head, tip)
    if in_upstream is not False:
        # The parent landed, or git could not tell: nothing else of it is read.
        return stacking.ParentLink(
            record, in_upstream, None, None, None, None, stacking.Unreadable()
        )
    issue = _issue_state(ports, memo, record["issue"])
    doc = _document(ports, memo, record)
    held = _lock(lock, memo, record)
    return stacking.ParentLink(
        record=record,
        in_upstream=in_upstream,
        state=None if isinstance(issue, _Unread) else issue.state,
        state_reason=None if isinstance(issue, _Unread) else issue.state_reason,
        waits_on_human=_waits_on_human(ports, memo, record, issue, doc),
        stopped=None
        if isinstance(held, _Unread)
        else held is not None and held.stopped_deliberately,
        next_record=stacking.Unreadable()
        if isinstance(doc, _Unread)
        else stacking.parse_stack_parent(doc),
    )


def parent_link(
    ports: Ports,
    cfg: Configuration,
    lock: RunLock,
    record: StackParent,
    tip: str,
    memo: StackMemo,
) -> stacking.ParentLink:
    """The facts `stacking.pause_cause` reads of one parent, memoised by `(issue, recorded
    head)`. A parent whose head is in `tip` costs no issue, document or lock read; a fact whose
    read failed is `None`. The hold part of the poller is the caller; a parent whose owner
    answered stays escalated until its own `land` step writes the release."""
    key = (record["issue"], record["head"])
    if key not in memo.links:
        memo.links[key] = _read_link(ports, cfg, lock, record, tip, memo)
    return memo.links[key]


def parent_chain(
    ports: Ports,
    cfg: Configuration,
    lock: RunLock,
    record: StackParent,
    tip: str,
    memo: StackMemo,
) -> list[stacking.ParentLink]:
    """The links from `record` up through each parent's own `stack_parent`, ending at the first
    link that has none or cannot be followed. It stops at the first repeated issue and once it
    has more links than `pause_cause` reads, so that `pause_cause` sees the cycle or the
    overflow and answers `unreadable`. Reads cost O(distinct ancestors) per memo."""
    chain: list[stacking.ParentLink] = []
    seen: set[int] = set()
    current = record
    while True:
        repeated = current["issue"] in seen
        seen.add(current["issue"])
        link = parent_link(ports, cfg, lock, current, tip, memo)
        chain.append(link)
        following = link.next_record
        if repeated or len(chain) > stacking.PAUSE_DEPTH:
            return chain
        if isinstance(following, stacking.Absent | stacking.Unreadable):
            return chain
        current = following
