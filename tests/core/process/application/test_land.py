"""GH-242: `agentic land` on a task whose landing entry was written but whose resume child
never ran (the GH-227 shape: last entry `re_review`, a subtask reopened, status `developing`).
Before, a second `land` answered `skipped/not_live` for good; now the reopened pass is
respawned, twice at most, never within the grace period of a spawn, never for a closed PR."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application import landing, landing_sync
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import landing as landing_rules
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    URL,
    _HeldLocks,
    _ports,
    _pr,
)

T0 = datetime(2026, 10, 14, 12, 30, tzinfo=UTC)
ENTRY = {
    "head": HEAD,
    "step": "re_review",
    "refusal": "head_not_reviewed",
    "at": "2026-10-14T12:23:27+00:00",
    "cost_base": 27.9541,
}


class _Spawner:
    def __init__(self, pid: int | None = 4242) -> None:
        self.pid = pid
        self.spawned: list[int] = []

    def __call__(self, number: int) -> int | None:
        self.spawned.append(number)
        return self.pid


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> str:
        return self.now.isoformat(timespec="seconds")

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _stuck(**more):
    """Task 5 as GH-227 left it: the entry on disk, the unit back at its review, no child."""
    return _ports(
        5,
        status="developing",
        landing=[dict(ENTRY)],
        subtasks={"only": {"id": "only", "status": "delivered", "reviewed_head": HEAD}},
        **more,
    )


def _land(ports, spawner, clock, lines=None):
    return landing.land(
        "GH-5",
        _config(),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        (lines if lines is not None else []).append,
        clock=clock,
        resume=spawner,
    )


def _config():
    return _cfg(Path("/nonexistent"))


def _last(ports) -> dict:
    return ports.documents.load("GH-5")["landing"][-1]


def test_a_reopened_landing_without_a_child_is_respawned_and_counted():
    ports, spawner, clock, lines = _stuck(), _Spawner(), _Clock(), []

    result = _land(ports, spawner, clock, lines)

    assert (result.kind, result.step) == ("dispatched", "re_review")
    assert spawner.spawned == [5]
    assert _last(ports)["resume_spawns"] == 1
    assert _last(ports)["resume_spawned_at"] == clock()
    assert _last(ports)["cost_base"] == 27.9541
    assert "agentic: land: GH-5: reopened pass not running, resume -> pid 4242 (1 of 2)" in lines


def test_a_second_land_after_the_grace_period_spawns_again_and_a_third_is_spent():
    ports, spawner, clock = _stuck(), _Spawner(), _Clock()
    _land(ports, spawner, clock)

    clock.advance(301)
    second = _land(ports, spawner, clock)
    assert (second.kind, spawner.spawned) == ("dispatched", [5, 5])
    assert _last(ports)["resume_spawns"] == 2

    clock.advance(301)
    third = _land(ports, spawner, clock)
    assert (third.kind, third.reason) == ("skipped", "resume_spent")
    assert spawner.spawned == [5, 5]
    assert _last(ports)["resume_spawns"] == 2


def test_within_the_grace_period_of_a_spawn_nothing_is_spawned():
    ports, spawner, clock = _stuck(), _Spawner(), _Clock()
    _land(ports, spawner, clock)

    clock.advance(299)
    _land(ports, spawner, clock)

    assert spawner.spawned == [5]
    assert _last(ports)["resume_spawns"] == 1


def test_a_run_started_since_the_spawn_means_the_child_lives_and_nothing_is_respawned():
    ports, spawner, clock = _stuck(), _Spawner(), _Clock()
    _land(ports, spawner, clock)
    clock.advance(400)
    document = ports.documents.load("GH-5")
    started = (T0 + timedelta(seconds=60)).isoformat()
    ports.documents.save({**document, "runs": [{"role": "Tech Lead", "started_at": started}]})

    _land(ports, spawner, clock)

    assert spawner.spawned == [5]


def test_a_closed_pull_request_is_abandoned_and_spawns_nothing():
    ports, spawner = _stuck(), _Spawner()
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")

    result = _land(ports, spawner, _Clock())

    assert (result.kind, result.reason) == ("skipped", "pr_abandoned")
    assert spawner.spawned == []
    assert "resume_spawns" not in _last(ports)


def test_a_resume_that_answers_no_pid_writes_nothing_and_a_later_land_can_spawn():
    ports, clock = _stuck(), _Clock()
    silent = _Spawner(pid=None)
    _land(ports, silent, clock)

    assert silent.spawned == [5]
    assert "resume_spawns" not in _last(ports)
    assert "resume_spawned_at" not in _last(ports)

    working = _Spawner()
    _land(ports, working, clock)

    assert working.spawned == [5]
    assert _last(ports)["resume_spawns"] == 1


def test_an_unreadable_pull_request_stops_the_recovery_with_the_tracker_error():
    ports, spawner = _stuck(), _Spawner()

    def broken(reference):
        pull_request_number(reference)
        raise OSError("gh is down")

    ports.tracker.pull_request = broken

    result = _land(ports, spawner, _Clock())

    assert (result.kind, result.reason) == ("stopped", "tracker_error: gh is down")
    assert spawner.spawned == []


def test_an_escalated_task_is_not_respawned():
    ports, spawner = _stuck(escalated="needs a human"), _Spawner()

    _land(ports, spawner, _Clock())

    assert spawner.spawned == []


def _unstarted(**more):
    """The pass recorded and nothing reopened (GH-450): every unit approved on an older head, the
    last `re_review` entry open and without `cost_base`."""
    entry = {k: v for k, v in ENTRY.items() if k != "cost_base"}
    return _ports(
        5,
        landing=[entry],
        cost_usd=30.0,
        subtasks={"only": {"id": "only", "status": "approved", "reviewed_head": "e" * 40}},
        **more,
    )


def test_an_unstarted_pass_is_run_by_land_on_its_own_entry(monkeypatch):
    ports, clock, lines, seen = _unstarted(), _Clock(), [], []

    def executor(context):
        # What `landing_sync._reopened` does with the entry the document already holds.
        state = ports.documents.load("GH-5")
        seen.append(len(state["landing"]))
        marked = landing_rules.with_cost_base(
            state["landing"], context.head, context.step.kind, state["cost_usd"]
        )
        ports.documents.save({**state, "landing": marked})

    monkeypatch.setattr(landing_sync, "re_review", executor)
    before = ports.documents.load("GH-5")["landing"]
    assert len(before) == 1 and "cost_base" not in before[0]

    result = landing.land(
        "GH-5",
        _config(),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset({"re_review"}),
        lines.append,
        clock=clock,
    )

    after = ports.documents.load("GH-5")["landing"]
    assert (result.kind, result.step) == ("dispatched", "re_review"), (result, lines)
    assert seen == [1]  # the executor met the entry, no second one was appended before it
    assert len(after) == 1  # the pass is counted once
    assert after[0]["head"] == HEAD and after[0]["step"] == "re_review"
    assert after[0]["cost_base"] == 30.0  # stamped by the executor, as ever
    assert not any("already taken" in line for line in lines)


def test_a_stamped_pass_is_resumed_not_run_again(monkeypatch):
    """The input the reuse must reject: with `cost_base` the entry is an `unfinished` pass, so
    `land` respawns its resume child and never hands the entry to the executor again."""
    ports, lines, ran, spawner = _stuck(), [], [], _Spawner()
    state = ports.documents.load("GH-5")
    ports.documents.save(
        {
            **state,
            "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": "e" * 40}},
        }
    )
    monkeypatch.setattr(landing_sync, "re_review", ran.append)
    result = landing.land(
        "GH-5",
        _config(),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset({"re_review"}),
        lines.append,
        clock=_Clock(),
        resume=spawner,
    )
    assert ran == [] and spawner.spawned == [5]
    assert len(ports.documents.load("GH-5")["landing"]) == 1
    assert result.kind == "dispatched"
