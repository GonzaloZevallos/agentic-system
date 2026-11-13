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

LANE_SYNCS_MIN = 2
"""The fewest syncs a head must have left for a lane to open: one the lane forces on the head
(it syncs master over the member's merge), and one left for the head's own next move."""

LANE_MERGES_MAX = 20
"""How many `lane_merges` entries a head's document keeps: the newest."""

LANE_RECORD_WINDOW = timedelta(days=7)
"""How long after its last write a member's document is still looked at for a merge to record."""

INTEGRATING_KINDS = frozenset({"land_model", "role"})
"""The kinds of child that integrate: `role` and `land_model` children run on the head's branch,
and the head's landing then syncs master, paid from its sync budget. `merge` and `land_checks`
judge a fixed (head, master) pair and change neither."""

LaneReason = Literal[
    "head_not_integrating",
    "head_paths_unknown",
    "head_sync_budget",
    "holds_master_unknown",
    "lacks_master",
    "paths_overlap",
    "member_paths_unknown",
]
"""Why a member that is `merge_now` and clean has no lane beside the head."""


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
    `yielded_at`, `yields` and `steps_on_head`; no other code builds such a state by hand.

    The fast-lane facts (GH-681), all defaulted and all read by the caller: `merge_now` that the
    decision is `merge` and there is no backoff, `clean` that the pull request is clean,
    `holds_master` that its branch already holds master, `integrating` that a live child of an
    integrating kind (`integrates`) runs on it, `paths` the files its diff touches and
    `head_syncs_left` the syncs its head budget still allows. `paths`, `clean` and `holds_master`
    are `None` when the read failed or was not attempted, never `frozenset()` or `False` as a
    stand-in: `None` is never clean, never holding master and never disjoint, while an empty
    `frozenset()` is a read, an empty diff. `paths` is a snapshot taken when the caller read it.
    The lane's safety rests on the head's own `sync_master` seeing any overlap, which is why
    `head_syncs_left` is a required fact. A semantic interaction between files that do not
    overlap is not detected."""

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
    merge_now: bool = False
    clean: bool | None = None
    holds_master: bool | None = None
    integrating: bool = False
    paths: frozenset[str] | None = None
    head_syncs_left: int | None = None


@dataclass(frozen=True)
class Yield:
    """A head that gave up its turn in a call to `order`: `member` is its state after the yield,
    `reason` why."""

    member: Member
    reason: YieldReason


@dataclass(frozen=True)
class Order:
    """The result of `order`: the issue at the head (`None`: nobody eligible) and the members that
    yielded to reach it, in the order they did. `lane` is the one member that may be integrated
    beside the head (GH-681), `None` when there is none. A busy head never yields and a head that
    yielded in this call fails `holds_turn`, so the head as passed in and the head after yields
    agree whenever a lane exists."""

    head: int | None
    yielded: tuple[Yield, ...]
    lane: int | None = None


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


def integrates(kind: str) -> bool:
    """Whether a child of `kind` integrates on the head's branch (`INTEGRATING_KINDS`)."""
    return kind in INTEGRATING_KINDS


def lane_ready(m: Member) -> bool:
    """Whether `m` has every fact of its own for the lane: `merge_now`, clean, holding master, its
    paths read, and nothing that keeps it from advancing. It does not check that `m` is eligible
    in `order`, nor `yielded_at`; the head conditions are `lane_reason`'s."""
    return (
        m.merge_now
        and m.clean is True
        and m.holds_master is True
        and m.paths is not None
        and not (m.waits_on_human or m.busy or m.live_pass or m.waits_on_ci or m.account_paused)
    )


def _head_obstacle(head_: Member | None, now: datetime) -> LaneReason | frozenset[str]:
    """Why `head_` opens no lane, or its paths when it does: the only place the head conditions
    are written (busy and seen within `GAP_MAX`, integrating, paths read, `LANE_SYNCS_MIN`
    syncs). A `str` is the reason; a `frozenset` is the head's paths, read and known."""
    if head_ is None or not (holds_turn(head_, now) and head_.integrating):
        return "head_not_integrating"
    if head_.paths is None:
        return "head_paths_unknown"
    if head_.head_syncs_left is None or head_.head_syncs_left < LANE_SYNCS_MIN:
        return "head_sync_budget"
    return head_.paths


def lane_reason(member: Member, head_: Member | None, now: datetime) -> LaneReason | None:
    """Why `member`, `merge_now` and clean, has no lane beside `head_`: the head's conditions
    first, then the member's own. `None` for a member that is not `merge_now` and clean (it gets no
    reason) and for one with no obstacle of its own: it never means "has the lane", which is
    `Order.lane`."""
    if not member.merge_now or member.clean is not True:
        return None
    taken = _head_obstacle(head_, now)
    if isinstance(taken, str):
        return taken
    if member.holds_master is None:
        return "holds_master_unknown"
    if not member.holds_master:
        return "lacks_master"
    if member.paths is None:
        return "member_paths_unknown"
    if member.paths & taken:
        return "paths_overlap"
    return None


def _lane(eligible: list[Member], chosen: Member | None, now: datetime) -> int | None:
    """The member that gets the lane beside the final head `chosen`, `None` when none does: the
    first by `(queued_at, issue)` that is `lane_ready` and whose paths are disjoint from the
    head's."""
    taken = _head_obstacle(chosen, now)
    if isinstance(taken, str):
        return None
    pick = _first(
        m
        for m in eligible
        if m is not chosen and lane_ready(m) and m.paths is not None and not m.paths & taken
    )
    return pick.issue if pick is not None else None


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
    return Order(
        chosen.issue if chosen is not None else None,
        tuple(done),
        _lane(eligible, chosen, now),
    )


def advance(members: Iterable[Member], now: datetime) -> tuple[Order, tuple[Member, ...]]:
    """`order` at `now` and the members to persist, in input order. Each starts from its yielded
    state if it yielded in this call, else as given; then the final head gets `observed` (so a
    yielded member that ends as head is anchored in the same call), a member that was not yielded
    and was seen as head gets `left_head`, any other is unchanged. A head that vanished is not in
    `members` and is not folded. `Order.lane` passes through as `order` gave it."""
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


def resumed(members: Iterable[Member], now: datetime) -> tuple[Member, ...]:
    """`members` as the round should read them at `now`: a busy head left unobserved for longer
    than `GAP_MAX` (a held round, or a poller that stopped, skipped the rounds that would have
    recorded it) keeps its turn without paying the gap. Only the member with the most recent
    `seen_at` can be it, and only if it is busy and nobody else was seen within `GAP_MAX`; it
    comes back with `seen_at=now` and `charged_s` unchanged. Every other member is unchanged.
    Applied once per call of the evaluation, before `advance`; never by the writer."""
    given = tuple(members)
    seen = [m for m in given if m.seen_at is not None]
    if not seen:
        return given
    latest = max(seen, key=lambda m: (m.seen_at or datetime.min, -m.issue))
    gap = _elapsed(latest.seen_at, now) if latest.seen_at is not None else timedelta(0)
    if not latest.busy or gap <= GAP_MAX:
        return given
    if any(
        m is not latest and m.seen_at is not None and _elapsed(m.seen_at, now) <= GAP_MAX
        for m in seen
    ):
        return given
    return tuple(replace(m, seen_at=now) if m is latest else m for m in given)


YIELD_MARK = "head_yields"
"""The key `turn_patch` sets, to the member's new count of yields, when the patch records a yield:
the caller replaces it with the list of `{at, head, reason}` it appends to."""


def turn_patch(before: Member, after: Member, now: datetime) -> dict[str, object]:
    """The head-budget keys to write for one member of a round: `before` as the document read it
    and `after` what `advance` made of it, `{}` when nothing is to be written. The only decision
    of whether and what to write: part 2 callers never build these states by hand.

    - a head with no `seen_at`: `head_seen_at` (and `head_turn_at` if it had none);
    - a head seen `HEAD_SEEN_EVERY` ago or more (a busy head `resumed` refreshed included):
      `head_charged_s` and `head_seen_at`; seen less ago: `{}` (the write is throttled);
    - a member that was head (`seen_at` set) and no longer is, or was skipped: `head_charged_s`
      folded and `head_seen_at` cleared, the turn and the charge kept;
    - a yield (`after.yielded_at` newer than `before.yielded_at`): the turn, the charge and the
      sighting cleared (or set, if it ends the call as head again), `head_yielded_at`, and
      `YIELD_MARK` for the caller to append the record.

    Instants are ISO strings, `None` clears a key."""
    if after.yielded_at is not None and (
        before.yielded_at is None or after.yielded_at > before.yielded_at
    ):
        return {
            "head_turn_at": _iso(after.turn_at),
            "head_charged_s": _charge(after) or None,
            "head_seen_at": _iso(after.seen_at),
            "head_yielded_at": _iso(after.yielded_at),
            YIELD_MARK: after.yields,
        }
    if after.seen_at is None:
        if before.seen_at is None:
            return {}
        return {"head_charged_s": _charge(after), "head_seen_at": None}
    if before.seen_at is None:
        patch: dict[str, object] = {"head_seen_at": _iso(after.seen_at)}
        if before.turn_at is None:
            patch["head_turn_at"] = _iso(after.turn_at)
        return patch
    if _elapsed(before.seen_at, now) < HEAD_SEEN_EVERY:
        return {}
    return {"head_charged_s": _charge(after), "head_seen_at": _iso(after.seen_at)}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _charge(m: Member) -> float:
    """The charge as the document keeps it: whole seconds."""
    return float(round(m.charged_s))


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


def lane_merge_verdict(raw: Mapping[str, object]) -> Literal["record", "not_lane"] | None:
    """What a member's document says of its fast-lane merge (GH-681, informative; no decision
    reads it): `record` when `auto_merged_at` is later than `lane_admitted.at` (instants, never
    strings) and the merge verb kept the admitted pull request head and master (`same_commit`),
    `not_lane` when it merged after the admission on another sha or master (not provably a lane
    merge), `None` for anything else: no admission, already marked, a merge not later than the
    admission, or an instant that is missing or does not parse. It is not the lane predicate
    (`lane_ready`, `lane_reason`)."""
    from .rules import same_commit  # rules imports this module through `document`

    admitted = raw.get("lane_admitted")
    if not isinstance(admitted, Mapping) or raw.get("lane_recorded") is not None:
        return None
    admitted_at, merged_at = instant(admitted.get("at")), instant(raw.get("auto_merged_at"))
    if admitted_at is None or merged_at is None or merged_at <= admitted_at:
        return None
    same = same_commit(
        _text(raw.get("auto_merge_head")), _text(admitted.get("pr_head"))
    ) and same_commit(_text(raw.get("auto_merge_master")), _text(admitted.get("master")))
    return "record" if same else "not_lane"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
