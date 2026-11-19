"""The stacked start's merge truth table, pure (GH-855, GH-859).

A stacked child is a task whose pull request is built on its parent's branch. It must never
merge before its parent: this module holds the one place that says so. `parse_stack_parent` is
the only reader of the document's `stack_parent` record and `refusal` the only comparison of
what that record names with what upstream and the child hold; no other module reads the key or
compares those shas. It reads no file and calls no port: the caller gathers `StackFacts`.

`StackFacts.parent_in_upstream` is whether `record.head` is an ancestor of the upstream
(`git merge-base --is-ancestor`, read by the caller), never what the parent issue's state says.
The landing only adds commits on top of the parent's head (`sync_master` is a merge, never a
rebase), so a landing fix on the parent does not displace it. This assumes the repository merges
with a merge commit: a squash or rebase merge leaves the recorded head outside upstream, and the
child is then orphaned.

The wait on an unfetchable head (GH-885): a child whose own head or whose parent's recorded head
cannot be fetched (`StackFacts.heads_fetched is False`) waits `STACK_UNFETCHABLE_WAIT` from the
first round that saw it, then the landing escalates it (`stack_parent_unmerged`, class
`questions`). `unfetchable_patch` is the only writer of the document's `stack_unfetchable:
{head, since}`, `parse_unfetchable` the only reader and `unfetchable_expired` the only
comparison of `since` with a clock; all three are pure, `now` is a parameter. The patch's rows,
in order:

- R1 the document is escalated: clear (no clock runs while a person owns the escalation);
- R2 `heads_fetched is None`: nothing was read, no change;
- R3 `heads_fetched is True`: clear;
- R4 a record of another head than `pr_head`: restart `{pr_head, now}`;
- R5 a live `landing` entry `escalate`/`stack_parent_unmerged` carries `stack_since` equal to the
  record's `since`: the wait was released, restart (a release buys a new bounded wait);
- R6 no record, or an unreadable one: start `{pr_head, now}`, whatever the parent issue state;
- R7 otherwise: no change.

It returns `{}` whenever the target equals the current state, and clears with
`{"stack_unfetchable": None}` (`record_closure` cannot delete a key). It reads no `pause_log`, so
a poller retake never resets the clock.

`pause_cause` and `pause_note` (GH-898) only produce the cause a stacked child waits for and its
text; they gate nothing. `refusal` stays the only gate on merging. Where the note is posted, when
a paused child is skipped and the rule that no merge runs while `pause_cause` is not `none` belong
to the poller hold part, whose ADR lives with it. "Moves again" means the parent leaves the cause:
it is reopened, resumed, or its escalation is released.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

# The module, not the names: `rules` imports this one too, and reads it at call time.
from . import rules
from .task import StackParent, StackUnfetchable


@dataclass(frozen=True)
class Absent:
    """The document has no `stack_parent` key: a task that was never stacked."""


@dataclass(frozen=True)
class Unreadable:
    """The key is there and its value is no record of a parent: it names nothing safe to wait
    for. Carries no text of the document, so nothing free ever reaches a comment."""


@dataclass(frozen=True)
class StackFacts:
    """What `refusal` reads. `record` is `None` when the document's record was unreadable.
    `parent_state` is the parent issue's state, `unknown` when it could not be read.
    `parent_in_upstream` says `record.head` is an ancestor of the upstream and `child_has_parent`
    that the child's head holds it; each is `None` when git could not tell."""

    record: StackParent | None
    parent_state: Literal["open", "closed", "unknown"]
    parent_in_upstream: bool | None
    child_has_parent: bool | None
    # GH-885: whether the child's own head and the recorded parent head can both be
    # fetched (`fetch_commit`); `None` when not read. `unfetchable_expired` and
    # `unfetchable_since` are the wait's clock read from the document, filled only in a turn where
    # `heads_fetched is False`. Each `None` changes nothing in the reducer or the decision.
    heads_fetched: bool | None = None
    unfetchable_expired: bool | None = None
    unfetchable_since: str | None = None


def _iso8601(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def parse_stack_parent(raw: Mapping[str, object]) -> Absent | StackParent | Unreadable:
    """The document's `stack_parent`: `Absent` when the key is missing (decided on `in`, never
    on `get`: a present `None` is unreadable, not absent), the record when every field is
    well-formed, `Unreadable` otherwise: a non-dict, an `issue` that is not an int (a bool is
    not one), a `task_id` that is not a str, a `head` that is not a full commit id or a
    `started_at` that is not ISO-8601."""
    if "stack_parent" not in raw:
        return Absent()
    value = raw["stack_parent"]
    if not isinstance(value, dict):
        return Unreadable()
    issue = value.get("issue")
    task_id = value.get("task_id")
    head = value.get("head")
    started_at = value.get("started_at")
    if (
        not isinstance(issue, int)
        or isinstance(issue, bool)
        or not isinstance(task_id, str)
        or not isinstance(head, str)
        or not rules.full_commit_id(head)
        or not isinstance(started_at, str)
        or not _iso8601(started_at)
    ):
        return Unreadable()
    return StackParent(issue=issue, task_id=task_id, head=head, started_at=started_at)


def stacked(raw: Mapping[str, object]) -> bool:
    """A view of `parse_stack_parent` (GH-897): whether the document is stacked, an unreadable
    record included. It gates the restack rows, which an orphaned task is owed too."""
    return not isinstance(parse_stack_parent(raw), Absent)


def stacked_on(raw: Mapping[str, object]) -> int | None:
    """A view of `parse_stack_parent` (GH-897): the parent issue, only for a readable record.
    It gates the pull request line, which never names a parent it could not read."""
    parsed = parse_stack_parent(raw)
    return parsed["issue"] if isinstance(parsed, dict) else None


def facts_of(raw: Mapping[str, object]) -> StackFacts | None:
    """The one builder of unread facts (GH-860): `None` for a document that never was stacked,
    so that it makes no stacking read and decides as it always did; otherwise the record (`None`
    when unreadable) with every fact unknown. The caller that reads the facts replaces them."""
    parsed = parse_stack_parent(raw)
    if isinstance(parsed, Absent):
        return None
    return StackFacts(
        record=None if isinstance(parsed, Unreadable) else parsed,
        parent_state="unknown",
        parent_in_upstream=None,
        child_has_parent=None,
    )


# Whether the parent's closing merge holds the recorded head: `contains` it does, `excludes` it
# does not, `none` the issue has no merge commit, `unread` git could not tell.
ClosingMerge = Literal["contains", "excludes", "none", "unread"]


def parent_state_of(state: str, closing: ClosingMerge) -> Literal["open", "closed", "unknown"]:
    """The parent issue's state as `refusal` reads it. `closed` only when the issue is closed and
    its closing merge does not hold the recorded head (or there is none): the parent's work was
    left out. A closed issue whose merge holds the head is `unknown`: the parent landed and the
    caller's upstream is stale, so the child waits a round instead of reading as orphaned."""
    match state, closing:
        case "OPEN", _:
            return "open"
        case "CLOSED", "excludes" | "none":
            return "closed"
        case "CLOSED", "contains" | "unread":
            return "unknown"
        case _:
            return "unknown"


def unread_facts() -> StackFacts:
    """A record that is present and no fact read: what `rules.stack_probe_wanted` substitutes to
    ask whether reading them could change the decision. The record's fields are never read."""
    placeholder = StackParent(issue=0, task_id="", head="0" * 40, started_at="1970-01-01")
    return StackFacts(
        record=placeholder, parent_state="unknown", parent_in_upstream=None, child_has_parent=None
    )


def refusal(stack: StackFacts, conflicts: bool | None) -> rules.MergeRefusal | None:
    """The one truth table of a stacked child's merge, `None` when nothing stacking-related
    stands in the way. Rows, in order:

    1. record unreadable: `stack_base_orphaned`;
    2. the parent's head is in upstream: the child holds it, `None` whatever the issue state
       says; lacks it, `stack_restack_due` -- or `None` when `conflicts is True`, so that
       `merge_conflict` answers; unknown, `stack_parent_unmerged`;
    3. it is not in upstream: the parent issue `closed`, `stack_base_orphaned`; `open` or
       `unknown`, `stack_parent_unmerged`;
    4. ancestry unknown: `stack_parent_unmerged`.

    Row 2 never reads `parent_state`. Anything not known is a refusal: an unreadable fact never
    merges."""
    if stack.record is None:
        return "stack_base_orphaned"
    if stack.parent_in_upstream is True:
        if stack.child_has_parent is True:
            return None
        if stack.child_has_parent is False:
            return None if conflicts is True else "stack_restack_due"
        return "stack_parent_unmerged"
    if stack.parent_in_upstream is False and stack.parent_state == "closed":
        return "stack_base_orphaned"
    return "stack_parent_unmerged"


PauseCause = Literal["parent_escalated", "parent_stopped", "parent_abandoned", "unreadable", "none"]
NotedCause = Literal["parent_escalated", "parent_stopped", "parent_abandoned"]

PAUSE_DEPTH = 10
"""The most links of a parent chain `pause_cause` reads: a longer one is `unreadable`."""

_NOTE_TEXT: Mapping[NotedCause, str] = {
    "parent_escalated": "escalated",
    "parent_stopped": "was stopped",
    "parent_abandoned": "was closed as not planned",
}


@dataclass(frozen=True)
class ParentLink:
    """One parent of a stacked child's chain, as the caller read it. `record` is what the
    child (or the parent below) names; `in_upstream` is whether `record.head` is in the upstream
    (`None`: git could not tell). Every other fact is `None` when its read failed or was not made
    because `in_upstream` is not `False`; `waits_on_human` is `landing.waits_on_human` of the
    parent's document, `stopped` a deliberate stop of its run. `next_record` is the parent's own
    `stack_parent`: `Absent` ends the chain, `Unreadable` cannot be followed."""

    record: StackParent
    in_upstream: bool | None
    state: str | None
    state_reason: str | None
    waits_on_human: bool | None
    stopped: bool | None
    next_record: Absent | StackParent | Unreadable


def _link_cause(link: ParentLink) -> PauseCause | None:
    """What this one link says: a cause, `unreadable`, `none` (the parent landed: nothing
    above it matters), or `None` when it is idle and the next link decides."""
    if link.in_upstream is True:
        return "none"
    if link.in_upstream is None:
        return "unreadable"
    if link.waits_on_human is True:
        return "parent_escalated"
    if link.stopped is True:
        return "parent_stopped"
    if link.state == "CLOSED" and link.state_reason == "NOT_PLANNED":
        return "parent_abandoned"
    facts = (link.state, link.state_reason, link.waits_on_human, link.stopped)
    if any(fact is None for fact in facts) or isinstance(link.next_record, Unreadable):
        return "unreadable"
    return None


def pause_cause(chain: Sequence[ParentLink]) -> PauseCause:
    """Why a stacked child waits on its parents, `none` when it does not. The links are read in
    order, each by `_link_cause`; a repeated issue or an 11th link is `unreadable`, and so is a
    chain that ends with a parent still to read. Anything not known is `unreadable`, never
    `none`. It decides nothing about merging: that is `refusal`'s."""
    seen: set[int] = set()
    for position, link in enumerate(chain):
        issue = link.record["issue"]
        if position >= PAUSE_DEPTH or issue in seen:
            return "unreadable"
        seen.add(issue)
        cause = _link_cause(link)
        if cause is not None:
            return cause
        if isinstance(link.next_record, Absent):
            return "none"
    return "none" if not chain else "unreadable"


def pause_note(parent: int, cause: NotedCause) -> str:
    """The text a paused child's issue carries: the parent and the cause, and when it ends."""
    return (
        f"Paused: stacked on #{parent}, which {_NOTE_TEXT[cause]}. "
        f"Resumes when #{parent} moves again."
    )


def parent_unmerged(stack: StackFacts, conflicts: bool | None) -> bool:
    """A view of `refusal`: the parent has not reached upstream, or that is not known."""
    return refusal(stack, conflicts) == "stack_parent_unmerged"


def held(stack: StackFacts | None) -> bool:
    """Whether the landing order keeps a task out of the Cabeza and the Carril rápido (GH-838):
    it is stacked and its parent's head has not reached upstream, `refusal` being the only
    judge. `None` (never stacked) is never held. No `conflicts` argument: `refusal` reads it only
    where it answers `stack_restack_due` or `None`, so it could not change the answer.

    Doubts hold: unknown ancestry, an unknown tracker state, and an unfetchable head inside its
    wait. An unfetchable head whose wait expired is released, so that it reaches the Cabeza and
    `landing_step` escalates it. `stack_base_orphaned` is human-owned (`waits_on_human` drops it),
    and `stack_restack_due` is not held, so that the restack `sync_master` can run."""
    if stack is None or refusal(stack, None) != "stack_parent_unmerged":
        return False
    return not (stack.heads_fetched is False and stack.unfetchable_expired is True)


def base_landed(stack: StackFacts, conflicts: bool | None) -> bool:
    """A view of `refusal`: nothing stacking-related stands in the way."""
    return refusal(stack, conflicts) is None


def restack_due(stack: StackFacts, conflicts: bool | None) -> bool:
    """A view of `refusal`: the parent landed and the child must take upstream once."""
    return refusal(stack, conflicts) == "stack_restack_due"


STACK_UNFETCHABLE_WAIT = timedelta(hours=12)
"""How long a child whose head cannot be fetched waits before the landing escalates it (GH-885)."""


def _aware(value: object) -> datetime | None:
    """`value` as an ISO-8601 instant with an offset; `None` for anything else, naive included."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def parse_unfetchable(raw: Mapping[str, object]) -> StackUnfetchable | None | Unreadable:
    """The document's `stack_unfetchable`, the only reader of it: `None` when the key is missing
    or `None` (no record), the record when `head` is a str and `since` an ISO-8601 instant with
    an offset, `Unreadable` otherwise (`{}`, a non-mapping, a naive `since`)."""
    value = raw.get("stack_unfetchable")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return Unreadable()
    head = value.get("head")
    since = value.get("since")
    if not isinstance(head, str) or _aware(since) is None or not isinstance(since, str):
        return Unreadable()
    return StackUnfetchable(head=head, since=since)


def unfetchable_expired(record: StackUnfetchable | None | Unreadable, now: datetime) -> bool:
    """Whether the wait ran out: `now - since >= STACK_UNFETCHABLE_WAIT`. `False` without a
    readable record. The only comparison of `since` with a clock."""
    if record is None or isinstance(record, Unreadable):
        return False
    since = _aware(record["since"])
    return since is not None and now - since >= STACK_UNFETCHABLE_WAIT


def _released(raw: Mapping[str, object], record: StackUnfetchable) -> bool:
    """R5: a live `landing` entry escalated this very clock (`stack_since` is its `since`)."""
    return any(
        entry.get("step") == "escalate"
        and entry.get("refusal") == "stack_parent_unmerged"
        and entry.get("stack_since") == record["since"]
        for entry in rules.live_landing(raw)
    )


def unfetchable_patch(
    raw: Mapping[str, object], facts: StackFacts, pr_head: str | None, now: datetime
) -> dict[str, object]:
    """The change to the document's `stack_unfetchable` for this round, the only writer of it
    (rows R1-R7 in the module docstring). `{}` whenever the target equals the current state, and
    for a child without a `pr_head`. Pure: `now` (timezone-aware) is the caller's."""
    if pr_head is None:
        return {}
    record = parse_unfetchable(raw)
    clear: dict[str, object] = {} if record is None else {"stack_unfetchable": None}
    if raw.get("escalated"):
        return clear
    if facts.heads_fetched is None:
        return {}
    if facts.heads_fetched:
        return clear
    start: dict[str, object] = {
        "stack_unfetchable": StackUnfetchable(head=pr_head, since=now.isoformat())
    }
    if record is None or isinstance(record, Unreadable):
        return start
    if record["head"] != pr_head or _released(raw, record):
        return start
    return {}
