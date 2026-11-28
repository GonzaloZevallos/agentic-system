"""`GhRateLimitWatch` (GH-1157): one latch read exactly once, and a reader that never marks."""

from __future__ import annotations

import threading

from agentic.core.process.adapters.github.rate_limit_watch import GhRateLimitWatch
from agentic.core.process.adapters.github.run import RateLimitLatch
from agentic.core.process.domain.rate_limit_wait import RateLimitReading


class _Reader:
    def __init__(self, reading: RateLimitReading | None) -> None:
        self.reading = reading
        self.reads = 0

    def rate_limit(self) -> RateLimitReading | None:
        self.reads += 1
        return self.reading


def test_take_reads_the_mark_and_clears_it():
    latch = RateLimitLatch()
    watch = GhRateLimitWatch(latch, _Reader(None))
    assert watch.take() is False
    latch.mark()
    latch.mark()
    assert [watch.take(), watch.take()] == [True, False]


def test_reading_is_what_the_reader_says_and_leaves_the_latch_alone():
    reading = RateLimitReading({"core": (0, 5.0)})
    reader = _Reader(reading)
    latch = RateLimitLatch()
    watch = GhRateLimitWatch(latch, reader)
    assert watch.reading() is reading
    assert reader.reads == 1
    assert watch.take() is False
    reader.reading = None
    assert watch.reading() is None


def test_a_mark_set_while_another_thread_takes_is_never_lost():
    """Each round marks once, in one thread, while this one takes at the same instant (a barrier
    lines them up): exactly one of that take and the next sees the mark, never neither."""
    latch = RateLimitLatch()
    watch = GhRateLimitWatch(latch, _Reader(None))
    rounds = 3000
    start = threading.Barrier(2)
    end = threading.Barrier(2)

    def marker() -> None:
        for _ in range(rounds):
            start.wait()
            latch.mark()
            end.wait()

    thread = threading.Thread(target=marker)
    thread.start()
    seen = []
    for _ in range(rounds):
        start.wait()
        during = watch.take()
        end.wait()
        seen.append(int(during) + int(watch.take()))
    thread.join()
    assert seen == [1] * rounds
