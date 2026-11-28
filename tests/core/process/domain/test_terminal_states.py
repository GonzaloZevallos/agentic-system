"""The memo of determinate blocker closures (GH-992): what `record` keeps and when `served` gives
it back. Pure: no IO, no clock."""

from __future__ import annotations

import pytest

from agentic.core.process.application.ports import IssueState
from agentic.core.process.domain.terminal_states import (
    Closure,
    OpenView,
    TerminalMemo,
    record,
    served,
)


def state(kind: str, reason: str, commit: str | None = None) -> IssueState:
    return IssueState(
        state="CLOSED" if kind == "CLOSED" else "OPEN",
        state_reason=reason,
        blocked_by=(),
        closing_merge_commit=commit,
    )


def view(memo: TerminalMemo, *open_numbers: int) -> OpenView:
    return OpenView(memo, frozenset(open_numbers))


def test_a_not_planned_closure_is_recorded_and_served() -> None:
    memo = TerminalMemo()

    record(memo, 7, state("CLOSED", "NOT_PLANNED"))

    assert served(memo, 7, view(memo)) == Closure("NOT_PLANNED", None)


def test_a_completed_closure_with_a_merge_commit_is_recorded_and_served() -> None:
    memo = TerminalMemo()

    record(memo, 7, state("CLOSED", "COMPLETED", "abc123"))

    assert served(memo, 7, view(memo)) == Closure("COMPLETED", "abc123")


@pytest.mark.parametrize(
    "indeterminate",
    [
        state("CLOSED", "COMPLETED", None),
        state("CLOSED", "COMPLETED", ""),
        state("CLOSED", "DUPLICATE", "abc123"),
        state("CLOSED", "", "abc123"),
        state("OPEN", "", None),
        state("OPEN", "COMPLETED", "abc123"),
        state("OPEN", "NOT_PLANNED"),
    ],
)
def test_a_read_that_does_not_decide_the_verdict_is_never_recorded(
    indeterminate: IssueState,
) -> None:
    memo = TerminalMemo()

    record(memo, 7, indeterminate)

    assert served(memo, 7, view(memo)) is None


def test_a_number_never_recorded_is_not_served() -> None:
    memo = TerminalMemo()
    record(memo, 7, state("CLOSED", "NOT_PLANNED"))

    assert served(memo, 8, view(memo)) is None


def test_a_number_in_the_open_listing_is_evicted_and_stays_a_miss() -> None:
    memo = TerminalMemo()
    record(memo, 7, state("CLOSED", "NOT_PLANNED"))

    assert served(memo, 7, view(memo, 7, 9)) is None
    assert served(memo, 7, view(memo)) is None  # evicted, not merely skipped once


def test_an_open_listing_naming_other_numbers_keeps_the_entry() -> None:
    memo = TerminalMemo()
    record(memo, 7, state("CLOSED", "NOT_PLANNED"))

    assert served(memo, 7, view(memo, 9)) == Closure("NOT_PLANNED", None)
    assert served(memo, 7, view(memo)) == Closure("NOT_PLANNED", None)
