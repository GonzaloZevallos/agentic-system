"""The queue of the exempt children, one per product (GH-500, GH-1226, ADR-0045 addendum).

A merge and a `land_checks` land take no slot of the cap, but each product runs at most one of
them at a time and the machine at most `EXEMPT_SLOTS_MAX` together. Picking the lowest issue
number every round starved the rest: a merge that refuses at once is still a candidate next
round and wins again. This module is the pure side of the fix: the count of the live exempt
children (`live_exempt`), the one order of the candidates (`rank`, `ordered`), the questions
that gate publishing a wait (`startable`, `behind`), the refusal a poller does not repeat on
the same head and master (`backed_off`) and the attempt records a fact has made stale
(`stale_keys`). It reads no file and calls no port; `rules.poll_plan` and the poller call it."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple

# The module, not the name: `rules` imports this one too, and reads it at call time.
from . import rules

EXEMPT_SLOTS_MAX = 2
"""How many exempt children may be live on the machine, one per product. It does not protect
the CPU: the Cupo de chequeo ledger does (ADR-0070). It bounds how many products land at once."""

EXEMPT_WAIT_TTL = timedelta(minutes=15)
"""How long a published exempt wait counts against the other products after its writer last
rewrote it: a poller that died never holds a slot for longer. A live poller rewrites it every
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
    """What one poller keeps for a candidate, `(product, issue, kind)` keyed: the two instants
    `rules.poll_plan` reads to order the candidates of a product, and whether the starvation
    line was already said for it (`starved_reported`, reset when a child starts for it)."""

    first_seen: datetime | None = None
    last_attempt: datetime | None = None
    starved_reported: bool = False


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
    held: bool,
    own_live_exempt: bool,
) -> bool:
    """Whether `candidate` could start this round, so that its poller may publish a wait for
    it: not in backoff, not in a `held` round and its own product has no live exempt child.
    Another product's live child does not stop it: the slot is per product, so the wait is
    published (and seen by the others) while that child runs. A candidate that could not start
    is never published, so it never holds another product back."""
    return not (backed_off or held or own_live_exempt)


def behind(own: ExemptRank | None, others: Sequence[ExemptWait], free: int) -> bool:
    """Whether this product yields this round. `free` is how many of the `EXEMPT_SLOTS_MAX`
    machine slots no live child holds. With none free it yields. Otherwise it yields when the
    fresh waits of `others` (the other roots) that rank before its own head (`own`, a tie
    going to the lower `root_key`) fill the free slots: at least `free` of them. A product with
    no head of its own (`None`) ranks after every wait. The oldest wait is behind none while
    a slot is free, so it starts first."""
    return free <= 0 or len(ahead(own, others)) >= free


def ahead(own: ExemptRank | None, others: Sequence[ExemptWait]) -> list[ExemptWait]:
    """The fresh waits of `others` that rank before this product's own head (`own`), all of
    them when it has none: the one criterion of `behind`, also what the poller names."""
    if own is None:
        return list(others)
    mine = (own.since, own.root_key)
    return [w for w in others if (w.since, w.root_key) < mine]


type ExemptHolder = tuple[str, str, rules.RunKind]
type ExemptLive = tuple[int, int, tuple[ExemptHolder, ...], tuple[ExemptHolder, ...]]
"""What `live_exempt` counted, `(own, machine, holders, mine)`: `own` live exempt children of
this product, `machine` of all products, `holders` the `(product, task_id, kind)` of those of
the other products and `mine` of this product's, each sorted by product then task. A plain
tuple, so the generated class diagrams under `docs/architecture/generated/` stay as they are."""


def _by_product_task(children: Iterable[ExemptHolder]) -> tuple[ExemptHolder, ...]:
    return tuple(sorted(children, key=lambda h: (h[0], h[1])))


def live_exempt(running: Iterable[tuple[str, str, rules.RunKind]], product: str) -> ExemptLive:
    """Count the live exempt children (`rules.counts_against_cap` false) of `product` and of
    the machine from `(product, task_id, kind)` triples, and list them: the one place that
    does. The lists are sorted here, so the result does not depend on the order of `running`."""
    machine = 0
    holders: list[ExemptHolder] = []
    mine: list[ExemptHolder] = []
    for who, task_id, kind in running:
        if rules.counts_against_cap(kind):
            continue
        machine += 1
        (mine if who == product else holders).append((who, task_id, kind))
    return len(mine), machine, _by_product_task(holders), _by_product_task(mine)


def _written_at(
    refusal: Mapping[str, object] | None, head: str | None, master: str | None
) -> datetime | None:
    """When a well formed `{reason, head, master, at}` refusal of a reason in `BACKOFF_REASONS`
    was written, if both its shas name the commits asked about under `rules.same_commit`;
    otherwise None: a refusal is never read for another pair."""
    if not isinstance(refusal, Mapping):
        return None
    reason, recorded_head = refusal.get("reason"), refusal.get("head")
    recorded_master, at = refusal.get("master"), refusal.get("at")
    if reason not in BACKOFF_REASONS:
        return None
    if not (isinstance(recorded_head, str) and isinstance(recorded_master, str)):
        return None
    if not isinstance(at, str):
        return None
    try:
        written = datetime.fromisoformat(at)
    except ValueError:
        return None
    if written.tzinfo is None:
        return None
    if not (rules.same_commit(recorded_head, head) and rules.same_commit(recorded_master, master)):
        return None
    return written


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
    written = _written_at(refusal, head, master)
    if written is None:
        return False
    return timedelta(0) <= now - written < EXEMPT_REFUSAL_BACKOFF


def backoff_ends(
    refusal: Mapping[str, object] | None, head: str | None, master: str | None
) -> datetime | None:
    """The instant the backoff of `refusal` runs out on this head and master, or None when it
    holds nothing for this pair. The starvation clock starts there: the rounds a merge spent in
    backoff are `backoff`, not `exempt_busy`, and must not count as waiting for the slot."""
    written = _written_at(refusal, head, master)
    return None if written is None else written + EXEMPT_REFUSAL_BACKOFF


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
