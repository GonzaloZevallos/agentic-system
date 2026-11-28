"""The memo of determinate blocker closures (GH-992): a closed blocker whose read already
decides the verdict is kept across poller rounds, so a candidate held behind another blocker
does not pay for it again each round.

Pure: no IO and no clock. Only `record` writes a `TerminalMemo` and only `served` reads it.
What `record` keeps is a closure that cannot change the verdict: `NOT_PLANNED`, or `COMPLETED`
with a closing merge commit. Anything else is read every round as before. A kept closure is
served only behind a round's own complete open listing (`OpenView`): a number in that listing
was reopened, so it is evicted and read live."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ReadState(Protocol):
    """The three facts of an issue read that `record` judges; the application's `IssueState`
    has them. A protocol, so the domain does not import the application."""

    @property
    def state(self) -> str: ...

    @property
    def state_reason(self) -> str: ...

    @property
    def closing_merge_commit(self) -> str | None: ...


@dataclass(frozen=True)
class Closure:
    """What a determinate closed read said: its `stateReason` and, for `COMPLETED`, the
    closing merge commit. A narrow value, never an `IssueState` with blanked fields."""

    state_reason: str
    closing_merge_commit: str | None


@dataclass
class TerminalMemo:
    """The closures kept by issue number, for the life of the poller process. Written only by
    `record`, read only by `served`."""

    _closures: dict[int, Closure] = field(default_factory=dict[int, Closure])


@dataclass(frozen=True)
class OpenView:
    """One round's proof for the memo: the numbers of that round's open listing, read in full
    (not failed, not cut). Without one, nothing is served."""

    memo: TerminalMemo
    open_numbers: frozenset[int]


def _determinate(state: ReadState) -> bool:
    if state.state != "CLOSED":
        return False
    if state.state_reason == "NOT_PLANNED":
        return True
    return state.state_reason == "COMPLETED" and bool(state.closing_merge_commit)


def record(memo: TerminalMemo, number: int, state: ReadState) -> None:
    """Keep `number`'s closure when the read decides the verdict; keep nothing otherwise."""
    if _determinate(state):
        memo._closures[number] = Closure(state.state_reason, state.closing_merge_commit or None)


def served(memo: TerminalMemo, number: int, view: OpenView) -> Closure | None:
    """The kept closure of `number`, unless the round's open listing names it: then the entry
    is evicted and the caller reads live."""
    if number in view.open_numbers:
        memo._closures.pop(number, None)
        return None
    return memo._closures.get(number)
