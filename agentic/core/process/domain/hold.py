"""The per-issue hold (GH-839): an issue labelled `HOLD_LABEL` is neither launched nor resumed
while the label stays, and removing it releases the issue. Pure: the one place the label name
lives, the size of the page the read asks for, and the verdict over what that read returned."""

from __future__ import annotations

HOLD_LABEL = "agentic-hold"
# The page `Tracker.held_issues` asks for. A page of exactly this many rows may be a cut list.
HELD_LIMIT = 500


def held(issue: int, held_issues: frozenset[int]) -> bool:
    """Whether `issue` is among the open issues the round's one read found held."""
    return issue in held_issues
