"""GH-824: the worktree sweep's pure decisions.

Seam: `agentic.core.process.domain.worktree_sweep` (task_of, due, verdict, checkout_fate,
remember, say, MEMO_TTL_ROUNDS)."""

from __future__ import annotations

from typing import get_args

import pytest

from agentic.core.process.domain import worktree_sweep as sweep
from agentic.core.process.domain.worktree_sweep import (
    MEMO_TTL_ROUNDS,
    SWEEP_READS_MAX,
    Outcome,
    SweepMemory,
    checkout_fate,
    due,
    remember,
    say,
    task_of,
    verdict,
)


def listing(*numbers: int) -> dict[str, list[str]]:
    return {f"GH-{n}": [f"GH-{n}"] for n in numbers}


@pytest.mark.parametrize(
    ("name", "task"),
    [
        ("GH-7", "GH-7"),
        ("GH-7.domain-and-verb", "GH-7"),
        ("GH-7.1", "GH-7"),
        ("merge-GH-7", "GH-7"),
        ("slice-GH-7", "GH-7"),
        ("GH-123", "GH-123"),
        ("integration-ab12cd", None),
        ("perf-GH-7", None),
        ("playbook", None),
        ("GH-", None),
        ("GH-7x", None),
        ("xGH-7", None),
        ("land-GH-7", None),
    ],
)
def test_task_of(name: str, task: str | None) -> None:
    assert task_of(name) == task


def test_due_walks_from_the_cursor_and_wraps_around() -> None:
    names = listing(30, 5, 20, 10)
    first, memory = due(names, SweepMemory(cursor=10))
    assert first == ["GH-20", "GH-30", "GH-5", "GH-10"]
    assert memory.cursor == 10


def test_due_starts_at_the_smallest_and_sorts_by_number_not_by_text() -> None:
    ids, memory = due(listing(100, 9, 20), SweepMemory())
    assert ids == ["GH-9", "GH-20", "GH-100"]
    assert (memory.cursor, memory.round) == (100, 1)


def test_due_caps_the_reads_and_the_next_round_continues_after_the_last() -> None:
    names = listing(*range(1, 26))
    first, memory = due(names, SweepMemory())
    assert first == [f"GH-{n}" for n in range(1, SWEEP_READS_MAX + 1)]
    assert memory.cursor == SWEEP_READS_MAX
    second, memory = due(names, memory)
    assert second[0] == f"GH-{SWEEP_READS_MAX + 1}"
    third, _ = due(names, memory)
    assert third[:5] == [f"GH-{n}" for n in range(21, 26)]
    assert third[5] == "GH-1"


def test_due_without_ids_keeps_the_cursor_and_still_counts_the_round() -> None:
    _, memory = due(listing(4), SweepMemory(cursor=4, round=2))
    again, memory = due({}, memory)
    assert again == []
    assert (memory.cursor, memory.round) == (4, 4)


def test_due_skips_a_memo_within_its_ttl_and_looks_again_after_it() -> None:
    names = listing(1, 2)
    ids, memory = due(names, SweepMemory())
    memory = remember(remember(memory, "GH-1", "open"), "GH-2", "read_failed")
    for _ in range(MEMO_TTL_ROUNDS["read_failed"] - 1):
        ids, memory = due(names, memory)
        assert ids == []
    ids, memory = due(names, memory)
    assert ids == ["GH-2"]
    for _ in range(MEMO_TTL_ROUNDS["open"] - MEMO_TTL_ROUNDS["read_failed"] - 1):
        ids, memory = due(names, memory)
        assert "GH-1" not in ids
    ids, memory = due(names, memory)
    assert "GH-1" in ids


def test_due_never_skips_a_removed_or_dead_lock_taken_task() -> None:
    names = listing(1, 2)
    _, memory = due(names, SweepMemory())
    memory = remember(remember(memory, "GH-1", "removed"), "GH-2", "dead_lock_taken")
    ids, _ = due(names, memory)
    assert ids == ["GH-1", "GH-2"]


def test_due_prunes_memos_and_said_pairs_of_tasks_no_longer_listed() -> None:
    _, memory = due(listing(1, 2), SweepMemory())
    memory = remember(remember(memory, "GH-1", "dirty"), "GH-2", "dirty")
    _, memory = say(memory, "GH-1", "dirty")
    _, memory = say(memory, "GH-2", "dirty")
    _, memory = due(listing(2), memory)
    assert set(memory.memos) == {"GH-2"}
    assert memory.said == {("GH-2", "dirty")}


def test_a_task_gone_between_rounds_does_not_make_the_next_one_skip_a_round() -> None:
    names = listing(*range(1, 16))
    first, memory = due(names, SweepMemory())
    assert first[-1] == "GH-10"
    # GH-10 (the cursor) was removed and GH-11 pruned by the time of the next round.
    names = {task: n for task, n in names.items() if task not in ("GH-10", "GH-11")}
    second, _ = due(names, memory)
    assert second[:3] == ["GH-12", "GH-13", "GH-14"]


def test_ttl_table_has_one_value_per_outcome() -> None:
    assert set(MEMO_TTL_ROUNDS) == set(get_args(Outcome))
    assert MEMO_TTL_ROUNDS == {
        "open": 30,
        "held": 30,
        "dirty": 30,
        "removal_failed": 30,
        "read_failed": 5,
        "removed": 0,
        "dead_lock_taken": 0,
    }
    assert (sweep.SWEEP_READS_MAX, sweep.SWEEP_WALL_S) == (10, 30)
    assert (sweep.OPEN_TTL_ROUNDS, sweep.HELD_TTL_ROUNDS) == (30, 30)


@pytest.mark.parametrize(
    ("state", "removes"),
    [("CLOSED", True), ("OPEN", False), ("closed", False), ("", False), ("MERGED", False)],
)
def test_verdict_removes_only_for_exactly_closed(state: str, removes: bool) -> None:
    assert verdict(state) is removes


@pytest.mark.parametrize(
    ("is_checkout", "committed", "fate"),
    [
        (False, None, "empty"),
        (False, True, "empty"),
        (True, True, "clean"),
        (True, False, "dirty"),
        (True, None, "dirty"),
    ],
)
def test_checkout_fate(is_checkout: bool, committed: bool | None, fate: str) -> None:
    assert checkout_fate(is_checkout, committed) == fate


def test_say_is_true_only_the_first_time_a_task_and_cause_pair_is_said() -> None:
    memory = SweepMemory()
    first, memory = say(memory, "GH-1", "dirty")
    again, memory = say(memory, "GH-1", "dirty")
    other_cause, memory = say(memory, "GH-1", "unreadable")
    other_task, memory = say(memory, "GH-2", "dirty")
    assert (first, again, other_cause, other_task) == (True, False, True, True)


def test_remember_replaces_the_outcome_with_the_current_round_and_leaves_the_input() -> None:
    _, memory = due(listing(1), SweepMemory())
    after = remember(memory, "GH-1", "open")
    assert after.memos == {"GH-1": (1, "open")}
    assert memory.memos == {}
    assert remember(after, "GH-1", "held").memos == {"GH-1": (1, "held")}
