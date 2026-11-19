"""The per-issue hold (GH-839): an issue labelled `HOLD_LABEL` is neither launched nor resumed
while the label stays, and removing it releases the issue. Pure: the one place the label name
lives, the size of the page the read asks for, and the verdict over what that read returned."""

from __future__ import annotations

from typing import Literal

HOLD_LABEL = "agentic-hold"
# The page `Tracker.held_issues` asks for. A page of exactly this many rows may be a cut list.
HELD_LIMIT = 500

HoldsUnreadCause = Literal[
    "cut", "timed_out", "unreachable", "transient", "rate_limited", "refused", "failed"
]
"""Why a round could not read the holds, a closed set decided by the type of the exception the
read raised, never by reading `gh`'s text: `cut` the page may hide a held issue, the others the
transport or `gh` itself (`failed`: a `TrackerCallFailed` of no finer type)."""


def held(issue: int, held_issues: frozenset[int]) -> bool:
    """Whether `issue` is among the open issues the round's one read found held."""
    return issue in held_issues
