"""`InProcessGhCallLog` (GH-990): record, drain, and no count lost under threads."""

import threading

import pytest

from agentic.core.process.adapters.github.gh_call_log import InProcessGhCallLog
from agentic.core.process.domain.gh_calls import GH_CALL_KINDS


def test_drain_returns_every_kind_zeros_included():
    log = InProcessGhCallLog()
    log.record("api")
    drained = log.drain()
    assert set(drained) == set(GH_CALL_KINDS)
    assert drained["api"] == 1
    assert [drained[kind] for kind in GH_CALL_KINDS if kind != "api"] == [0] * 5


def test_an_untouched_log_drains_all_zeros():
    assert dict(InProcessGhCallLog().drain()) == dict.fromkeys(GH_CALL_KINDS, 0)


def test_drain_resets():
    log = InProcessGhCallLog()
    log.record("pr_view")
    log.record("pr_view")
    assert log.drain()["pr_view"] == 2
    log.record("issue_list")
    second = log.drain()
    assert second["pr_view"] == 0
    assert second["issue_list"] == 1


def test_the_result_is_read_only():
    drained = InProcessGhCallLog().drain()
    with pytest.raises(TypeError):
        drained["api"] = 5


def test_a_drained_result_is_not_changed_by_later_records():
    log = InProcessGhCallLog()
    drained = log.drain()
    log.record("api")
    assert drained["api"] == 0


def test_no_count_is_lost_under_threads():
    log = InProcessGhCallLog()
    threads, per_thread = 8, 2000
    done = threading.Event()
    seen = 0

    def drainer():
        nonlocal seen
        while not done.is_set():
            seen += sum(log.drain().values())

    def recorder():
        for _ in range(per_thread):
            log.record("other")

    drain_thread = threading.Thread(target=drainer)
    workers = [threading.Thread(target=recorder) for _ in range(threads)]
    drain_thread.start()
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    done.set()
    drain_thread.join()
    assert seen + sum(log.drain().values()) == threads * per_thread
