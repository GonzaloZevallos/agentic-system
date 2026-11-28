"""The poller's GraphQL budget step (GH-939): `cli._poll`'s `finally` around the round body,
and `main`'s poll dispatch that wires the real read.

Seams: `cli._poll` with injected read, call log, telemetry, lock, launcher and sleep doubles;
`cli.main` (poll dispatch) with `_poll` replaced by a recorder. Every counting test builds its
own `InProcessGhCallLog`; the dispatch test clears `composition.process_call_log`'s cache
before and after."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentic import cli, composition
from agentic.core.measurement.adapters.otel import telemetry
from agentic.core.process.adapters.github.gh_call_log import InProcessGhCallLog
from agentic.core.process.adapters.telemetry import NoTelemetry
from agentic.core.process.application.ports import TrackerCallFailed
from agentic.core.process.domain.gh_calls import GH_CALL_KINDS
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports

PREFIX = "agentic: poll: graphql budget:"
RESET = 4_000_000_000


def document(used: int, reset: int = RESET) -> dict[str, object]:
    return {
        "resources": {
            "graphql": {"limit": 5000, "used": used, "remaining": 5000 - used, "reset": reset}
        }
    }


class Stop(Exception):
    pass


class LockDouble:
    def acquire(self, task_id: str, command: str) -> None:
        return None

    def release(self, task_id: str) -> None:
        return None

    def read(self, task_id: str) -> None:
        return None

    def refusal(self, task_id: str) -> None:
        return None


class RaisingTelemetry(NoTelemetry):
    def gh_budget(self, consumer, calls, remaining, round_points) -> None:
        raise RuntimeError("the exporter is down")


class RecordingTelemetry(NoTelemetry):
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def gh_budget(self, consumer, calls, remaining, round_points) -> None:
        self.calls.append((consumer, dict(calls), remaining, round_points))


@pytest.fixture(autouse=True)
def quiet_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_once` is not under test here: it says nothing unless a test replaces it."""
    monkeypatch.setattr(cli.pollmod, "run_once", lambda *args, **kwargs: [])


class Loop:
    """`cli._poll` over `rounds` rounds; `sleeps` counts how often the loop reached its sleep."""

    def __init__(self, tmp_path: Path, rounds: int) -> None:
        self.tmp_path = tmp_path
        self.rounds = rounds
        self.sleeps = 0

    async def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        if self.sleeps == self.rounds:
            raise Stop()

    async def run(self, **kwargs: Any) -> None:
        with pytest.raises(Stop):
            await cli._poll(
                Configuration(product_dir=self.tmp_path),
                memory_ports(),
                60.0,
                run_lock=LockDouble(),
                registry=MemoryRunRegistry(),
                launcher=FakeLauncher(),
                argv0="agentic",
                sleep=self.sleep,
                **kwargs,
            )


def budget_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(PREFIX)]


@pytest.mark.anyio
async def test_a_call_recorded_in_the_round_shows_in_the_line(monkeypatch, tmp_path, capsys):
    log = InProcessGhCallLog()

    def round_with_one_call(*args, **kwargs):
        log.record("pr_list")
        return []

    monkeypatch.setattr(cli.pollmod, "run_once", round_with_one_call)

    await Loop(tmp_path, 1).run(
        budget_read=lambda: document(100), gh_calls=log, budget_telemetry=NoTelemetry()
    )

    [line] = budget_lines(capsys.readouterr().out)
    assert re.fullmatch(
        rf"{PREFIX} remaining 4900/5000, reset in \d+ s, round points: unmeasured, "
        r"calls issue_list=0 issue_view=0 pr_list=1 pr_view=0 api=0 other=0",
        line,
    )


@pytest.mark.anyio
async def test_the_carry_lives_across_the_rounds_of_the_loop(tmp_path, capsys):
    readings = iter([document(100), document(130)])
    telemetry = RecordingTelemetry()

    await Loop(tmp_path, 2).run(
        budget_read=lambda: next(readings),
        gh_calls=InProcessGhCallLog(),
        budget_telemetry=telemetry,
    )

    first, second = budget_lines(capsys.readouterr().out)
    assert "round points: unmeasured" in first
    assert "round 30 points" in second
    assert [call[0] for call in telemetry.calls] == ["poller", "poller"]
    assert telemetry.calls[1][2:] == (4870, 30)


@pytest.mark.anyio
async def test_the_step_runs_after_the_end_beat_and_before_the_sleep(tmp_path):
    events: list[str] = []

    class Beats:
        def write(self, key, checkout, at, phase, interval_s) -> None:
            events.append(phase)

    class Ordered(Loop):
        async def sleep(self, seconds: float) -> None:
            events.append("sleep")
            await super().sleep(seconds)

    def read() -> object:
        events.append("read")
        return document(1)

    await Ordered(tmp_path, 1).run(
        heartbeats=Beats(),
        budget_read=read,
        gh_calls=InProcessGhCallLog(),
        budget_telemetry=NoTelemetry(),
    )

    assert events == ["start", "end", "read", "sleep"]


@pytest.mark.anyio
async def test_a_failed_round_still_gets_its_line_and_the_loop_goes_on(
    monkeypatch, tmp_path, capsys
):
    def stalled(*args, **kwargs):
        raise TrackerCallFailed("gh stalled")

    monkeypatch.setattr(cli.pollmod, "run_once", stalled)

    await Loop(tmp_path, 2).run(
        budget_read=lambda: document(5),
        gh_calls=InProcessGhCallLog(),
        budget_telemetry=NoTelemetry(),
    )

    out = capsys.readouterr().out
    assert out.count("agentic: poll: round ended early: gh stalled") == 2
    assert len(budget_lines(out)) == 2


@pytest.mark.anyio
async def test_a_round_that_raises_otherwise_still_gets_its_line_and_the_error_propagates(
    monkeypatch, tmp_path, capsys
):
    def broken(*args, **kwargs):
        raise KeyError("a bug")

    monkeypatch.setattr(cli.pollmod, "run_once", broken)
    loop = Loop(tmp_path, 1)

    with pytest.raises(KeyError):
        await cli._poll(
            Configuration(product_dir=tmp_path),
            memory_ports(),
            60.0,
            run_lock=LockDouble(),
            registry=MemoryRunRegistry(),
            launcher=FakeLauncher(),
            argv0="agentic",
            sleep=loop.sleep,
            budget_read=lambda: document(5),
            gh_calls=InProcessGhCallLog(),
            budget_telemetry=NoTelemetry(),
        )

    assert len(budget_lines(capsys.readouterr().out)) == 1
    assert loop.sleeps == 0


@pytest.mark.anyio
async def test_a_telemetry_that_raises_prints_unavailable_and_the_next_round_runs(tmp_path, capsys):
    loop = Loop(tmp_path, 2)

    await loop.run(
        budget_read=lambda: document(5),
        gh_calls=InProcessGhCallLog(),
        budget_telemetry=RaisingTelemetry(),
    )

    assert budget_lines(capsys.readouterr().out) == [f"{PREFIX} unavailable"] * 2
    assert loop.sleeps == 2


@pytest.mark.anyio
async def test_a_stdout_that_refuses_the_budget_line_still_reaches_the_sleep(monkeypatch, tmp_path):
    class Refusing:
        def __init__(self, real) -> None:
            self.real = real

        def write(self, text: str) -> int:
            if "graphql budget" in text:
                raise OSError("stdout is gone")
            return self.real.write(text)

        def flush(self) -> None:
            self.real.flush()

    monkeypatch.setattr(sys, "stdout", Refusing(sys.stdout))
    loop = Loop(tmp_path, 1)

    await loop.run(
        budget_read=lambda: document(5),
        gh_calls=InProcessGhCallLog(),
        budget_telemetry=NoTelemetry(),
    )

    assert loop.sleeps == 1


@pytest.mark.anyio
async def test_without_a_read_no_budget_line_is_printed_and_the_log_is_not_drained(
    tmp_path, capsys
):
    log = InProcessGhCallLog()
    log.record("api")

    await Loop(tmp_path, 1).run(gh_calls=log, budget_telemetry=NoTelemetry())

    assert budget_lines(capsys.readouterr().out) == []
    assert log.drain()["api"] == 1


@pytest.mark.anyio
async def test_a_read_without_a_call_log_prints_no_budget_line(tmp_path, capsys):
    reads: list[int] = []

    def read() -> object:
        reads.append(1)
        return document(5)

    await Loop(tmp_path, 1).run(budget_read=read, budget_telemetry=NoTelemetry())

    assert budget_lines(capsys.readouterr().out) == []
    assert reads == []


@pytest.fixture
def fresh_process_log() -> Iterator[None]:
    composition.process_call_log.cache_clear()
    yield
    composition.process_call_log.cache_clear()


def test_main_wires_the_real_read_the_process_log_and_the_otel_telemetry(
    monkeypatch, tmp_path, fresh_process_log
):
    (tmp_path / ".agentic").mkdir()
    seen: dict[str, object] = {}

    async def recorder(*args: object, **kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(cli, "_poll", recorder)
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_replace_environ", lambda env: None)
    monkeypatch.setattr("sys.argv", ["agentic", "poll", "--product-dir", str(tmp_path)])

    cli.main()

    read = seen["budget_read"]
    assert callable(read)
    assert seen["gh_calls"] is composition.process_call_log()
    assert seen["budget_telemetry"] is telemetry
    assert set(GH_CALL_KINDS) == set(composition.process_call_log().drain())
