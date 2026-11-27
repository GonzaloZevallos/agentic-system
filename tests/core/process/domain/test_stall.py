"""The stall verdict is a pure function of a snapshot (GH-299, ADR-0057)."""

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
        hook_expires=None,
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


# The ceiling of an in-flight hook (GH-978): with the ADR-0057 threshold at 1800 s and the
# watchdog looking every CHECK_EVERY_S, the last renewed tick sits at or before the expiry.
THRESHOLD = 1800.0
EXPIRES = 9_000.0
LAST_TICK = EXPIRES - CHECK_EVERY_S / 2  # a renewal that landed shortly before the expiry
IDLE = dict(
    child=ChildActivity(cpu_ticks=5, running=False),
    previous_child=ChildActivity(cpu_ticks=5, running=False),
)


def hook_snap(**overrides) -> StallSnapshot:
    fields = dict(
        pending_tools=0,
        hooks_in_flight=1,
        hook_expires=EXPIRES,
        timeout_s=THRESHOLD,
        **IDLE,
    )
    fields.update(overrides)
    return snap(**fields)


def test_a_hook_inside_its_ceiling_sustains_the_attempt_however_long_the_silence():
    verdict = judge(hook_snap(now=EXPIRES - 100, last_activity=EXPIRES - 100 - THRESHOLD - 500))
    assert not verdict.stalled
    assert verdict.last_activity == EXPIRES - 100


def test_a_hook_exactly_at_its_ceiling_still_renews():
    verdict = judge(hook_snap(now=EXPIRES, last_activity=EXPIRES - THRESHOLD - 500))
    assert verdict.last_activity == EXPIRES


def test_past_the_ceiling_the_hook_stops_renewing_and_the_threshold_runs_from_the_last_tick():
    verdict = judge(hook_snap(now=LAST_TICK + THRESHOLD, last_activity=LAST_TICK))
    assert not verdict.stalled
    assert verdict.last_activity == LAST_TICK


def test_past_the_ceiling_plus_the_threshold_it_is_a_stall():
    verdict = judge(hook_snap(now=LAST_TICK + THRESHOLD + 1, last_activity=LAST_TICK))
    assert verdict.stalled
    assert verdict.last_activity == LAST_TICK


def test_a_hook_without_a_ceiling_sustains_the_attempt_as_before():
    verdict = judge(hook_snap(hook_expires=None, now=EXPIRES * 10, last_activity=0.0))
    assert not verdict.stalled
    assert verdict.last_activity == EXPIRES * 10


def test_a_2900_s_gate_inside_its_ceiling_is_not_stalled():
    entered = 5_000.0
    verdict = judge(
        hook_snap(now=entered + 2900.0, last_activity=entered, hook_expires=entered + 3300.0)
    )
    assert not verdict.stalled


def test_past_the_ceiling_plus_the_threshold_a_pending_tool_with_a_working_child_keeps_it_alive():
    verdict = judge(
        hook_snap(
            now=EXPIRES + THRESHOLD + 600,
            last_activity=LAST_TICK,
            pending_tools=1,
            child=ChildActivity(cpu_ticks=9, running=False),
            previous_child=ChildActivity(cpu_ticks=5, running=False),
        )
    )
    assert not verdict.stalled


def test_past_the_ceiling_a_pending_tool_with_an_idle_readable_child_stalls():
    verdict = judge(
        hook_snap(now=LAST_TICK + THRESHOLD + 1, last_activity=LAST_TICK, pending_tools=1)
    )
    assert verdict.stalled


def test_an_exit_after_the_ceiling_restarts_the_whole_threshold():
    exited = LAST_TICK + 600
    verdict = judge(
        hook_snap(
            now=exited + THRESHOLD,
            last_activity=LAST_TICK,
            hooks_in_flight=0,
            hook_expires=None,
            last_hook_exit=exited,
        )
    )
    assert not verdict.stalled
    assert verdict.last_activity == exited
