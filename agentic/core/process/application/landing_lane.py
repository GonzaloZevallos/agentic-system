"""The facts the fast lane needs of a landing round (GH-681): transcribed, never decided.

`lane_facts` reads, for the head of the landing order, its pushed paths and the syncs its budget
still allows, and for each candidate whether its branch holds master and what it touches. The
lane predicate and its reasons live in `domain.landing_order` and nowhere else; this module hands
it `Member`s and takes no part in the decision. A read that fails is `None` in that fact, never
`False`, `0` or an empty set: the domain reads `None` as "unknown" and never opens a lane on it.

Local git only, and one `tracker.pull_request` read of the head per round. It imports nothing from
`application.poll` (which calls it), so it takes plain values and domain types."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..domain import landing, landing_order
from . import landing_plan
from .ports import GitError, Ports


@dataclass(frozen=True)
class LaneHead:
    """The head of the order as `lane_facts` reads it: its `issue`, the `reference` of its pull
    request (the URL its document stores), its document `raw` (for the syncs it has spent) and
    whether a live child of an integrating kind runs on it (`landing_order.integrates`, which the
    caller applies)."""

    issue: int
    reference: str
    raw: Mapping[str, object]
    integrating: bool


def _head_oid(ports: Ports, reference: str) -> str | None:
    """The head sha the pull request has now, `None` when it could not be read."""
    try:
        pull_request = ports.tracker.pull_request(reference)
    except (ValueError, OSError):
        return None
    return pull_request.head_oid if pull_request is not None else None


def _touched(ports: Ports, master: str, head: str) -> frozenset[str] | None:
    """What `head` changed since it left `master`, `None` when git could not say."""
    try:
        return frozenset(ports.workspace.touched_paths(master, head))
    except GitError:
        return None


def _syncs_left(raw: Mapping[str, object], head_oid: str) -> int | None:
    """The syncs the head's budget allows still: `LANDING_SYNCS_MAX` less the `sync_master`
    entries of the `landing` list inside the sync epoch, per task and not per sha."""
    try:
        return landing.LANDING_SYNCS_MAX - landing_plan.spent(raw, head_oid, None).syncs
    except (ValueError, TypeError):
        return None


def _head_member(
    ports: Ports, current_master: str, head: LaneHead, member: landing_order.Member
) -> landing_order.Member:
    """`member` (the head) with its own facts: nothing is read for a head that does not
    integrate, and nothing else than the pull request when its head sha is unknown."""
    if not head.integrating:
        return replace(member, integrating=False)
    head_oid = _head_oid(ports, head.reference)
    if head_oid is None:
        return replace(member, integrating=True, paths=None, head_syncs_left=None)
    return replace(
        member,
        integrating=True,
        paths=_touched(ports, current_master, head_oid),
        head_syncs_left=_syncs_left(head.raw, head_oid),
    )


def _holds_master(
    ports: Ports, product_dir: Path, current_master: str, pr_head: str
) -> bool | None:
    try:
        return ports.workspace.is_ancestor(product_dir, current_master, pr_head)
    except GitError:
        return None


def _candidate_member(
    ports: Ports,
    product_dir: Path,
    current_master: str,
    member: landing_order.Member,
    pr_head: str,
) -> landing_order.Member:
    """`member` as a candidate: `merge_now` and clean by the caller's own reading; its branch
    holds master or not, and only when it does are its paths read."""
    holds = _holds_master(ports, product_dir, current_master, pr_head)
    paths = _touched(ports, current_master, pr_head) if holds is True else None
    return replace(member, merge_now=True, clean=True, holds_master=holds, paths=paths)


def lane_facts(
    ports: Ports,
    product_dir: Path,
    current_master: str,
    members: Sequence[landing_order.Member],
    head: LaneHead,
    candidates: Mapping[int, str],
) -> tuple[landing_order.Member, ...]:
    """A copy of `members`, in order, with the lane facts read. `candidates` maps an issue to its
    pull request head sha for each member the caller knows to be decided `merge`, not backed off
    and conflict-free; the others come back unchanged.

    A head that does not integrate is read for nothing, and the candidates still carry `merge_now`
    and `clean` but no master or path fact. No candidate is read when the head's paths are `None`:
    the lane cannot open on them."""
    result: list[landing_order.Member] = []
    read_head: landing_order.Member | None = None
    for member in members:
        if member.issue == head.issue:
            read_head = _head_member(ports, current_master, head, member)
            result.append(read_head)
        else:
            result.append(member)
    readable = read_head is not None and read_head.integrating and read_head.paths is not None
    for index, member in enumerate(result):
        pr_head = candidates.get(member.issue)
        if pr_head is None or member.issue == head.issue:
            continue
        if readable:
            result[index] = _candidate_member(ports, product_dir, current_master, member, pr_head)
        else:
            result[index] = replace(member, merge_now=True, clean=True)
    return tuple(result)
