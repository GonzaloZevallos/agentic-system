"""The worktree sweep inside a poll round (GH-824, part 2), at `poll.run_once` over fakes: where
in the round it runs, when it does not, what protects a task from it, and what the held line
does across rounds."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.application import notify, poll
from agentic.core.process.application.ports import IssueState, RunRegistration, SpaceReading
from agentic.core.process.application.scratch_sweep import TMP_ROOT
from agentic.core.process.application.worktree_sweep import SweepCarry
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import PullRequest
from agentic.core.process.domain.worktree_sweep import SweepMemory
from agentic.shared.configuration import Configuration
from tests.fakes import (
    FakeFreeSpace,
    FakeLauncher,
    MemoryRunRegistry,
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    memory_ports,
)

CLOSED = IssueState("CLOSED", "COMPLETED", None, None, ())
OPEN = IssueState("OPEN", "", None, None, ())
HELD_LINE = "agentic: poll: worktrees: not swept while held"
PRODUCT = "mine"


class Journal:
    """The order in which the round touched the launcher, the tracker and the disk listing."""

    def __init__(self) -> None:
        self.events: list[str] = []


class Launcher(FakeLauncher):
    def __init__(self, journal: Journal) -> None:
        super().__init__()
        self.order = journal

    def spawn(self, argv: list[str]) -> int:
        self.order.events.append("launch")
        return super().spawn(argv)


class Workspace(MemoryWorkspace):
    def __init__(self, journal: Journal, names: list[str]) -> None:
        super().__init__()
        self.order = journal
        self.stale_checkouts = list(names)

    def checkout_names(self, prefix: str) -> list[str]:
        if prefix == "":
            self.order.events.append("listed")
        return super().checkout_names(prefix)


class Tracker(MemoryTracker):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[int] = []

    def issue_state(self, number: int) -> IssueState:
        self.reads.append(number)
        return super().issue_state(number)


class Lock:
    def acquire(self, task_id: str, command: str) -> None:
        return None

    def release(self, task_id: str) -> None:
        return None

    def read(self, task_id: str) -> None:
        return None

    def refusal(self, task_id: str) -> None:
        return None


class Round:
    """One `run_once` over fakes: `closed` and `opened` are the issues the tracker answers for,
    `on_disk` the checkouts, `queue` the issues in the tray (each one launches)."""

    def __init__(
        self,
        *,
        on_disk: list[str],
        closed: tuple[int, ...] = (),
        opened: tuple[int, ...] = (),
        queue: tuple[int, ...] = (),
        live: list[RunRegistration] | None = None,
        full: bool = False,
        holds: tuple[str, ...] = (),
    ) -> None:
        self.order = Journal()
        self.workspace = Workspace(self.order, on_disk)
        self.tracker = Tracker()
        for number in closed:
            self.tracker.states[number] = CLOSED
        for number in opened:
            self.tracker.states[number] = OPEN
        self.tracker._queue = list(queue)
        self.registry = MemoryRunRegistry(live)
        self.launcher = Launcher(self.order)
        self.cfg = Configuration(product_dir=Path("/nonexistent-product"))
        self.full = full
        low = SpaceReading(0, 0)
        paths = {
            rules.SCRATCH_VOLUME: self.cfg.scratch_dir,
            rules.PRODUCT_VOLUME: self.cfg.product_dir / ".agentic",
            rules.TMP_VOLUME: TMP_ROOT,
        }
        free_space = (
            FakeFreeSpace(low)
            if full
            else FakeFreeSpace(by_path={paths[name]: low for name in holds})
            if holds
            else None
        )
        self.ports = memory_ports(
            workspace=self.workspace,
            tracker=self.tracker,
            stack=MemoryStack(product=PRODUCT),
            free_space=free_space,
        )
        for number in queue:
            self.tracker.states[number] = OPEN
            self.ports.inbox.issues[str(number)] = {
                "id": f"GH-{number}",
                "title": "t",
                "description": "a plain description",
            }

    def run(
        self,
        carry: SweepCarry | None,
        *,
        dry_run: bool = False,
        notify_carry: notify.NotifyCarry | None = None,
    ) -> list[str]:
        return poll.run_once(
            self.cfg,
            self.ports,
            self.registry,
            self.launcher,
            datetime.now(UTC),
            "agentic",
            register_timeout_s=0.0,
            sleep=lambda s: None,
            lock=Lock(),
            current_boot=None,
            dry_run=dry_run,
            sweep_carry=carry,
            notify_carry=notify_carry,
        )


def test_the_sweep_runs_after_the_launch_of_the_same_round() -> None:
    world = Round(on_disk=["GH-7"], closed=(7,), queue=(3,))

    lines = world.run(SweepCarry())

    assert world.order.events[:2] == ["launch", "listed"]
    assert "GH-7" in world.workspace.removed_checkouts
    assert "agentic: poll: launch #3 -> pid 4242" in lines
    assert any(line.startswith("agentic: sweep: removed 1 worktrees") for line in lines)


def test_a_round_held_without_the_product_volume_does_not_sweep() -> None:
    world = Round(on_disk=["GH-7"], closed=(7,), holds=(rules.TMP_VOLUME,))

    lines = world.run(SweepCarry())

    assert world.tracker.reads == []
    assert world.workspace.removed_checkouts == []
    assert HELD_LINE in lines


@pytest.mark.parametrize(
    "holds",
    [
        (rules.PRODUCT_VOLUME,),
        (rules.SCRATCH_VOLUME, rules.PRODUCT_VOLUME),
        (rules.SCRATCH_VOLUME, rules.PRODUCT_VOLUME, rules.TMP_VOLUME),
    ],
)
def test_a_round_held_with_the_product_volume_sweeps_and_says_no_held_line(
    holds: tuple[str, ...],
) -> None:
    world = Round(on_disk=["GH-7"], closed=(7,), holds=holds, queue=(3,))
    carry = SweepCarry()

    lines = world.run(carry)

    assert "GH-7" in world.workspace.removed_checkouts
    assert any(line.startswith("agentic: sweep: removed 1 worktrees") for line in lines)
    assert HELD_LINE not in lines and not carry.held_said
    assert world.launcher.spawned == []  # the round is still held: nothing starts


def test_a_round_held_by_the_product_volume_leaves_a_task_with_a_live_merge_child() -> None:
    world = Round(on_disk=["GH-7"], closed=(7,), holds=(rules.PRODUCT_VOLUME,))
    world.registry.entries = [RunRegistration(1, "h", PRODUCT, "GH-7", "merge")]
    carry = SweepCarry()

    world.run(carry)

    assert world.tracker.reads == [] and world.workspace.removed_checkouts == []
    assert "GH-7" not in carry.memory.memos


def test_a_dry_round_with_a_carry_reads_no_issue_state_and_keeps_the_memory() -> None:
    world = Round(on_disk=["GH-7"], closed=(7,))
    carry = SweepCarry(SweepMemory(), held_said=False)
    before = carry.memory

    lines = world.run(carry, dry_run=True)

    assert world.tracker.reads == []
    assert carry.memory is before and not carry.held_said
    assert "dry-run: skipped worktrees" in lines
    assert HELD_LINE not in lines


def test_the_held_line_is_said_once_across_held_free_and_held_rounds() -> None:
    carry = SweepCarry()
    tmp = (rules.TMP_VOLUME,)
    first = Round(on_disk=[], holds=tmp).run(carry)
    free = Round(on_disk=[]).run(carry)
    again = Round(on_disk=[], holds=tmp).run(carry)

    assert HELD_LINE in first
    assert HELD_LINE not in free and HELD_LINE not in again
    assert carry.held_said


def test_a_closed_task_acted_on_this_round_is_not_swept() -> None:
    """A merged task with no `closed_at` gets a close this round: no live run is registered
    for it, only the action protects it from the sweep."""
    world = Round(on_disk=["GH-7"], closed=(7,))
    world.ports.inbox.issues["7"] = {"id": "GH-7", "title": "t", "description": "d"}
    reference = "https://github.com/o/r/pull/9"
    world.ports.documents.save(
        {"id": "GH-7", "title": "t", "status": "delivered", "pull_request": reference}
    )
    world.tracker.prs[reference] = PullRequest(
        state="MERGED",
        created_at="2026-11-01T00:00:00+00:00",
        merged_at="2026-11-01T01:00:00+00:00",
        commits=(),
        merge_commit="c0ffee",
    )

    lines = world.run(SweepCarry())

    assert "agentic: poll: close #7 -> pid 4242" in lines
    assert 7 not in world.tracker.reads
    assert world.workspace.removed_checkouts == []


def test_a_live_task_of_another_product_does_not_protect_this_products_closed_task() -> None:
    world = Round(on_disk=["GH-7"], closed=(7,))
    world.registry.entries = [RunRegistration(1, "h", "elsewhere", "GH-7")]

    world.run(SweepCarry())

    assert "GH-7" in world.workspace.removed_checkouts


def test_a_live_task_of_this_product_is_left_for_the_next_round() -> None:
    world = Round(on_disk=["GH-7"], closed=(7,))
    world.registry.entries = [RunRegistration(1, "h", PRODUCT, "GH-7")]
    carry = SweepCarry()

    world.run(carry)

    assert world.tracker.reads == [] and world.workspace.removed_checkouts == []
    assert "GH-7" not in carry.memory.memos


def test_without_a_carry_the_round_says_what_it_said_before_the_change() -> None:
    free = Round(on_disk=["GH-7"], closed=(7,), queue=(3,))
    held = Round(on_disk=["GH-7"], closed=(7,), queue=(3,), full=True)

    free_lines = free.run(None)
    held_lines = held.run(None)

    assert 7 not in free.tracker.reads and free.workspace.removed_checkouts == []
    assert not any("worktrees" in line or "agentic: sweep" in line for line in free_lines)
    assert HELD_LINE not in held_lines
    assert free_lines == ["agentic: poll: launch #3 -> pid 4242"]


def test_a_round_with_the_notifier_off_says_exactly_what_it_said_without_a_notify_carry() -> None:
    # GH-1205: the five notifier members are None, so the carry changes no note and no line.
    for queue in ((), (3,)):
        plain = Round(on_disk=[], queue=queue).run(SweepCarry())
        carried = Round(on_disk=[], queue=queue).run(
            SweepCarry(), notify_carry=notify.NotifyCarry()
        )
        assert carried == plain
    idle = Round(on_disk=[]).run(None, notify_carry=notify.NotifyCarry())
    assert idle == ["agentic: poll: nothing to do this round"]


def test_the_notifier_round_runs_once_per_live_round_held_or_not_and_never_in_a_dry_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, notify.NotifyCarry]] = []

    def spy(ports: object, now: datetime, carry: notify.NotifyCarry) -> list[str]:
        calls.append((ports, carry))
        return ["notify: spied"]

    monkeypatch.setattr(notify, "poll_round", spy)
    carry = notify.NotifyCarry()

    live = Round(on_disk=[])
    lines = live.run(None, notify_carry=carry)
    assert len(calls) == 1 and calls[0] == (live.ports, carry)
    assert lines == ["notify: spied", "agentic: poll: nothing to do this round"]

    held = Round(on_disk=[], holds=(rules.PRODUCT_VOLUME,))
    held.run(None, notify_carry=carry)
    assert len(calls) == 2 and calls[1][0] is held.ports

    Round(on_disk=[]).run(None, dry_run=True, notify_carry=carry)
    Round(on_disk=[]).run(None)  # no carry: the round has nothing to hand it
    assert len(calls) == 2
