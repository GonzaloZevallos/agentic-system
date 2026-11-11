"""The landing order of a product (GH-588): which delivered pull request is integrated and landed
now. Pure: members in, the head's issue out. The only place pull requests of a product are put in
order; the caller builds the members (a pull request recorded, no `closed_at`, open) and asks
`head`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Turn = Literal["head", "waiting"]
"""Whether a pull request is the head of its product's order (`head`: it is integrated, checked
and landed) or waits for its turn (`waiting`: it is left as it is)."""


@dataclass(frozen=True)
class Member:
    """One delivered pull request in the order. `queued_at` is its parsed `landing_queued_at`
    (`None`: unstamped), `live_pass` that a landing pass is in flight on it, `waits_on_human` that
    the task belongs to a person (`landing.waits_on_human`)."""

    issue: int
    queued_at: datetime | None
    live_pass: bool
    waits_on_human: bool


def _first(members: Iterable[Member]) -> Member | None:
    """The first by `(queued_at, issue)`; an unstamped member goes after every stamped one."""
    ordered = sorted(
        members,
        key=lambda m: (m.queued_at is None, m.queued_at or datetime.min, m.issue),
    )
    return ordered[0] if ordered else None


def head(members: Iterable[Member]) -> int | None:
    """The issue at the head of the order, `None` when nobody is eligible. Precedence: a member
    that waits on a human is never the head, even with a live pass or the oldest stamp; among the
    rest a member with a live pass wins (the earliest by the order below if several); else the
    oldest `queued_at`, ties by issue, unstamped members last (by issue)."""
    eligible = [m for m in members if not m.waits_on_human]
    chosen = _first(m for m in eligible if m.live_pass) or _first(eligible)
    return chosen.issue if chosen is not None else None


def turn_of(issue: int, head_issue: int | None) -> Turn:
    """`head` when `issue` leads the order, else `waiting`."""
    return "head" if head_issue is not None and issue == head_issue else "waiting"
