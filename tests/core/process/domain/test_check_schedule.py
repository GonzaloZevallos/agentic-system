"""`check_schedule`: how many cores a gate gets and who goes first, as pure functions over a
snapshot of the ledger (GH-361, ADR-0066). With 16 cores the reserve is 4, a subtask gate is
capped at 6 and integration and land at 12."""

from dataclasses import replace
from pathlib import Path

from agentic.core.process.domain.check_schedule import (
    AGING_S,
    CheckPriority,
    Entry,
    Limits,
    Request,
    alive,
    decide,
    overdue_decision,
)

SUBTASK, INTEGRATION, LAND = CheckPriority.SUBTASK, CheckPriority.INTEGRATION, CheckPriority.LAND
LIMITS = Limits(cores=16)
NOW = 10_000.0


def holder(token, share, priority=SUBTASK, overdue=False, pid=100):
    return Entry("p", pid, "boot", token, priority, NOW - 50, share, NOW - 40, NOW + 9000, overdue)


def waiter(token, priority=SUBTASK, enqueued_at=NOW - 5, pid=100):
    return Entry("p", pid, "boot", token, priority, enqueued_at, 0, None, NOW + 9000, False)


def request(priority=SUBTASK, token="me", enqueued_at=NOW, pid=200):
    return Request(priority, enqueued_at, pid, token)


def test_limits_derive_reserve_and_caps_from_the_cores():
    assert (LIMITS.reserve, LIMITS.cap(SUBTASK), LIMITS.cap(LAND), LIMITS.share_min) == (
        4,
        6,
        12,
        2,
    )
    assert Limits(cores=2).reserve == 1  # at least 1
    assert Limits(cores=1).reserve == 0  # but never the whole machine
    assert Limits(cores=16, share_max=8, share_max_subtask=3).cap(SUBTASK) == 3
    assert Limits(cores=16, share_max=5).cap(SUBTASK) == 5  # never above the general cap


def test_priority_is_a_closed_order():
    assert SUBTASK < INTEGRATION < LAND
    assert {p.value for p in CheckPriority} == {"subtask", "integration", "land"}


def test_sizes_for_each_mix_with_sixteen_cores():
    lone_subtask = decide([], request(SUBTASK), NOW, LIMITS)
    second = decide([holder("a", 6)], request(SUBTASK), NOW, LIMITS)
    third = decide([holder("a", 6), holder("b", 6)], request(SUBTASK), NOW, LIMITS)
    land_next_to_subtask = decide([holder("a", 6)], request(LAND), NOW, LIMITS)
    lone_land = decide([], request(LAND), NOW, LIMITS)
    assert [d.granted for d in (lone_subtask, second, third)] == [True, True, False]
    assert (lone_subtask.share, second.share, third.share) == (6, 6, 0)
    assert (land_next_to_subtask.share, lone_land.share) == (10, 12)


def test_a_subtask_never_gets_the_reserve_with_or_without_a_land():
    full = [holder("a", 6), holder("b", 6)]  # 4 free, all of it the reserve
    assert not decide(full, request(SUBTASK), NOW, LIMITS).granted
    assert not decide([*full, waiter("l", LAND)], request(SUBTASK), NOW, LIMITS).granted
    # 5 free: only 1 is outside the reserve, below the minimum share
    assert not decide([holder("a", 6), holder("b", 5)], request(SUBTASK), NOW, LIMITS).granted
    # 7 free: 3 are outside the reserve
    assert decide([holder("a", 6), holder("b", 3)], request(SUBTASK), NOW, LIMITS).share == 3


def test_a_land_may_use_the_reserve_and_waits_below_the_minimum():
    assert decide([holder("a", 6), holder("b", 6)], request(LAND), NOW, LIMITS).share == 4
    assert not decide(
        [holder("a", 6), holder("b", 6), holder("c", 3)], request(LAND), NOW, LIMITS
    ).granted


def test_booked_shares_never_pass_the_cores_across_a_sequence_of_grants():
    entries: list[Entry] = []
    for index, priority in enumerate([SUBTASK, LAND, SUBTASK, INTEGRATION, SUBTASK, LAND]):
        decision = decide(entries, request(priority, token=f"t{index}"), NOW, LIMITS)
        if decision.granted:
            entries.append(holder(f"t{index}", decision.share, priority))
        assert sum(e.share for e in entries) <= LIMITS.cores


def test_order_is_land_then_integration_then_subtask():
    queue = [waiter("s", SUBTASK, NOW - 100), waiter("i", INTEGRATION, NOW - 50)]
    held = [holder("a", 6), holder("b", 6)]  # 4 free: only closer runs fit
    assert decide([*held, *queue], request(LAND, "l", NOW), NOW, LIMITS).granted
    assert decide([*held, *queue], request(INTEGRATION, "i", NOW - 50), NOW, LIMITS).granted
    assert not decide([*held, *queue], request(SUBTASK, "s", NOW - 100), NOW, LIMITS).granted
    # a subtask behind an integration waiter is not the head, even with cores to spare
    assert not decide(
        [waiter("i", INTEGRATION, NOW - 50)], request(SUBTASK, "s", NOW - 100), NOW, LIMITS
    ).granted


def test_at_equal_rank_the_earlier_enqueued_goes_first_then_the_lower_pid():
    early, late = waiter("early", SUBTASK, NOW - 30), waiter("late", SUBTASK, NOW - 10)
    assert decide([late], request(SUBTASK, "early", NOW - 30), NOW, LIMITS).granted
    assert not decide([early], request(SUBTASK, "late", NOW - 10), NOW, LIMITS).granted
    tie = waiter("x", SUBTASK, NOW - 10, pid=50)
    assert not decide([tie], request(SUBTASK, "y", NOW - 10, pid=60), NOW, LIMITS).granted
    assert decide([tie], request(SUBTASK, "y", NOW - 10, pid=40), NOW, LIMITS).granted


def test_a_request_already_in_the_ledger_is_not_counted_twice():
    mine = replace(waiter("me", SUBTASK, NOW))
    assert decide([mine], request(SUBTASK, "me", NOW), NOW, LIMITS).share == 6


def test_aging_promotes_a_subtask_in_the_queue_but_never_into_the_reserve():
    old = NOW - AGING_S
    integration = waiter("i", INTEGRATION, NOW - 10)
    assert not decide([integration], request(SUBTASK, "s", NOW - AGING_S + 1), NOW, LIMITS).granted
    aged = decide([integration], request(SUBTASK, "s", old), NOW, LIMITS)
    assert aged.granted and aged.share == 6
    full = [holder("a", 6), holder("b", 6)]
    assert not decide(full, request(SUBTASK, "s", old), NOW, LIMITS).granted


def test_overdue_on_an_idle_ledger_gets_a_normal_share_unmarked():
    decision = overdue_decision([], request(SUBTASK), NOW, LIMITS)
    assert (decision.granted, decision.share, decision.overdue) == (True, 6, False)


def test_overdue_ignores_the_queue_order():
    ahead = [waiter("l", LAND, NOW - 100)]
    decision = overdue_decision(ahead, request(SUBTASK), NOW, LIMITS)
    assert (decision.share, decision.overdue) == (6, False)


def test_overdue_on_a_full_ledger_gets_the_minimum_marked_overdue():
    full = [holder("a", 6), holder("b", 6), holder("c", 4, LAND)]
    decision = overdue_decision(full, request(SUBTASK), NOW, LIMITS)
    assert (decision.granted, decision.share, decision.overdue) == (True, 2, True)
    land = overdue_decision(full, request(LAND), NOW, LIMITS)
    assert (land.share, land.overdue) == (2, True)


def test_an_overdue_subtask_never_takes_from_the_reserve_it_leaves_untouched():
    held = [holder("a", 6), holder("b", 6)]
    overdue_subtask = holder("o", 2, SUBTASK, overdue=True)
    # the minimum share a subtask gets late is booked beyond the bound, not out of the reserve
    assert decide([*held, overdue_subtask], request(LAND), NOW, LIMITS).share == 4
    land_overdue = holder("lo", 2, LAND, overdue=True)
    assert decide([*held, land_overdue], request(LAND), NOW, LIMITS).share == 2


def test_alive_needs_a_live_pid_and_an_unexpired_entry():
    entry = holder("a", 6)
    assert alive(entry, NOW, lambda pid, boot: True)
    assert not alive(entry, NOW, lambda pid, boot: False)
    assert not alive(entry, entry.expires_at, lambda pid, boot: True)  # expires AT, not after
    assert alive(entry, entry.expires_at - 1, lambda pid, boot: pid == 100 and boot == "boot")


def test_the_module_reads_neither_the_clock_nor_the_machine():
    """The core count and the time arrive as arguments (ADR-0066): importing `os` or `time`
    would let the rules read them behind the caller's back."""
    import ast

    from agentic.core.process.domain import check_schedule

    tree = ast.parse(Path(check_schedule.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imported & {"os", "time", "datetime"}
