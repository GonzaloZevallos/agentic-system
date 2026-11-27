"""The worktree sweep (GH-824), at `application.worktree_sweep.sweep`: fakes for every decision,
one real temp repo through `GitWorkspace` for what git does with a directory it lost."""

from __future__ import annotations

import errno
import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application.ports import (
    TrackerCallFailed,
)
from agentic.core.process.application.worktree_sweep import SweepCarry, sweep
from agentic.core.process.domain.worktree_sweep import (
    SWEEP_READS_MAX,
    SWEEP_WALL_S,
    SweepMemory,
)
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports
from tests.fakes_worktree_sweep import CLOSED, Clock, Lock, Tracker, world


def test_a_closed_task_loses_all_its_checkouts_and_keeps_its_branches() -> None:
    ports, workspace, _ = world(
        "GH-7", "GH-7.x", "merge-GH-7", "slice-GH-7", "GH-70", "perf-1", "playbook", closed=(7,)
    )
    workspace.existing_branches["agentic/GH-7"] = ("abc", "base")
    lock = Lock()

    lines, memory = sweep(ports, lock, SweepMemory(), Clock())

    assert workspace.cleaned == ["GH-7"]
    assert set(workspace.removed_checkouts) >= {"GH-7", "GH-7.x", "merge-GH-7", "slice-GH-7"}
    assert not {"GH-70", "perf-1", "playbook"} & set(workspace.removed_checkouts)
    assert workspace.existing_branches == {"agentic/GH-7": ("abc", "base")}
    assert lock.events[:2] == [("acquire", "GH-7"), ("release", "GH-7")]
    assert memory.memos["GH-7"][1] == "removed"
    assert [line.split(" in ")[0] for line in lines] == ["agentic: sweep: removed 4 worktrees"]


def test_an_open_task_a_held_lock_and_an_unreadable_state_leave_the_task_untouched() -> None:
    ports, workspace, tracker = world(
        "GH-1", "GH-2", "GH-3", "GH-4", closed=(2, 3), opened=(1,)
    )  # GH-4 was never scripted: `gh` could not read it
    lock = Lock(held={"GH-2"})

    lines, memory = sweep(ports, lock, SweepMemory(), Clock())

    assert {
        name.removeprefix("merge-").removeprefix("slice-") for name in workspace.removed_checkouts
    } == {"GH-3"}
    assert workspace.cleaned == ["GH-3"]
    assert {t: memo[1] for t, memo in memory.memos.items()} == {
        "GH-1": "open",
        "GH-2": "held",
        "GH-3": "removed",
        "GH-4": "read_failed",
    }
    assert ("release", "GH-2") not in lock.events


def test_a_failing_tracker_stops_the_reads_of_the_round() -> None:
    ports, workspace, tracker = world("GH-1", "GH-2", closed=(1, 2))
    tracker.failure = TrackerCallFailed("gh timed out")

    lines, memory = sweep(ports, Lock(), SweepMemory(), Clock())

    assert tracker.reads == [1] and lines == [] and workspace.removed_checkouts == []
    assert memory.memos["GH-1"][1] == "read_failed" and "GH-2" not in memory.memos


@pytest.mark.parametrize("dirty", ["GH-7.x", "merge-GH-7"])
def test_one_dirty_checkout_keeps_the_whole_task_and_says_so_once(dirty: str) -> None:
    ports, workspace, _ = world("GH-7", "GH-7.x", "merge-GH-7", closed=(7,))
    workspace.uncommitted.add(workspace.checkout_path(dirty))
    carry = SweepCarry()

    first, carry.memory = sweep(ports, Lock(), carry.memory, Clock())
    second, carry.memory = sweep(ports, Lock(), carry.memory, Clock())

    assert workspace.removed_checkouts == [] and workspace.cleaned == []
    assert len(first) == 1 and dirty in first[0] and second == []
    assert carry.memory.memos["GH-7"][1] == "dirty"


def test_a_checkout_whose_status_cannot_be_read_keeps_the_task_and_the_next_task_goes() -> None:
    ports, workspace, _ = world("GH-7", "GH-8", closed=(7, 8))
    workspace.unreadable.add(workspace.checkout_path("GH-7"))

    lines, memory = sweep(ports, Lock(), SweepMemory(), Clock())

    assert workspace.cleaned == ["GH-8"]
    assert "cannot read" in lines[0] and memory.memos["GH-7"][1] == "dirty"


def test_a_dead_lock_is_taken_over_said_once_and_released() -> None:
    ports, workspace, _ = world("GH-7", closed=(7,))
    lock = Lock(dead={"GH-7"})

    lines, memory = sweep(ports, lock, SweepMemory(), Clock())

    assert "took over the lock left by pid 4242 on host (2026-11-16T08:00:00Z)" in lines[0]
    assert workspace.cleaned == ["GH-7"] and lock.events[-1] == ("release", "GH-7")
    assert ("GH-7", "dead_lock_taken") in memory.said


def test_a_checkout_without_git_is_discarded_before_release_task_and_the_task_goes() -> None:
    ports, workspace, _ = world("GH-7", "GH-7.1", closed=(7,))
    workspace.not_checkouts.add(workspace.checkout_path("GH-7.1"))
    workspace.journal = []

    sweep(ports, Lock(), SweepMemory(), Clock())

    assert workspace.journal.index("remove:GH-7.1") < workspace.journal.index("cleanup:GH-7")


def test_a_failing_cleanup_is_said_once_across_rounds_and_looked_at_again_later() -> None:
    ports, workspace, _ = world("GH-7", closed=(7,))
    workspace.cleanup_error = "worktree is locked"
    carry = SweepCarry()

    first, carry.memory = sweep(ports, Lock(), carry.memory, Clock())
    for _ in range(30):  # the memo expires; the next failure is the same cause
        again, carry.memory = sweep(ports, Lock(), carry.memory, Clock())

    assert len(first) == 1 and "worktree is locked" in first[0]
    assert again == [] and carry.memory.memos["GH-7"][1] == "removal_failed"


def test_one_task_failing_does_not_stop_the_next() -> None:
    ports, workspace, _ = world("GH-7", "GH-8", closed=(7, 8))
    workspace.cleanup_error = "locked"

    lines, memory = sweep(ports, Lock(), SweepMemory(), Clock())

    assert {t: m[1] for t, m in memory.memos.items()} == {
        "GH-7": "removal_failed",
        "GH-8": "removal_failed",
    }
    assert len(lines) == 2


def test_at_most_ten_issue_states_are_read_and_the_cursor_resumes_after_them() -> None:
    ports, _, tracker = world(*[f"GH-{n}" for n in range(1, 13)], opened=tuple(range(1, 13)))
    carry = SweepCarry()

    _, carry.memory = sweep(ports, Lock(), carry.memory, Clock())
    first = list(tracker.reads)
    _, carry.memory = sweep(ports, Lock(), carry.memory, Clock())

    assert first == list(range(1, SWEEP_READS_MAX + 1))
    assert tracker.reads[SWEEP_READS_MAX:] == [11, 12]


def test_no_task_starts_after_the_wall_budget() -> None:
    ports, workspace, tracker = world("GH-1", "GH-2", "GH-3", closed=(1, 2, 3))
    clock = Clock(step=SWEEP_WALL_S / 2)  # started at 15 s: the first task is within budget

    lines, memory = sweep(ports, Lock(), SweepMemory(), clock)

    assert tracker.reads == [1] and workspace.cleaned == ["GH-1"]
    assert set(memory.memos) == {"GH-1"} and lines[-1].startswith("agentic: sweep: removed")


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def test_real_git_a_directory_git_lost_is_removed_with_its_task_and_the_branches_stay(
    tmp_path: Path,
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    git("init", "-q", "-b", "master", cwd=root)
    (root / "a.txt").write_text("a\n")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "base", cwd=root)
    workspace = GitWorkspace(root, Configuration(product_dir=root).git_identity)
    task = workspace.task_checkout("GH-9")
    sub = workspace.subtask_checkout("GH-9", "1")
    (sub.path / ".git").unlink()  # git lost this directory: no `.git` file left
    tracker = Tracker()
    tracker.states[9] = CLOSED

    lines, _ = sweep(
        memory_ports(workspace=workspace, tracker=tracker), Lock(), SweepMemory(), Clock()
    )

    assert not task.path.exists() and not sub.path.exists()
    listing = git("worktree", "list", "--porcelain", cwd=root)
    assert "GH-9" not in listing
    assert git("branch", "--list", "agentic/GH-9*", cwd=root).split() == [
        task.branch,
        sub.branch,
    ]
    assert workspace.task_checkout("GH-9").path.is_dir()
    assert lines and lines[-1].startswith("agentic: sweep: removed 2 worktrees")


def test_a_busy_task_is_skipped_before_any_read_and_leaves_no_memo() -> None:
    ports, workspace, tracker = world("GH-7", "GH-8", closed=(7, 8))
    lock = Lock()

    lines, memory = sweep(ports, lock, SweepMemory(), Clock(), busy=frozenset({"GH-7"}))

    assert tracker.reads == [8]
    assert "GH-7" not in memory.memos and memory.memos["GH-8"][1] == "removed"
    assert ("acquire", "GH-7") not in lock.events
    assert not {"GH-7"} & set(workspace.removed_checkouts)

    _, again = sweep(ports, lock, memory, Clock())  # the next round, no longer busy
    assert 7 in tracker.reads


def full_disk() -> OSError:
    return OSError(errno.ENOSPC, "No space left on device")


def test_no_space_from_the_lock_stops_the_round_after_one_read_and_one_acquire() -> None:
    ports, workspace, tracker = world("GH-1", "GH-2", "GH-3", closed=(1, 2, 3))
    lock = Lock()
    lock.failure = full_disk()
    carry = SweepCarry()

    lines, carry.memory = sweep(ports, lock, carry.memory, Clock())

    assert tracker.reads == [1] and lock.events == [("acquire", "GH-1")]
    assert len(lines) == 1 and lines[0].startswith("agentic: sweep: GH-1: no space: ")
    assert "No space left on device" in lines[0]
    assert {t: m[1] for t, m in carry.memory.memos.items()} == {"GH-1": "no_space"}
    assert workspace.removed_checkouts == []

    lock.failure = None  # the disk has room again
    lines, carry.memory = sweep(ports, lock, carry.memory, Clock())

    assert tracker.reads == [1, 1, 2, 3]
    assert workspace.cleaned == ["GH-1", "GH-2", "GH-3"]
    assert [line.split(" in ")[0] for line in lines] == ["agentic: sweep: removed 3 worktrees"]


def test_no_space_is_said_once_across_rounds() -> None:
    ports, _, _ = world("GH-1", closed=(1,))
    lock = Lock()
    lock.failure = full_disk()
    carry = SweepCarry()

    first, carry.memory = sweep(ports, lock, carry.memory, Clock())
    second, carry.memory = sweep(ports, lock, carry.memory, Clock())

    assert len(first) == 1 and second == []
    assert carry.memory.memos["GH-1"][1] == "no_space"


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
def test_no_space_from_a_removal_rewinds_the_cursor_to_that_task(code: int) -> None:
    ports, workspace, tracker = world("GH-1", "GH-2", "GH-3", closed=(1, 2, 3))
    workspace.full_for = {"GH-2"}
    workspace.full_errno = code

    lines, memory = sweep(ports, Lock(), SweepMemory(), Clock())

    assert tracker.reads == [1, 2]
    assert {t: m[1] for t, m in memory.memos.items()} == {"GH-1": "removed", "GH-2": "no_space"}
    assert memory.cursor == 1
    assert any(line.startswith("agentic: sweep: GH-2: no space: ") for line in lines)


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EROFS])
def test_any_other_errno_is_still_removal_failed_and_the_round_goes_on(code: int) -> None:
    ports, workspace, tracker = world("GH-1", "GH-2", closed=(1, 2))
    lock = Lock()
    lock.failure = OSError(code, "boom")

    lines, memory = sweep(ports, lock, SweepMemory(), Clock())

    assert tracker.reads == [1, 2]
    assert {t: m[1] for t, m in memory.memos.items()} == {
        "GH-1": "removal_failed",
        "GH-2": "removal_failed",
    }
    assert len(lines) == 2 and all("removal failed: " in line for line in lines)
    assert memory.cursor == 2
