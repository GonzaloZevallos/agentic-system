"""budget_round (GH-939): the poller's GraphQL budget line, its carry and its failure paths."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.adapters.github.gh_call_log import InProcessGhCallLog
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.budget_round import (
    UNAVAILABLE,
    BudgetCarry,
    budget_round,
)
from agentic.core.process.application.ports import TrackerCallFailed
from agentic.core.process.domain.gh_calls import GhCallKind, GhConsumer
from agentic.core.process.domain.github_budget import GraphqlReading, Unmeasured

NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)
RESET = int(NOW.timestamp()) + 90


def now() -> datetime:
    return NOW


def document(used: int, reset: int = RESET) -> dict[str, object]:
    return {
        "resources": {
            "graphql": {"used": used, "limit": 5000, "remaining": 5000 - used, "reset": reset}
        }
    }


def log_with(**counts: int) -> InProcessGhCallLog:
    log = InProcessGhCallLog()
    kinds: dict[str, GhCallKind] = {
        "issue_list": "issue_list",
        "api": "api",
        "other": "other",
        "pr_view": "pr_view",
    }
    for name, n in counts.items():
        for _ in range(n):
            log.record(kinds[name])
    return log


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[GhConsumer, dict[GhCallKind, int], int | None, object]] = []

    def gh_budget(
        self,
        consumer: GhConsumer,
        calls: Mapping[GhCallKind, int],
        remaining: int | None,
        round_points: int | Unmeasured,
    ) -> None:
        self.events.append((consumer, dict(calls), remaining, round_points))


class Raising:
    def gh_budget(
        self,
        consumer: GhConsumer,
        calls: Mapping[GhCallKind, int],
        remaining: int | None,
        round_points: int | Unmeasured,
    ) -> None:
        raise RuntimeError("exporter down")


def test_first_round_prints_unmeasured_points_with_counts_in_kind_order() -> None:
    log = log_with(api=3, issue_list=1, other=2)
    telemetry = Recorder()
    carry = BudgetCarry()

    line = budget_round(lambda: document(10), log, carry, telemetry, now=now)

    assert line == (
        "agentic: poll: graphql budget: remaining 4990/5000, reset in 90 s, "
        "round points: unmeasured, calls issue_list=1 issue_view=0 pr_list=0 pr_view=0 "
        "api=3 other=2"
    )
    assert carry.previous == GraphqlReading(10, 5000, 4990, datetime.fromtimestamp(RESET, UTC))
    assert telemetry.events == [
        (
            "poller",
            {
                "issue_list": 1,
                "issue_view": 0,
                "pr_list": 0,
                "pr_view": 0,
                "api": 3,
                "other": 2,
            },
            4990,
            Unmeasured.UNMEASURED,
        )
    ]


def test_second_round_prints_the_measured_points() -> None:
    log = InProcessGhCallLog()
    telemetry = Recorder()
    carry = BudgetCarry()
    budget_round(lambda: document(10), log, carry, telemetry, now=now)

    line = budget_round(lambda: document(25), log, carry, telemetry, now=now)

    assert line.startswith(
        "agentic: poll: graphql budget: remaining 4975/5000, reset in 90 s, round 15 points, calls "
    )
    assert telemetry.events[-1][2:] == (4975, 15)


def test_reset_in_never_goes_below_zero_and_rounds_up() -> None:
    log = InProcessGhCallLog()
    late = budget_round(
        lambda: document(1, RESET - 100), log, BudgetCarry(), NoTelemetry(), now=now
    )
    odd = budget_round(
        lambda: document(1),
        log,
        BudgetCarry(),
        NoTelemetry(),
        now=lambda: NOW + timedelta(milliseconds=500),
    )
    assert "reset in 0 s" in late
    assert "reset in 90 s" in odd


@pytest.mark.parametrize("failure", ["none", "unparsed", "tracker"])
def test_failed_read_prints_counts_and_clears_the_carry(failure: str) -> None:
    log = log_with(api=2)
    telemetry = Recorder()
    carry = BudgetCarry()
    budget_round(lambda: document(10), InProcessGhCallLog(), carry, telemetry, now=now)
    assert carry.previous is not None

    def read() -> object | None:
        if failure == "tracker":
            raise TrackerCallFailed("gh api rate_limit failed")
        return None if failure == "none" else {"resources": {}}

    line = budget_round(read, log, carry, telemetry, now=now)

    assert line == (
        "agentic: poll: graphql budget: not read, calls issue_list=0 issue_view=0 "
        "pr_list=0 pr_view=0 api=2 other=0"
    )
    assert carry.previous is None
    assert telemetry.events[-1][2:] == (None, Unmeasured.UNMEASURED)


def test_round_after_a_failed_read_is_unmeasured() -> None:
    log = InProcessGhCallLog()
    carry = BudgetCarry()
    budget_round(lambda: document(10), log, carry, NoTelemetry(), now=now)
    budget_round(lambda: None, log, carry, NoTelemetry(), now=now)

    line = budget_round(lambda: document(30), log, carry, NoTelemetry(), now=now)

    assert "round points: unmeasured" in line


def test_a_telemetry_that_raises_gives_unavailable_and_clears_the_carry() -> None:
    log = log_with(api=1)
    carry = BudgetCarry()
    budget_round(lambda: document(10), InProcessGhCallLog(), carry, NoTelemetry(), now=now)

    line = budget_round(lambda: document(20), log, carry, Raising(), now=now)

    assert line == UNAVAILABLE == "agentic: poll: graphql budget: unavailable"
    assert carry.previous is None
    # The next round still runs, and is unmeasured because the carry was cleared.
    assert "round points: unmeasured" in budget_round(
        lambda: document(30), log, carry, NoTelemetry(), now=now
    )


def test_a_failed_read_with_a_telemetry_that_raises_gives_unavailable() -> None:
    carry = BudgetCarry()
    line = budget_round(lambda: None, InProcessGhCallLog(), carry, Raising(), now=now)
    assert line == UNAVAILABLE
    assert carry.previous is None


def test_the_log_is_drained_before_the_read_even_when_the_read_raises() -> None:
    log = log_with(api=4)

    def read() -> object | None:
        raise RuntimeError("boom")

    line = budget_round(read, log, BudgetCarry(), NoTelemetry(), now=now)

    assert line == UNAVAILABLE
    assert sum(log.drain().values()) == 0


def test_the_log_is_drained_before_the_read_runs() -> None:
    log = log_with(api=4)
    seen: list[int] = []

    def read() -> object | None:
        seen.append(sum(log.drain().values()))
        return document(1)

    budget_round(read, log, BudgetCarry(), NoTelemetry(), now=now)

    assert seen == [0]


def test_a_base_exception_from_the_read_propagates() -> None:
    log = log_with(api=1)
    carry = BudgetCarry()

    def read() -> object | None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        budget_round(read, log, carry, NoTelemetry(), now=now)

    assert sum(log.drain().values()) == 0
