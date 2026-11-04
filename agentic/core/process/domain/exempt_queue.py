"""The queue of the one exempt child the machine allows (GH-500, ADR-0045 addendum).

A merge and a `land_checks` land take no slot of the cap, but at most one of them runs on the
whole machine at a time. Picking the lowest issue number every round starved the rest: a merge
that refuses at once is still a candidate next round and wins again. This module is the pure
side of the fix: the one order of the candidates (`rank`, `ordered`), the questions that gate
publishing a wait (`startable`, `behind`), the refusal a poller does not repeat on the same
head and master (`backed_off`) and the attempt records a fact has made stale (`stale_keys`).
It reads no file and calls no port; `rules.poll_plan` and the poller call it."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple

# The module, not the name: `rules` imports this one too, and reads it at call time.
from . import rules

EXEMPT_WAIT_TTL = timedelta(minutes=15)
"""How long a published exempt wait holds the other products after its writer last rewrote it:
a poller that died never holds the shared slot for longer. A live poller rewrites it every
round while a candidate of its own could start, so it never expires under one."""

EXEMPT_REFUSAL_BACKOFF = timedelta(minutes=30)
"""How long a refusal that writes nothing is honoured on the same head and master before the
merge is tried again: a changed head or master releases it earlier (`backed_off`)."""

EXEMPT_STARVED_AFTER = timedelta(minutes=20)
"""How long a candidate may wait behind `exempt_busy` or a yield before the poller says so
once, naming the holder of the slot."""

BACKOFF_REASONS: frozenset[str] = frozenset({"ci_untrusted_no_dispatch"})
"""The refusals `backed_off` honours: those that wrote nothing and ask for no other step.
`merge_conflict` hands over to the landing instead, and `master_moved` is left to the queue
order, because its master never matches the next round's."""

NEVER = datetime.min.replace(tzinfo=UTC)
"""The rank instant of a candidate with no record yet: it goes before every attempted one."""

ExemptKind = Literal["merge", "land"]
ExemptKey = tuple[str, int, ExemptKind]
"""`(product, issue, kind)`: the key of an attempt record, never the issue number alone."""

_KIND_RANK: dict[ExemptKind, int] = {"merge": 0, "land": 1}


class ExemptRank(NamedTuple):
    """The sort key of a candidate: `since` is when it last tried, or was first seen, or
    `NEVER`; then a merge before a land; then the root and the issue, so equal instants fall
    back on today's order (all merges before lands, lowest issue first)."""

    since: datetime
    kind: int
    root_key: str
    issue: int


@dataclass(frozen=True)
class ExemptCandidate:
    """One merge or exempt land that could take the slot: `root_key` names the tasks root of
    its poller (`rules.root_key`), `first_seen` when the poller first listed it and
    `last_attempt` when a child for it last started."""

    kind: ExemptKind
    product: str
    root_key: str
    issue: int
    first_seen: datetime | None = None
    last_attempt: datetime | None = None


@dataclass(frozen=True)
class ExemptAttempt:
    """The two instants one poller keeps for a candidate, `(product, issue, kind)` keyed:
    what `rules.poll_plan` reads to order the candidates of a product."""

    first_seen: datetime | None = None
    last_attempt: datetime | None = None


@dataclass(frozen=True)
class ExemptWait:
    """The fact one poller publishes while a candidate of its product could take the slot:
    `since` is the `rank(...).since` of its head, `written_at` when it last wrote the record."""

    product: str
    root_key: str
    written_at: datetime
    since: datetime


@dataclass(frozen=True)
class ExemptFacts:
    """What the poller read about the task of an attempt record: whether it is `closed`, its
    `pull_request` (`None` or empty when it has none) and that pull request's `pr_state`
    (`OPEN`, `MERGED`, `CLOSED`, or `None` when unreadable)."""

    closed: bool
    pull_request: str | None
    pr_state: str | None


def rank(candidate: ExemptCandidate) -> ExemptRank:
    """The one order of the exempt candidates, across products: who waited longest since
    its last attempt goes first. The same value gives the `since` a poller publishes."""
    return ExemptRank(
        candidate.last_attempt or candidate.first_seen or NEVER,
        _KIND_RANK[candidate.kind],
        candidate.root_key,
        candidate.issue,
    )


def ordered(candidates: Iterable[ExemptCandidate]) -> list[ExemptCandidate]:
    """The candidates in `rank` order: the first is the one that starts when the slot is free."""
    return sorted(candidates, key=rank)


def startable(
    candidate: ExemptCandidate,
    *,
    backed_off: bool,
    busy: bool,
    held: bool,
    own_live_exempt: bool,
) -> bool:
    """Whether `candidate` could start this round, so that its poller may publish a wait for
    it: not in backoff, not busy (an exempt child live on the machine), not in a `held` round
    and its own product has no live exempt child. A candidate that could not start is never
    published, so it never holds another product back."""
    return not (backed_off or busy or held or own_live_exempt)


def behind(own: ExemptRank | None, others: Sequence[ExemptWait]) -> bool:
    """Whether this product yields to another product's published wait: some record of
    `others` (the fresh records of the other roots) ranks before this product's own head
    (`own`), a tie going to the lower `root_key`. A product with no head of its own (`None`)
    is behind any fresh record. The oldest wait is behind none, so it starts first."""
    if own is None:
        return bool(others)
    return any((w.since, w.root_key) < (own.since, own.root_key) for w in others)


def backed_off(
    refusal: Mapping[str, object] | None,
    head: str | None,
    master: str | None,
    now: datetime,
) -> bool:
    """Whether the merge is not tried again this round: `refusal` is a well formed
    `{reason, head, master, at}` of a reason in `BACKOFF_REASONS`, both shas name the commits
    asked about under `rules.same_commit`, and less than `EXEMPT_REFUSAL_BACKOFF` has passed
    since `at`. A missing or malformed record, a moved head or master, an expired backoff or
    an `at` in the future releases it: a refusal is never read for another pair."""
    if not isinstance(refusal, Mapping):
        return False
    reason, recorded_head = refusal.get("reason"), refusal.get("head")
    recorded_master, at = refusal.get("master"), refusal.get("at")
    if reason not in BACKOFF_REASONS:
        return False
    if not (isinstance(recorded_head, str) and isinstance(recorded_master, str)):
        return False
    if not isinstance(at, str):
        return False
    try:
        written = datetime.fromisoformat(at)
    except ValueError:
        return False
    if written.tzinfo is None:
        return False
    if not (rules.same_commit(recorded_head, head) and rules.same_commit(recorded_master, master)):
        return False
    return timedelta(0) <= now - written < EXEMPT_REFUSAL_BACKOFF


def stale_keys(
    records: Collection[ExemptKey], facts: Mapping[tuple[str, int], ExemptFacts]
) -> list[ExemptKey]:
    """The attempt records a fact has made useless: the task is closed, has no pull request,
    or its pull request is merged or closed unmerged (`rules.pr_abandoned`). Facts are keyed by
    `(product, issue)`. A record with no facts, or whose task is merely absent from this
    round's candidates, is never stale: only a fact deletes a record."""
    stale: list[ExemptKey] = []
    for key in records:
        known = facts.get((key[0], key[1]))
        if known is None:
            continue
        if (
            known.closed
            or not known.pull_request
            or known.pr_state == "MERGED"
            or rules.pr_abandoned(known.pr_state)
        ):
            stale.append(key)
    return sorted(stale)
