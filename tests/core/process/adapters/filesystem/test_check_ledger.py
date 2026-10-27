"""`FilesystemCheckLedger`: the machine-wide ledger of shares (GH-361, ADR-0070). Everything
runs in a temporary directory with a fake liveness, clock, sleep and reaper: no `/proc`, no
real state directory, no second process."""

import json

import pytest

from agentic.core.process.adapters.filesystem.check_ledger import (
    POLL_INTERVAL_S,
    FilesystemCheckLedger,
)
from agentic.core.process.application.ports import CheckRequest
from agentic.core.process.domain.check_schedule import CheckPriority, Limits
from agentic.core.process.domain.reaping import CHECK_TASK
from tests.fakes import FakeReaper

SUBTASK, INTEGRATION, LAND = CheckPriority.SUBTASK, CheckPriority.INTEGRATION, CheckPriority.LAND
BOOT = "boot-a"
LIMITS = Limits(cores=16)


class World:
    """A clock the sleep moves, a set of live pids and a reaper that remembers the order of
    events, shared by every ledger of a test (the ledgers of different processes)."""

    def __init__(self, tmp_path):
        self.directory = tmp_path / "checks"
        self.now = 1_000_000.0
        self.live: set[int] = set()
        self.events: list[str] = []
        self.slept: list[float] = []
        self.on_sleep = lambda: None
        world = self

        class Reaper(FakeReaper):
            def reap_run(self, task_id, run):
                world.events.append(f"reap {task_id} {run}")
                return super().reap_run(task_id, run)

        self.reaper = Reaper()

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        self.on_sleep()

    def ledger(self, pid=100, product="mine", reaper=None, boot=BOOT) -> FilesystemCheckLedger:
        self.live.add(pid)
        return FilesystemCheckLedger(
            self.directory,
            product,
            reaper or self.reaper,
            LIMITS,
            boot_id=lambda: boot,
            is_alive=lambda p, b: p in self.live and b in (boot, None),
            wall_clock=lambda: self.now,
            monotonic=lambda: self.now,
            sleep=self.sleep,
            pid=lambda: pid,
        )

    def entries(self) -> list[dict]:
        path = self.directory / "ledger.json"
        return json.loads(path.read_text())["entries"] if path.exists() else []


def request(priority=SUBTASK, wait=0.0, expires=7200.0, **kw) -> CheckRequest:
    return CheckRequest(priority, wait, expires, **kw)


def test_a_lone_subtask_gets_six_cores_and_release_frees_them(tmp_path):
    world = World(tmp_path)
    ledger = world.ledger()
    grant = ledger.acquire(request())
    assert (grant.share, grant.overdue, grant.peers, grant.wait_s) == (6, False, 0, 0.0)
    assert [e["share"] for e in world.entries()] == [6]
    ledger.release(grant)
    ledger.release(grant)  # harmless twice
    assert world.entries() == []


def test_two_subtasks_run_and_the_third_waits_until_one_releases(tmp_path):
    world = World(tmp_path)
    first = world.ledger(1).acquire(request())
    second = world.ledger(2).acquire(request())
    assert (first.share, second.share, second.peers) == (6, 6, 1)
    third_ledger = world.ledger(3)
    world.on_sleep = lambda: world.ledger(1).release(first)
    third = third_ledger.acquire(request(wait=60))
    assert (third.share, third.overdue, third.wait_s) == (6, False, POLL_INTERVAL_S)
    assert world.slept == [POLL_INTERVAL_S]


def test_a_waiter_keeps_its_place_visible_to_the_others_while_it_waits(tmp_path):
    world = World(tmp_path)
    world.ledger(1).acquire(request())
    world.ledger(2).acquire(request())
    seen = []
    world.on_sleep = lambda: seen.append(
        [(e["pid"], e["share"], e["priority"]) for e in world.entries()]
    )
    waited = world.ledger(3).acquire(request(wait=2))
    assert seen[0] == [(1, 6, "subtask"), (2, 6, "subtask"), (3, 0, "subtask")]
    assert waited.overdue and waited.share == 2  # the wait ran out: the minimum, overdue
    assert world.entries()[-1]["overdue"] is True


def test_a_land_waiting_goes_before_an_older_subtask_and_takes_the_reserve(tmp_path):
    world = World(tmp_path)
    world.ledger(1).acquire(request())
    world.ledger(2).acquire(request())  # 4 cores free: the reserve
    world.on_sleep = lambda: None
    land = world.ledger(3).acquire(request(LAND, wait=5))
    assert (land.share, land.overdue) == (4, False)
    subtask = world.ledger(4).acquire(request(SUBTASK, wait=3))
    assert subtask.overdue  # the reserve was never its to take


def test_the_gate_keeps_its_first_enqueued_at_and_token_across_calls(tmp_path):
    world = World(tmp_path)
    ledger = world.ledger()
    first = ledger.acquire(request())
    ledger.release(first)
    world.now += 500
    again = ledger.acquire(request(enqueued_at=first.enqueued_at, token=first.token))
    assert (again.enqueued_at, again.token) == (first.enqueued_at, first.token)


def test_overdue_on_an_idle_ledger_is_a_normal_unmarked_share(tmp_path):
    world = World(tmp_path)
    grant = world.ledger().acquire(request(wait=0))
    assert (grant.share, grant.overdue) == (6, False)


def test_the_cores_of_a_dead_holder_come_back_only_after_its_processes_are_reaped(tmp_path):
    world = World(tmp_path)
    world.ledger(1).acquire(request())
    world.ledger(2).acquire(request())
    world.live.discard(1)  # the holder died with its checks still running
    grant = world.ledger(3).acquire(request())
    assert grant.share == 6 and grant.wait_s == 0
    assert world.events == [f"reap {CHECK_TASK} 1"]
    assert sorted(e["pid"] for e in world.entries()) == [2, 3]


def test_a_holder_whose_processes_survive_the_reap_keeps_its_cores(tmp_path):
    world = World(tmp_path)
    world.ledger(1).acquire(request())
    world.ledger(2).acquire(request())
    world.live.discard(1)
    survivor = {"pid": 77, "command": "pytest", "outcome": "survived"}
    stubborn = FakeReaper(reaped_runs=(survivor,))
    grant = world.ledger(3, reaper=stubborn).acquire(request())
    assert grant.overdue and 1 in [e["pid"] for e in world.entries()]


def test_an_entry_with_a_reused_pid_is_dropped_at_its_expiry_without_a_reap(tmp_path):
    world = World(tmp_path)
    world.ledger(1).acquire(request(expires=100))
    world.ledger(2).acquire(request())  # pid 1 stays alive: it was reused by something else
    world.now += 100
    grant = world.ledger(3).acquire(request())
    assert grant.share == 6
    assert sorted(e["pid"] for e in world.entries()) == [2, 3]
    assert world.events == []  # a live pid is never reaped: it may be a live gate of ours


def test_an_entry_of_an_earlier_boot_is_dropped_without_reaping_its_pid(tmp_path):
    world = World(tmp_path)
    world.ledger(1, boot="boot-old").acquire(request())  # recorded before a reboot
    world.live.add(1)  # the same pid now belongs to something else in this boot
    grant = world.ledger(2).acquire(request())
    assert grant.share == 6
    assert world.events == []  # reaping by that pid could kill a live gate's checks
    assert [e["pid"] for e in world.entries()] == [2]


def test_another_products_dead_entry_stays_until_it_expires(tmp_path):
    world = World(tmp_path)
    other = world.ledger(1, product="other")
    other.acquire(request(expires=1000))
    other.acquire(request(expires=1000))  # two holders of 6: nothing outside the reserve
    world.live.discard(1)
    mine = world.ledger(2)
    assert mine.acquire(request(wait=0)).overdue  # still booked
    assert world.events == []  # not ours to reap
    world.now += 1000
    assert mine.acquire(request(wait=0)).share == 6  # expired: gone, no reap
    assert world.events == []


def test_release_in_a_finally_frees_the_cores_when_the_gate_raises(tmp_path):
    world = World(tmp_path)
    ledger = world.ledger()
    with pytest.raises(RuntimeError):
        grant = ledger.acquire(request())
        try:
            raise RuntimeError("the checks blew up")
        finally:
            ledger.release(grant)
    assert world.entries() == []


def test_a_wait_cut_short_by_an_exception_leaves_no_waiter_behind(tmp_path):
    world = World(tmp_path)
    world.ledger(1).acquire(request())
    world.ledger(2).acquire(request())

    def interrupt() -> None:
        raise KeyboardInterrupt

    world.on_sleep = interrupt
    with pytest.raises(KeyboardInterrupt):
        world.ledger(3).acquire(request(wait=60))
    assert sorted(e["pid"] for e in world.entries()) == [1, 2]


def test_a_corrupt_or_foreign_boot_ledger_is_read_as_what_survives(tmp_path):
    world = World(tmp_path)
    world.directory.mkdir(parents=True)
    (world.directory / "ledger.json").write_text("{not json")
    assert world.ledger(2, boot="boot-b").acquire(request()).share == 6  # reads past the junk
    assert world.ledger(3).acquire(request()).share == 6  # the record of pid 2 is of another boot
    assert sorted(e["pid"] for e in world.entries()) == [3]
