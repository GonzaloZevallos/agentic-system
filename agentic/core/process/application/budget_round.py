"""The poller's GraphQL budget line (GH-939): one reading of `gh api rate_limit` per round.

The `round C points` figure is the movement of the account's GraphQL `used` since the
previous read, across every process on the machine (the sleep and the poller's children
included); the `calls k=n` counts are only this process's own `gh` launches. They are
different scopes on one line and are not meant to match.

This module is the only drainer of the poller's `GhCallLog`: the log is never handed to
anything else that drains it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ..domain.gh_calls import GH_CALL_KINDS, GhCallKind, GhConsumer
from ..domain.github_budget import GraphqlReading, Unmeasured, graphql_reading, round_points
from .ports import GhCallLog, TrackerCallFailed

_PREFIX = "agentic: poll: graphql budget:"
UNAVAILABLE = f"{_PREFIX} unavailable"


class BudgetSink(Protocol):
    """What the round needs from telemetry; `Telemetry` satisfies it structurally (the
    application layer does not import the adapters that define it)."""

    def gh_budget(
        self,
        consumer: GhConsumer,
        calls: Mapping[GhCallKind, int],
        remaining: int | None,
        round_points: int | Unmeasured,
    ) -> None: ...


@dataclass
class BudgetCarry:
    """The previous reading, kept across the rounds of one loop."""

    previous: GraphqlReading | None = None


def _counts(calls: Mapping[GhCallKind, int]) -> str:
    return " ".join(f"{kind}={calls.get(kind, 0)}" for kind in GH_CALL_KINDS)


def _now() -> datetime:
    return datetime.now(UTC)


def budget_round(
    read: Callable[[], object | None],
    log: GhCallLog,
    carry: BudgetCarry,
    telemetry: BudgetSink,
    *,
    now: Callable[[], datetime] = _now,
) -> str:
    """Drain `log`, read the budget, and return the line for the poller log.

    The drain comes first and always happens. A read that returns `None`, does not parse or
    raises `TrackerCallFailed` still prints the counts and clears the carry. Any other
    `Exception` after the drain, a telemetry that raises included, gives the `unavailable`
    line and clears the carry; `BaseException` (cancellation) passes through."""
    calls = log.drain()
    try:
        try:
            current = graphql_reading(read())
        except TrackerCallFailed:
            current = None
        if current is None:
            carry.previous = None
            telemetry.gh_budget("poller", calls, None, Unmeasured.UNMEASURED)
            return f"{_PREFIX} not read, calls {_counts(calls)}"
        points = round_points(carry.previous, current)
        carry.previous = current
        telemetry.gh_budget("poller", calls, current.remaining, points)
        seconds = max(0, math.ceil((current.reset - now()).total_seconds()))
        round_part = (
            "round points: unmeasured"
            if points is Unmeasured.UNMEASURED
            else f"round {points} points"
        )
        return (
            f"{_PREFIX} remaining {current.remaining}/{current.limit}, "
            f"reset in {seconds} s, {round_part}, calls {_counts(calls)}"
        )
    except Exception:
        carry.previous = None
        return UNAVAILABLE
