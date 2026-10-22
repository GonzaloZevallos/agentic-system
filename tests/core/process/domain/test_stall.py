"""The stall verdict is a pure function of a snapshot (GH-299, ADR-0056)."""

import pytest

from agentic.core.process.domain.stall import (
    CHECK_EVERY_S,
    STALL_FLOOR_S,
    ChildActivity,
    StallSnapshot,
    judge,
)

TIMEOUT = 2400.0


def snap(**overrides) -> StallSnapshot:
    fields = dict(
        now=10_000.0,
        last_activity=10_000.0 - TIMEOUT - 1,
        pending_tools=0,
        hooks_in_flight=0,
        last_hook_exit=None,
        child=ChildActivity(cpu_ticks=5, running=False),
        previous_child=ChildActivity(cpu_ticks=5, running=False),
        timeout_s=TIMEOUT,
    )
    fields.update(overrides)
    return StallSnapshot(**fields)


def test_the_constants_the_watchdog_and_the_configuration_share():
    assert CHECK_EVERY_S == 30
    assert STALL_FLOOR_S == 600


def test_silence_past_the_threshold_is_a_stall():
    assert judge(snap()).stalled


def test_silence_up_to_the_threshold_is_not():
    assert not judge(snap(last_activity=10_000.0 - TIMEOUT)).stalled


def test_a_hot_child_does_not_sustain_an_attempt_with_no_pending_tool():
    verdict = judge(snap(child=ChildActivity(99, True), previous_child=ChildActivity(1, True)))
    assert verdict.stalled


def test_a_pending_tool_with_a_child_burning_cpu_renews_the_activity():
    verdict = judge(
        snap(pending_tools=1, child=ChildActivity(9, False), previous_child=ChildActivity(5, False))
    )
    assert not verdict.stalled
    assert verdict.last_activity == 10_000.0


def test_a_pending_tool_with_a_child_running_or_in_disk_wait_renews_it():
    assert not judge(snap(pending_tools=1, child=ChildActivity(5, True))).stalled


def test_a_pending_tool_with_a_present_but_idle_child_does_not_sustain_it():
    assert judge(snap(pending_tools=1)).stalled


@pytest.mark.parametrize("missing", ["child", "previous_child"])
def test_a_pending_tool_with_an_unreadable_snapshot_counts_as_activity(missing):
    verdict = judge(snap(pending_tools=1, **{missing: None}))
    assert not verdict.stalled


def test_an_unreadable_child_is_ignored_when_no_tool_is_pending():
    assert judge(snap(child=None)).stalled


def test_a_hook_in_flight_sustains_the_attempt_however_long_it_runs():
    verdict = judge(snap(hooks_in_flight=1))
    assert not verdict.stalled
    assert verdict.last_activity == 10_000.0


def test_a_hook_that_just_exited_restarts_the_whole_threshold():
    exited = 10_000.0 - 5
    verdict = judge(snap(last_hook_exit=exited))
    assert not verdict.stalled
    assert verdict.last_activity == exited
    assert judge(snap(now=exited + TIMEOUT + 1, last_hook_exit=exited)).stalled


def test_an_exit_older_than_the_last_activity_changes_nothing():
    last = 9_990.0
    assert judge(snap(last_activity=last, last_hook_exit=100.0)).last_activity == last
