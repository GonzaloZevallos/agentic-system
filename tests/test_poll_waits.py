"""`scripts/poll_waits.py`: land and exempt wait times over `journalctl -o short-iso` text
held here as fixtures, through `main(argv, stdin)` and its parsing functions."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "poll_waits.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("poll_waits_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load()

UNIT = "agentic-poll-home-zeta-dev-repos-apostrophe.service"


def _poll(clock: str, message: str) -> str:
    return f"2026-10-16T{clock}-03:00 NBK-0274 agentic[3349010]: agentic: poll: {message}"


def _started(clock: str) -> str:
    return (
        f"2026-10-16T{clock}-03:00 NBK-0274 systemd[3308]: Started {UNIT} - "
        "agentic poller for /home/zeta/dev/repos/apostrophe."
    )


def _intervals(lines: list[str], block: str):
    return [i for i in script.measure(lines).intervals if i.block == block]


def test_a_no_slot_wait_is_closed_by_the_land_start():
    lines = [
        _poll("08:00:00", "land #131 waits: no_slot"),
        _poll("08:02:00", "land #131 waits: no_slot"),
        _poll("08:10:00", "land #131 -> pid 41"),
    ]
    (interval,) = _intervals(lines, "land")
    assert (interval.kind, interval.issue, interval.closed) == ("land", 131, True)
    assert interval.minutes == 10.0


def test_a_wait_turning_from_no_slot_to_land_queued_is_one_interval():
    lines = [
        _poll("08:00:00", "land #131 waits: no_slot"),
        _poll("08:04:00", "land #131 waits: land_queued"),
        _poll("08:06:00", "land #131 waits: no_slot"),
        _poll("08:12:00", "land #131 -> pid 41"),
    ]
    (interval,) = _intervals(lines, "land")
    assert interval.minutes == 12.0


def test_an_interval_open_at_the_end_of_input_is_truncated_and_off_the_median():
    lines = [
        _poll("08:00:00", "land #1 waits: no_slot"),
        _poll("08:04:00", "land #1 -> pid 41"),
        _poll("09:00:00", "land #2 waits: no_slot"),
        _poll("09:30:00", "launch #9 -> pid 43"),
    ]
    closed, truncated = _intervals(lines, "land")
    assert closed.closed and closed.minutes == 4.0
    assert not truncated.closed
    assert (truncated.issue, truncated.minutes, truncated.cut_by) == (2, 30.0, "end of input")
    out = script.render("apostrophe", script.measure(lines))
    assert "  median_min: 4.0" in out
    assert "land #2 open 30.0 min (cut by end of input" in out


def test_a_poller_restart_cuts_the_open_wait_and_the_next_one_starts_fresh():
    lines = [
        _poll("08:00:00", "land #5 waits: no_slot"),
        _started("08:20:00"),
        _poll("08:21:00", "land #5 waits: no_slot"),
        _poll("08:25:00", "land #5 -> pid 41"),
    ]
    cut, closed = _intervals(lines, "land")
    assert (cut.closed, cut.cut_by, cut.minutes) == (False, "restart", 20.0)
    assert (closed.closed, closed.minutes) == (True, 4.0)


def test_exempt_busy_on_a_merge_is_timed_in_its_own_block_not_the_land_block():
    lines = [
        _poll("08:00:00", "merge #7 waits: exempt_busy"),
        _poll("08:30:00", "merge #7 -> pid 50"),
    ]
    assert _intervals(lines, "land") == []
    (interval,) = _intervals(lines, "exempt")
    assert (interval.kind, interval.issue, interval.minutes) == ("merge", 7, 30.0)
    out = script.render("apostrophe", script.measure(lines))
    assert "not expected to move (exempt serialisation out of scope)" in out


def test_a_merge_start_does_not_close_the_land_wait_of_the_same_issue():
    lines = [
        _poll("08:00:00", "land #7 waits: no_slot"),
        _poll("08:05:00", "merge #7 -> pid 50"),
        _poll("08:09:00", "land #7 -> pid 51"),
    ]
    (interval,) = _intervals(lines, "land")
    assert interval.minutes == 9.0


def test_two_interleaved_issues_are_paired_each_with_its_own_start():
    lines = [
        _poll("08:00:00", "land #1 waits: no_slot"),
        _poll("08:01:00", "land #2 waits: land_queued"),
        _poll("08:05:00", "land #2 -> pid 42"),
        _poll("08:06:00", "land #1 waits: no_slot"),
        _poll("08:11:00", "land #1 -> pid 41"),
    ]
    first, second = _intervals(lines, "land")
    assert (first.issue, first.minutes) == (1, 11.0)
    assert (second.issue, second.minutes) == (2, 4.0)


def test_pause_waiting_is_counted_and_has_no_duration():
    lines = [
        _poll("08:00:00", "land #1 waits: pause_waiting"),
        _poll("08:01:00", "land #1 waits: pause_waiting"),
        _poll("08:02:00", "land #1 -> pid 41"),
    ]
    result = script.measure(lines)
    assert result.pause_waiting == 2
    assert result.intervals == ()


def test_unrelated_and_malformed_lines_are_ignored():
    lines = [
        "-- Boot 1234 --",
        "",
        "not a timestamp at all",
        "2026-10-16T08:00:00-03:00 NBK-0274 im-config[1]: land #1 waits: no_slot",
        _poll("08:00:00", "launch #3 waits: no_slot"),
        _poll("08:00:00", "reflect 2 task(s) -> pid 9"),
        _poll("08:00:01", "land #1 waits: no_such_reason"),
        "2026-10-16T08:00:02-03:00 NBK-0274 systemd[3308]: Stopped " + UNIT + ".",
    ]
    result = script.measure(lines)
    assert result.intervals == ()
    assert result.pause_waiting == 0


def test_main_prints_one_block_per_measure_for_the_product(
    capsys: pytest.CaptureFixture[str],
):
    lines = [
        _poll("08:00:00", "land #1 waits: no_slot"),
        _poll("08:10:00", "land #1 -> pid 41"),
        _poll("08:11:00", "land #2 waits: exempt_busy"),
        _poll("08:12:00", "land #3 waits: pause_waiting"),
    ]
    code = script.main(["--product", "apostrophe"], io.StringIO("\n".join(lines) + "\n"))
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("product: apostrophe\n")
    assert out.index("land wait") < out.index("not expected to move") < out.index("pause_waiting")
    assert "  intervals: 1\n  median_min: 10.0\n  total_min: 10.0\n  truncated: 0" in out
    assert "land #2 open 1.0 min (cut by end of input" in out
    assert "  count: 1" in out


def test_a_block_with_no_closed_interval_prints_a_dash_for_the_median():
    out = script.render("apostrophe", script.measure([]))
    assert "  median_min: -" in out
