"""`FilesystemWriteGate`: the file that spaces the writes of the machine one second apart
(GH-1083). No second process: the clock is a fake the fake sleep moves, and the file is the
test's own `tmp_path`."""

import os
import stat
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem import write_gate
from agentic.core.process.adapters.filesystem.write_gate import (
    WRITE_TURN_FILE,
    FilesystemWriteGate,
)
from agentic.core.process.adapters.github.run import GH_RATE_WAIT_MAX_S
from agentic.core.process.adapters.shell import NullStopFlag
from agentic.core.process.application.ports import StopFlag
from agentic.core.process.domain.github_access import Turn

START = 1_700_000_000.0


class Time:
    def __init__(self, now: float = START) -> None:
        self.now = now
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class StopAfter:
    """A stop flag that is set once the fake clock has slept `slices` times."""

    def __init__(self, time: Time, slices: int) -> None:
        self._time = time
        self._slices = slices

    def is_set(self) -> bool:
        return len(self._time.slept) >= self._slices

    def set(self) -> None:
        raise AssertionError("the gate never sets the flag")


class SetFlag:
    def is_set(self) -> bool:
        return True

    def set(self) -> None:
        raise AssertionError("the gate never sets the flag")


def gate_at(
    path: Path,
    time: Time,
    cap_s: float = 30.0,
    stop: StopFlag | None = None,
) -> FilesystemWriteGate:
    return FilesystemWriteGate(
        path, cap_s, stop or NullStopFlag(), now=time.clock, sleep=time.sleep
    )


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "github" / WRITE_TURN_FILE


def slot_in(path: Path) -> float:
    return float(path.read_text())


def test_the_horizon_is_the_rate_wait_ceiling() -> None:
    assert write_gate._HORIZON_S == GH_RATE_WAIT_MAX_S


def test_the_gate_reports_its_cap(path: Path) -> None:
    assert gate_at(path, Time(), cap_s=12.5).cap_s == 12.5


def test_two_writers_in_a_row_are_at_least_a_second_apart(path: Path) -> None:
    time = Time()
    gate = gate_at(path, time)
    first = gate.take(30.0)
    first_at = time.now
    second = gate.take(30.0)
    assert first.outcome == "granted" and first.waited_s == 0.0
    assert second.outcome == "granted"
    assert second.waited_s == pytest.approx(1.0)
    assert time.now - first_at >= 1.0
    assert slot_in(path) == pytest.approx(START + 1.0)


def test_no_sleep_when_the_last_slot_is_more_than_a_second_old(path: Path) -> None:
    time = Time()
    path.parent.mkdir()
    path.write_text(f"{START - 1.5:24.6f}\n")
    turn = gate_at(path, time).take(30.0)
    assert turn == Turn("granted", 0.0)
    assert time.slept == []
    assert slot_in(path) == pytest.approx(START)


def test_a_refused_writer_does_not_change_the_file(path: Path) -> None:
    time = Time()
    path.parent.mkdir()
    ahead = f"{START + 20.0:24.6f}\n"
    path.write_text(ahead)
    turn = gate_at(path, time, cap_s=5.0).take(30.0)
    assert turn.outcome == "refused" and turn.waited_s == 0.0
    assert path.read_text() == ahead
    assert time.slept == []


def test_the_caller_remaining_time_refuses_even_when_the_cap_would_allow(path: Path) -> None:
    time = Time()
    path.parent.mkdir()
    ahead = f"{START + 10.0:24.6f}\n"
    path.write_text(ahead)
    gate = gate_at(path, time, cap_s=60.0)
    assert gate.take(3.0).outcome == "refused"
    assert gate.take(-5.0).outcome == "refused"
    assert path.read_text() == ahead
    assert time.slept == []


def test_a_stop_during_the_wait_returns_within_a_slice_and_keeps_the_reservation(
    path: Path,
) -> None:
    time = Time()
    path.parent.mkdir()
    path.write_text(f"{START + 4.0:24.6f}\n")  # this writer waits 5 s
    stopped = gate_at(path, time, stop=StopAfter(time, 1)).take(30.0)
    assert stopped.outcome == "stopped"
    assert stopped.waited_s == pytest.approx(1.0)
    assert time.slept == [1.0]
    assert slot_in(path) == pytest.approx(START + 5.0)  # the reservation stays
    reserved_at = START + 5.0
    time.slept.clear()
    nxt = gate_at(path, time).take(30.0)
    assert nxt.outcome == "granted"
    assert time.now >= reserved_at + 1.0
    assert slot_in(path) >= reserved_at + 1.0


def test_a_stop_that_arrives_during_the_final_slice_gives_stopped(path: Path) -> None:
    time = Time()
    path.parent.mkdir()
    path.write_text(f"{START:24.6f}\n")  # a 1 s wait: one slice
    turn = gate_at(path, time, stop=StopAfter(time, 1)).take(30.0)
    assert turn.outcome == "stopped"
    assert turn.waited_s == pytest.approx(1.0)


def test_with_stop_set_and_a_stale_slot_the_turn_is_granted_and_written(path: Path) -> None:
    time = Time()
    path.parent.mkdir()
    path.write_text(f"{START - 10.0:24.6f}\n")
    turn = gate_at(path, time, stop=SetFlag()).take(30.0)
    assert turn == Turn("granted", 0.0)
    assert time.slept == []
    assert slot_in(path) == pytest.approx(START)


def test_with_stop_set_a_wait_is_stopped_before_any_sleep(path: Path) -> None:
    time = Time()
    path.parent.mkdir()
    path.write_text(f"{START:24.6f}\n")
    turn = gate_at(path, time, stop=SetFlag()).take(30.0)
    assert turn == Turn("stopped", 0.0)
    assert time.slept == []


@pytest.mark.parametrize(
    "garbage",
    ["", "not a number", "nan\n", "-3\n", "9" * 80 + "\n", f"{START + 5000:24.6f}\n"],
)
def test_garbage_content_gives_an_immediate_turn_and_a_clean_record(
    path: Path, garbage: str
) -> None:
    time = Time()
    path.parent.mkdir()
    path.write_text(garbage)
    turn = gate_at(path, time).take(30.0)
    assert turn == Turn("granted", 0.0)
    assert time.slept == []
    assert len(path.read_bytes()) == 25
    assert slot_in(path) == pytest.approx(START)


def test_a_valid_record_is_not_truncated_again(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    time = Time()
    gate = gate_at(path, time)
    gate.take(30.0)  # the first turn cuts a new file to size: fine
    assert len(path.read_bytes()) == 25

    def refuse(fd: int, length: int) -> None:
        raise AssertionError("ftruncate on a file that already holds one record")

    monkeypatch.setattr(os, "ftruncate", refuse)
    assert gate.take(30.0).outcome == "granted"
    assert len(path.read_bytes()) == 25


def test_the_modes_are_0700_for_the_directory_and_0600_for_the_file(path: Path) -> None:
    gate_at(path, Time()).take(30.0)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_existing_directory_is_made_private(path: Path) -> None:
    path.parent.mkdir(mode=0o755)
    path.parent.chmod(0o755)
    gate_at(path, Time()).take(30.0)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
