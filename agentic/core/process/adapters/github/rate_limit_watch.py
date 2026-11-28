"""`GhRateLimitWatch`: the poller's view of the rate limit (GH-1157).

One latch, shared with the tracker, the inbox and the board of the poller's ports, says
whether any of them ended rate-limited; one reader says what `gh api rate_limit` states now."""

from __future__ import annotations

from typing import Protocol

from ...domain.rate_limit_wait import RateLimitReading
from .run import RateLimitLatch


class RateLimitReader(Protocol):
    """The one method the watch needs. Not the `Tracker` port: a tracker wrapped by another
    port would not fit here, and the watch must read through one that never marks the latch."""

    def rate_limit(self) -> RateLimitReading | None: ...


class GhRateLimitWatch:
    def __init__(self, latch: RateLimitLatch, reader: RateLimitReader):
        self._latch = latch
        self._reader = reader

    def take(self) -> bool:
        return self._latch.take()

    def reading(self) -> RateLimitReading | None:
        return self._reader.rate_limit()
