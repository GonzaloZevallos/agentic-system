"""The landing order of a product (GH-588, GH-621): which delivered pull request is integrated and
landed now. Pure: members in, the head's issue out. The only place pull requests of a product are
put in order; the caller builds the members (a pull request recorded, no `closed_at`, open) and
asks `head`, or `order` / `advance` when it has `now`.

A slow head never holds the order (GH-621). Each member carries the time it has been head
(`charged_s`, plus the stretch since it was last seen as head, capped by `GAP_MAX` so a stopped
poller is not charged) and the unproductive landing steps it took on its sha. A head that reaches
`HEAD_BUDGET` of time, or `HEAD_STEPS_MAX` unproductive steps, yields: it goes behind every member
that has not yielded and its charge starts again. A busy head (a live child) and a `merge_due` one
(its backoff is over, only the merge is left) never yield: the stall watcher (ADR-0057) and the
per-attempt cap bound a busy head, and yielding it would run two merges against master. A lone
eligible member never yields either.

Tiers, first match wins: (1) a member that holds its turn (busy, seen as head within `GAP_MAX`);
(2) a live pass among the members that have not yielded; (3) the oldest `queued_at` among them;
(4) the members that yielded, fewest yields first, then the oldest `yielded_at`, then issue.

Pure and clockless: `now` is always a parameter, every instant is tz-aware and every elapsed
time is clamped to `>= 0`. `observed`, `left_head` and `yielded` are the only writers of the
charge fields of a `Member`. Without `now`, `head` is the order of GH-588, unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import MISSING, dataclass, fields, replace
from datetime import datetime, timedelta
from typing import Literal

Turn = Literal["head", "waiting"]
"""Whether a pull request is the head of its product's order (`head`: it is integrated, checked
and landed) or waits for its turn (`waiting`: it is left as it is)."""

YieldReason = Literal["budget_time", "budget_steps"]
"""Why a head gave up its turn: it spent `HEAD_BUDGET` of time or `HEAD_STEPS_MAX` steps."""

HEAD_BUDGET = timedelta(minutes=45)
"""How long a member may be head, in observed time, before it yields."""

HEAD_SEEN_EVERY = timedelta(minutes=5)
"""How often the caller records that the head was seen (`observed`)."""

GAP_MAX = timedelta(minutes=15)
"""The longest stretch since `seen_at` that counts as time as head, and that still holds a turn."""

HEAD_STEPS_MAX = 2
"""How many unproductive landing steps on its sha make a head yield."""


@dataclass(frozen=True)
class Member:
    """One delivered pull request in the order. `queued_at` is its parsed `landing_queued_at`
    (`None`: unstamped), `live_pass` that a landing pass is in flight on it, `waits_on_human` that
    the task belongs to a person (`landing.waits_on_human`).

    The rest are the head-budget facts (GH-621), all defaulted: `turn_at` the start of the current
    turn (the caller reads it as `since` for `unproductive_steps`), `charged_s` the seconds already
    spent as head, `seen_at` when it was last seen as head, `yielded_at` and `yields` when and how
    many times it gave up its turn, `steps_on_head` the unproductive steps on its current sha,
    `busy` a live child, `merge_due` that only the merge is left, `waits_on_ci` and
    `account_paused` that it cannot advance now. A member with `yielded_at=None` has not yielded,
    whatever `yields` says.

    Scope: time is per member, so a new push does not reset it; steps are per sha.

    Precedence: `waits_on_human` always drops a member; `waits_on_ci` and `account_paused` drop
    only a member that is not busy (a busy one is working and must not lose its turn).

    Accepted starvation: a yielded member waits behind every member that has not yielded, later
    arrivals included. The wait is bounded by one `HEAD_BUDGET` for each member ahead of it.

    Only `observed`, `left_head` and `yielded` write `charged_s`, `seen_at`, `turn_at`,
    `yielded_at`, `yields` and `steps_on_head`; no other code builds such a state by hand."""

    issue: int
    queued_at: datetime | None
    live_pass: bool
    waits_on_human: bool
    turn_at: datetime | None = None
    charged_s: float = 0.0
    seen_at: datetime | None = None
    yielded_at: datetime | None = None
    yields: int = 0
    steps_on_head: int = 0
    busy: bool = False
    merge_due: bool = False
    waits_on_ci: bool = False
    account_paused: bool = False


@dataclass(frozen=True)
class Yield:
    """A head that gave up its turn in a call to `order`: `member` is its state after the yield,
    `reason` why."""

    member: Member
    reason: YieldReason


@dataclass(frozen=True)
class Order:
    """The result of `order`: the issue at the head (`None`: nobody eligible) and the members that
    yielded to reach it, in the order they did."""

    head: int | None
    yielded: tuple[Yield, ...]


def instant(value: object) -> datetime | None:
    """An ISO-8601 string with an offset as an aware datetime; aware or nothing: a naive string,
    an unparsable one or a value that is not a string gives `None`."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _elapsed(since: datetime, now: datetime) -> timedelta:
    """`now - since`, never negative (a clock that went back is not negative time)."""
    return max(now - since, timedelta(0))


def head_time(m: Member, now: datetime) -> timedelta:
    """The time `m` has been head: what it was charged, plus the stretch since it was last seen as
    head, capped at `GAP_MAX`."""
    spent = timedelta(seconds=m.charged_s)
    if m.seen_at is not None:
        spent += min(_elapsed(m.seen_at, now), GAP_MAX)
    return spent


def holds_turn(m: Member, now: datetime) -> bool:
    """A busy member seen as head within `GAP_MAX` keeps the turn. Never derived from `turn_at`."""
    return m.busy and m.seen_at is not None and _elapsed(m.seen_at, now) <= GAP_MAX


def unproductive_steps(
    entries: Iterable[Mapping[str, object]], head: object, since: datetime | None
) -> int:
    """How many landing steps on sha `head`, at or after `since`, led nowhere: a step closed
    without being resolved, or one a later entry of the same step on the same sha repeats. 0 when
    `since` is `None`. Entries whose `at` is missing, unreadable or naive are skipped.

    It under-counts on purpose: the time budget is the brake. The step limit does not catch a loop
    that changes the sha (#611)."""
    if since is None:
        return 0
    count = 0
    later: set[object] = set()
    for entry in reversed(list(entries)):
        if entry.get("head") != head:
            continue
        step = entry.get("step")
        at = instant(entry.get("at"))
        if step and at is not None and at >= since:
            unresolved = bool(entry.get("closed_at")) and not entry.get("resolved_at")
            if unresolved or step in later:
                count += 1
        if step:
            later.add(step)
    return count


def over_budget(m: Member, now: datetime) -> YieldReason | None:
    """Why `m` must give up the head, `None` when it need not. A busy head is bounded by the stall
    watcher (ADR-0057) and the per-attempt cap, not by this module (yielding it would run two
    merges against master); a `merge_due` one has only the merge left."""
    if m.busy or m.merge_due:
        return None
    if head_time(m, now) >= HEAD_BUDGET:
        return "budget_time"
    if m.steps_on_head >= HEAD_STEPS_MAX:
        return "budget_steps"
    return None


def observed(m: Member, now: datetime) -> Member:
    """`m`, seen as head at `now`: the time since it was last seen is charged and the turn is
    anchored if it had none. Applied only to the head of this round; part 2 never calls it on a
    member that was not head and never writes these fields itself."""
    return replace(
        m,
        charged_s=head_time(m, now).total_seconds(),
        seen_at=now,
        turn_at=m.turn_at if m.turn_at is not None else now,
    )


def left_head(m: Member, now: datetime) -> Member:
    """`m`, no longer head (or skipped) at `now`: what it spent is charged, it is not seen any
    more, and it keeps its turn. Applied only to a member that was head (`seen_at` set); part 2
    never calls it on a member that was not head and never writes these fields itself."""
    return replace(m, charged_s=head_time(m, now).total_seconds(), seen_at=None)


def yielded(m: Member, reason: YieldReason, now: datetime) -> Member:
    """`m` after giving up the head: the only definition of what a yield does to a member. The
    charge and the turn start again, and it goes behind every member that has not yielded."""
    del reason
    return replace(
        m,
        charged_s=0.0,
        turn_at=None,
        seen_at=None,
        steps_on_head=0,
        yielded_at=now,
        yields=m.yields + 1,
    )


def _first(members: Iterable[Member]) -> Member | None:
    """The first by `(queued_at, issue)`; an unstamped member goes after every stamped one."""
    ordered = sorted(
        members,
        key=lambda m: (m.queued_at is None, m.queued_at or datetime.min, m.issue),
    )
    return ordered[0] if ordered else None


def _choose(eligible: list[Member], now: datetime) -> Member | None:
    """The member the tiers put first among `eligible`."""
    fresh = [m for m in eligible if m.yielded_at is None]
    chosen = _first(m for m in eligible if holds_turn(m, now))
    chosen = chosen or _first(m for m in fresh if m.live_pass) or _first(fresh)
    if chosen is not None:
        return chosen
    gave_up = [((m.yields, at, m.issue), m) for m in eligible if (at := m.yielded_at) is not None]
    return min(gave_up, key=lambda pair: pair[0])[1] if gave_up else None


def order(members: Iterable[Member], now: datetime) -> Order:
    """The order of `members` at `now`. Eligible: not `waits_on_human`, and not `waits_on_ci` or
    `account_paused` unless busy. The head is the first by the tiers in the module docstring; if
    it is over budget and another member is eligible it yields (`Yield`, at most once per member
    per call, so several over-budget members in front may yield in one call) and the head is
    chosen again. A lone eligible member never yields.

    It chooses a head; it does not prevent a second live child (the poller's job). `Order.yielded`
    is the report: the state to persist is what `advance` returns. A yielded member with
    `live_pass` that is not busy waits its turn; its pass resumes later via `rules.landing_pass`."""
    eligible = [
        m
        for m in members
        if not m.waits_on_human and (m.busy or not (m.waits_on_ci or m.account_paused))
    ]
    done: list[Yield] = []
    chosen = _choose(eligible, now)
    while chosen is not None and len(eligible) > 1:
        reason = over_budget(chosen, now)
        if reason is None or any(y.member.issue == chosen.issue for y in done):
            break
        gave_up = Yield(yielded(chosen, reason, now), reason)
        done.append(gave_up)
        eligible = [gave_up.member if m is chosen else m for m in eligible]
        chosen = _choose(eligible, now)
    return Order(chosen.issue if chosen is not None else None, tuple(done))


def advance(members: Iterable[Member], now: datetime) -> tuple[Order, tuple[Member, ...]]:
    """`order` at `now` and the members to persist, in input order. Each starts from its yielded
    state if it yielded in this call, else as given; then the final head gets `observed` (so a
    yielded member that ends as head is anchored in the same call), a member that was not yielded
    and was seen as head gets `left_head`, any other is unchanged. A head that vanished is not in
    `members` and is not folded."""
    given = tuple(members)
    result = order(given, now)
    gave_up = {y.member.issue: y.member for y in result.yielded}
    folded: list[Member] = []
    for m in given:
        state = gave_up.get(m.issue, m)
        if m.issue == result.head:
            state = observed(state, now)
        elif m.issue not in gave_up and m.seen_at is not None:
            state = left_head(state, now)
        folded.append(state)
    return result, tuple(folded)


def _reject_new_facts(members: Iterable[Member]) -> None:
    """Raises `ValueError` naming the issue and the field when a member carries a head-budget fact
    off its default: without `now` nothing could read it."""
    for m in members:
        for f in fields(Member):
            if f.default is MISSING:
                continue
            if getattr(m, f.name) != f.default:
                raise ValueError(
                    f"issue {m.issue}: member field {f.name!r} is set but head() has no `now`"
                )


def head(members: Iterable[Member], now: datetime | None = None) -> int | None:
    """The issue at the head of the order, `None` when nobody is eligible. With `now`, the head of
    `order(members, now)`. Without it, the order of GH-588: a member that waits on a human is never
    the head, even with a live pass or the oldest stamp; among the rest a member with a live pass
    wins (the earliest by the order below if several); else the oldest `queued_at`, ties by issue,
    unstamped members last (by issue). Without `now` a member with a head-budget fact off its
    default raises `ValueError`."""
    if now is not None:
        return order(members, now).head
    listed = list(members)
    _reject_new_facts(listed)
    eligible = [m for m in listed if not m.waits_on_human]
    chosen = _first(m for m in eligible if m.live_pass) or _first(eligible)
    return chosen.issue if chosen is not None else None


def turn_of(issue: int, head_issue: int | None) -> Turn:
    """`head` when `issue` leads the order, else `waiting`."""
    return "head" if head_issue is not None and issue == head_issue else "waiting"
