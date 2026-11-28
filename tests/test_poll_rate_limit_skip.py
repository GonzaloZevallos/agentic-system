"""The poller skips rounds until a GitHub rate limit resets (GH-1214).

Seam: `cli._poll` driven as in `tests/test_poll_budget.py` (fake ports, injected sleep, lock,
launcher, heartbeats), with a fake clock behind `cli.datetime` and a `Gh` double that records
every read the loop or the round could make and fails the test when one is made while a round
must be skipped. `run_once` is a scripted stub, except in the one test that drives the real one
over a tracker whose `queue()` is rate limited.

Clock: every sleep moves it by `INTERVAL`. The first round ends on a limit that resets
`RESET_IN` after it, so the second round falls inside the skip and the third on its first
instant (`reset_offset` is at most 30 s)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest

from agentic import cli
from agentic.core.process.adapters.github.gh_call_log import InProcessGhCallLog
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.ports import TrackerRateLimited
from agentic.core.process.application.preflight import product_name
from agentic.core.process.domain.rate_limit import reset_offset
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryRunRegistry, MemoryTracker, memory_ports

T0 = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
INTERVAL = 60.0
RESET_IN = timedelta(seconds=90)


class Stop(Exception):
    pass


class Clock:
    def __init__(self):
        self.now = T0


class LockDouble:
    def acquire(self, task_id, command):
        return None

    def release(self, task_id):
        return None

    def read(self, task_id):
        return None

    def refusal(self, task_id):
        return None


class Beats:
    def __init__(self, events):
        self.events = events

    def write(self, key, checkout, at, phase, interval_s):
        self.events.append(phase)


class Gh:
    """Everything that would call `gh` in a round. `forbidden` is set while the loop is in a
    round that must be skipped; any call then fails the test."""

    def __init__(self, events):
        self.events = events
        self.forbidden = False

    def call(self, name):
        assert not self.forbidden, f"{name} was called in a skipped round"
        self.events.append(name)


class SpyTracker(MemoryTracker):
    def __init__(self, gh):
        super().__init__()
        self.gh = gh

    def queue(self):
        self.gh.call("queue")
        return super().queue()


class Scenario:
    """One `cli._poll` over `rounds` sleeps. `outcomes[n]` is what the n-th round that really
    ran does (the stub `run_once` calls it). `skipping` holds the sleeps after which the next
    round must make no call."""

    def __init__(self, tmp_path: Path, monkeypatch, rounds: int, real_round: bool = False):
        self.tmp_path = tmp_path
        self.rounds = rounds
        self.sleeps = 0
        self.events: list[str] = []
        self.gh = Gh(self.events)
        self.clock = Clock()
        self.outcomes: list[Any] = []
        self.ran = 0
        self.skipping: set[int] = set()
        self.ports = memory_ports(tracker=SpyTracker(self.gh))
        self.sleep_error: BaseException = Stop()
        self.raised: BaseException | None = None
        clock = self.clock

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now

        monkeypatch.setattr(cli, "datetime", FakeDatetime)
        if not real_round:
            monkeypatch.setattr(cli.pollmod, "run_once", self.run_once)
        original = self.ports.workspace.upstream

        def upstream():
            self.gh.call("upstream")
            return original()

        monkeypatch.setattr(self.ports.workspace, "upstream", upstream)

    def run_once(self, *args, **kwargs):
        self.gh.call("run_once")
        outcome = self.outcomes[self.ran]
        self.ran += 1
        return list(outcome()) if outcome is not None else []

    def read_budget(self):
        self.gh.call("budget_step")
        return None

    async def sleep(self, seconds):
        assert seconds == INTERVAL
        self.events.append("sleep")
        self.sleeps += 1
        self.clock.now += timedelta(seconds=seconds)
        if self.sleeps == self.rounds:
            raise self.sleep_error
        self.gh.forbidden = self.sleeps in self.skipping

    async def run(self, **kwargs):
        with pytest.raises((Stop, RuntimeError, anyio.get_cancelled_exc_class())) as raised:
            await cli._poll(
                Configuration(product_dir=self.tmp_path),
                self.ports,
                INTERVAL,
                run_lock=LockDouble(),
                registry=MemoryRunRegistry(),
                launcher=FakeLauncher(),
                argv0="agentic",
                sleep=self.sleep,
                heartbeats=Beats(self.events),
                budget_read=self.read_budget,
                gh_calls=InProcessGhCallLog(),
                budget_telemetry=NoTelemetry(),
                **kwargs,
            )
        self.raised = raised.value

    def until(self) -> datetime:
        return T0 + RESET_IN + reset_offset(product_name(self.ports))


def limited_at_first_round(reset_in: timedelta | None = RESET_IN):
    def rate_limited():
        resets_at = T0 + reset_in if reset_in is not None else None
        raise TrackerRateLimited("met the GitHub rate limit", resets_at=resets_at)

    return rate_limited


ROUND = ["start", "upstream", "run_once", "end", "budget_step", "sleep"]
SKIPPED = ["start", "end", "sleep"]


@pytest.mark.anyio
async def test_a_skipped_round_beats_start_and_end_prints_its_line_and_runs_no_step(
    tmp_path, monkeypatch, capsys
):
    scenario = Scenario(tmp_path, monkeypatch, 3)
    scenario.outcomes = [limited_at_first_round(), None]
    scenario.skipping = {1}

    await scenario.run()

    assert scenario.events == [*ROUND, *SKIPPED, *ROUND]
    out = capsys.readouterr().out
    line = f"agentic: poll: round skipped: GitHub rate limit until {scenario.until().isoformat()}"
    assert out.count(line) == 1
    assert "round ended early: met the GitHub rate limit" in out


@pytest.mark.anyio
async def test_the_round_after_until_runs_the_round_and_the_step(tmp_path, monkeypatch):
    scenario = Scenario(tmp_path, monkeypatch, 3)
    scenario.outcomes = [limited_at_first_round(), None]
    scenario.skipping = {1}

    await scenario.run()

    assert scenario.ran == 2
    assert scenario.events[-len(ROUND) :] == ROUND
    assert scenario.clock.now >= scenario.until()


@pytest.mark.anyio
async def test_a_skipped_round_never_extends_the_skip(tmp_path, monkeypatch):
    # `until` is T0 + 90 s + at most 30 s and the third round starts at T0 + 120 s: a skipped
    # round that moved `until` would make the third one a skip too, with no `run_once`.
    scenario = Scenario(tmp_path, monkeypatch, 3)
    scenario.outcomes = [limited_at_first_round(), None]
    scenario.skipping = {1}

    await scenario.run()

    assert scenario.ran == 2


@pytest.mark.anyio
async def test_a_skipped_round_still_calls_reexec(tmp_path, monkeypatch):
    scenario = Scenario(tmp_path, monkeypatch, 3)
    scenario.outcomes = [limited_at_first_round(), None]
    scenario.skipping = {1}

    def reexec(*args):
        scenario.events.append("reexec")
        return True

    monkeypatch.setattr(cli, "_reexec", reexec)

    await scenario.run(stage0=object())

    assert scenario.events == [
        "reexec",
        *ROUND,
        "reexec",
        *SKIPPED,
        "reexec",
        *ROUND,
    ]


@pytest.mark.anyio
async def test_a_round_that_raises_a_runtime_error_still_runs_the_step(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("a bug")

    scenario = Scenario(tmp_path, monkeypatch, 2)
    scenario.outcomes = [broken]

    await scenario.run()

    assert isinstance(scenario.raised, RuntimeError)
    assert scenario.events == ["start", "upstream", "run_once", "budget_step"]


@pytest.mark.anyio
async def test_a_limit_without_a_reset_leaves_the_carry_empty(tmp_path, monkeypatch, capsys):
    scenario = Scenario(tmp_path, monkeypatch, 2)
    scenario.outcomes = [limited_at_first_round(None), None]

    await scenario.run()

    assert scenario.events == [*ROUND, *ROUND]
    assert "round skipped" not in capsys.readouterr().out


@pytest.mark.anyio
async def test_a_far_reset_is_skipped_only_up_to_the_cap(tmp_path, monkeypatch, capsys):
    scenario = Scenario(tmp_path, monkeypatch, 2)
    scenario.outcomes = [limited_at_first_round(timedelta(days=3)), None]
    scenario.skipping = {1}

    await scenario.run()

    assert scenario.events == [*ROUND, *SKIPPED]
    assert (T0 + timedelta(hours=12)).isoformat() in capsys.readouterr().out


@pytest.mark.anyio
async def test_the_queue_read_of_the_real_round_sets_the_skip(tmp_path, monkeypatch, capsys):
    scenario = Scenario(tmp_path, monkeypatch, 3, real_round=True)
    scenario.ports.tracker.queue_error = TrackerRateLimited(
        "met the GitHub rate limit", resets_at=T0 + RESET_IN
    )
    scenario.skipping = {1}

    await scenario.run()

    # Round 1 reached the queue (twice: eval set, round) and ended early; round 2 made no
    # call; round 3 reached it again.
    assert scenario.events == [
        "start",
        "upstream",
        "queue",
        "queue",
        "end",
        "budget_step",
        "sleep",
        "start",
        "end",
        "sleep",
        "start",
        "upstream",
        "queue",
        "queue",
        "end",
        "budget_step",
        "sleep",
    ]
    out = capsys.readouterr().out
    assert out.count("round skipped: GitHub rate limit until") == 1


@pytest.mark.anyio
async def test_cancelling_during_a_skipped_rounds_sleep_writes_stop(tmp_path, monkeypatch):
    scenario = Scenario(tmp_path, monkeypatch, 2)
    scenario.outcomes = [limited_at_first_round()]
    scenario.skipping = {1}
    scenario.sleep_error = anyio.get_cancelled_exc_class()()

    await scenario.run()

    assert scenario.events == [*ROUND, *SKIPPED, "stop"]
