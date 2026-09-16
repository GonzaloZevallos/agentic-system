"""What a run may do at once and may spend (ADR-0004, fifth addendum).

One object per run, shared by every branch of its fan-out. It is not state: a branch of
`Send` carries a copy of the state and must not see its sisters' (that is what `Send`
is). It is not a port either: there is nothing outside to talk to and nothing to swap.
It is the run itself, as a process.

Per *run*, not per process: `agentic.composition` builds the graph once, at import, and
a `langgraph dev` server runs every task through that one object. A second task there
would inherit the first one's spend and stop on its first role. The graph keeps one of
these per thread id, which is the task id (ADR-0003).

**Concurrency.** With a review inside each branch, a wave of four subtasks can have
four Developers and four reviewers in flight, plus the sub-agents each reviewer
spawns. A 429 from the API arrives as `AttemptFailed`, and two of those escalate the
task: the semaphore is cheaper than the escalation.

**Money.** The task cap is read off `cost_usd`, which a branch only sees as of the
fan-out, so N branches can each spend a full cap's worth before any of them notices
(ADR-0004, third addendum accepted that; with reviews in the branch the number got too
big to accept). The counter here is what every branch does share. It only ever refuses
to *start* an attempt: a role already running is never killed, so nothing paid for is
thrown away.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class RunLimits:
    _semaphore: asyncio.Semaphore | None = field(default=None, repr=False)
    _spent_usd: float = 0.0

    def sized(self, max_concurrent_roles: int) -> RunLimits:
        """The same object, with the semaphore the first caller's configuration sizes.
        One run has one configuration, so the first size is the only size (and the
        semaphore stays on the loop that run lives in)."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max_concurrent_roles)
        return self

    @property
    def spent_usd(self) -> float:
        """What this process paid for, across every branch. A run resumed from a
        checkpoint starts at zero here and the state carries the history, so the caller
        takes whichever is larger."""
        return self._spent_usd

    def record(self, cost_usd: float) -> None:
        """One attempt, paid. Failed attempts count: they cost the same money."""
        self._spent_usd += cost_usd

    def slot(self) -> asyncio.Semaphore | _Unlimited:
        """Held for as long as a role runs. Unsized (a use case called outside a graph,
        a test) means no limit."""
        return self._semaphore or _UNLIMITED


class _Unlimited:
    async def __aenter__(self) -> None: ...

    async def __aexit__(self, *exc: object) -> None: ...


_UNLIMITED = _Unlimited()
