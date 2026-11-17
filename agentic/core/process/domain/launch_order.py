"""The launch order of a product (GH-836, glossary: Orden de lanzamiento): which ready issue the
poller launches first. It is not Cercanía (how near to merge a started issue is) nor the Orden de
aterrizaje (which delivered pull request lands next, `landing_order`): it ranks issues that have
not started, by how many open issues wait on them, so the launch that unblocks most goes first.

Pure: the edges in, the counts out. The caller decides which issues and which edges count."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def dependents(edges: Mapping[int, Sequence[int]]) -> dict[int, int]:
    """How many distinct issues wait, transitively, on each blocker. `edges` maps an issue to the
    issues that block it. Only an issue some other issue waits on has an entry, and a missing key
    is zero. A walk with a visited set per issue: a cycle ends, a diamond counts its far end once
    and an issue is never its own dependent."""
    counts: dict[int, int] = {}
    for waiting in edges:
        seen: set[int] = set()
        pending = list(edges[waiting])
        while pending:
            blocker = pending.pop()
            if blocker in seen or blocker == waiting:
                continue
            seen.add(blocker)
            pending.extend(edges.get(blocker, ()))
        for blocker in seen:
            counts[blocker] = counts.get(blocker, 0) + 1
    return counts


def launch_key(issue: int, counts: Mapping[int, int]) -> tuple[int, int]:
    """The sort key of the launch order: the issue with most dependents first (`counts` from
    `dependents`, a missing issue has none), the lowest number at a tie."""
    return (-counts.get(issue, 0), issue)
