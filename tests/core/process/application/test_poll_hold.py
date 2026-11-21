"""GH-839: a poller round and the per-issue hold, against the fake Tracker: a held issue waits
with one `waiting (held)` line, a failed hold read starts nothing, and nothing live is touched."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic.core.process.application import poll
from agentic.core.process.application.ports import (
    HeldListMayBeCut,
    IssueComment,
    IssueState,
    TrackerRefused,
    TrackerTimedOut,
)
from agentic.core.process.domain import hold, rules
from agentic.core.process.domain.scratch import SpaceReading
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_poll import (
    _dead_lock,
    _Locks,
    _no_sleep,
    _open_task,
    _paused,
    _seed_body,
)
from tests.core.process.application.test_poll_orphaned import _ports as orphan_ports
from tests.core.process.application.test_poll_stack_hold import CHILD, _notes, _world
from tests.core.process.application.test_poll_stack_hold import _go as _stack_go
from tests.fakes import (
    FakeFreeSpace,
    FakeLauncher,
    MemoryRunRegistry,
    RunRegistration,
    memory_ports,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _go(ports, *, lock=None, registry=None, now=NOW):
    launcher = FakeLauncher()
    lines = poll.run_once(
        Configuration(product_dir="/p"),
        ports,
        registry or MemoryRunRegistry(),
        launcher,
        now,
        "agentic",
        sleep=_no_sleep,
        lock=lock or _Locks(),
        current_boot="boot-now",
    )
    return lines, launcher


def _queue(ports, *numbers: int) -> None:
    ports.tracker._queue = [*ports.tracker._queue, *numbers]
    for n in numbers:
        ports.tracker.states[n] = IssueState("OPEN", "", (), None)
        _seed_body(ports, n)


def _about(lines: list[str], number: int) -> list[str]:
    return [line for line in lines if f"#{number} " in line or line.endswith(f"#{number}")]


def test_a_held_issue_is_not_launched_and_says_so_and_a_released_one_is():
    ports = memory_ports()
    _queue(ports, 5)
    ports.tracker.held = {5}
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    assert lines == ["agentic: poll: launch #5 waiting (held)"]
    ports.tracker.held = set()
    lines, launcher = _go(ports)
    assert lines == ["agentic: poll: launch #5 -> pid 4242"]
    assert len(launcher.spawned) == 1


def test_a_held_issue_still_blocks_its_dependent():
    ports = memory_ports()
    _queue(ports, 5)
    ports.tracker._queue.append(6)
    ports.tracker.states[6] = IssueState("OPEN", "", (5,), None)
    ports.tracker.held = {5}
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    assert lines == ["agentic: poll: launch #5 waiting (held)"]
    assert _about(lines, 6) == []


def test_a_held_retake_says_held_once_and_spawns_nothing():
    ports = memory_ports()
    _open_task(ports, "GH-30")
    ports.tracker.held = {30}
    lines, launcher = _go(ports, lock=_Locks(_dead_lock("GH-30")))
    assert launcher.spawned == []
    assert _about(lines, 30) == ["agentic: poll: retake #30 waiting (held)"]
    assert ports.tracker.comments == []


def test_a_held_orphan_says_held_once_with_no_no_slot_or_unaccounted_line():
    ports = orphan_ports()
    ports.tracker.held = {7}
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    assert _about(lines, 7) == ["agentic: poll: orphan #7 waiting (held)"]
    assert ports.tracker.comments == []


def _account_limit(ports, number: int) -> None:
    ports.documents.save(
        _paused(
            f"GH-{number}",
            "Developer failed 2 times: API Error: 429",
            [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        )
    )
    ports.tracker.timestamped[f"GH-{number}"] = [
        IssueComment(
            body="Escalated: Developer failed 2 times: API Error: 429",
            created_at="2026-10-05T10:00:00Z",
        )
    ]


def test_a_held_resume_says_held_once_and_a_released_one_resumes():
    ports = memory_ports()
    _account_limit(ports, 19)
    ports.tracker.held = {19}
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    assert _about(lines, 19) == ["agentic: poll: resume #19 waiting (held)"]
    ports.tracker.held = set()
    lines, launcher = _go(ports)
    assert lines == ["agentic: poll: resume #19 -> pid 4242"]


def test_a_held_issue_with_a_live_child_gets_no_line_no_action_and_keeps_its_registry_entry():
    ports = memory_ports()
    _open_task(ports, "GH-30")
    ports.tracker.held = {30}
    live = RunRegistration(pid=1, host="h", product="memory", task_id="GH-30")
    registry = MemoryRunRegistry([live])
    lines, launcher = _go(
        ports, lock=_Locks(_dead_lock("GH-30"), alive={"GH-30"}), registry=registry
    )
    assert launcher.spawned == []
    assert not any("held" in line for line in lines)
    assert registry.live() == [live]


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (TrackerTimedOut("gh timed out"), "timed_out"),
        (TrackerRefused("gh refused"), "refused"),
        (HeldListMayBeCut("a page of 500"), "cut"),
    ],
)
def test_a_failed_hold_read_starts_nothing_and_says_the_closed_cause(error, cause):
    ports = memory_ports()
    _queue(ports, 5)
    _open_task(ports, "GH-30")
    _account_limit(ports, 19)
    ports.tracker.held_issues_error = error
    lines, launcher = _go(ports, lock=_Locks(_dead_lock("GH-30")))
    assert launcher.spawned == []
    assert f"agentic: poll: holds unread ({cause})" in lines
    assert not any("waiting (held)" in line or "-> pid" in line for line in lines)
    assert ports.tracker.comments == []


def test_a_five_hundred_row_read_is_a_cut_list_and_starts_nothing():
    ports = memory_ports()
    _queue(ports, 5)
    ports.tracker.held_issues_error = HeldListMayBeCut(f"{hold.HELD_LIMIT} rows")
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    assert lines == ["agentic: poll: holds unread (cut)"]


def test_an_unread_hold_publishes_no_waiting_land():
    ports = memory_ports()
    ports.tracker.held_issues_error = TrackerTimedOut("gh timed out")
    registry = MemoryRunRegistry()
    _go(ports, registry=registry)
    assert all(not issues for _, _, issues, _ in registry.land_wait_publishes)


def test_any_other_exception_from_the_hold_read_propagates():
    ports = memory_ports()
    ports.tracker.held_issues_error = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        _go(ports)


def test_a_round_held_by_volumes_does_not_read_the_holds():
    full = FakeFreeSpace(SpaceReading(free_bytes=0, free_inodes=0))
    ports = memory_ports(free_space=full)
    _queue(ports, 5)
    ports.tracker.held_issues_error = AssertionError("the holds must not be read")
    lines, launcher = _go(ports)
    assert launcher.spawned == []
    assert "agentic: poll: launch #5 waiting (space_held)" in lines
    assert not any("(held)" in line or "holds unread" in line for line in lines)


def test_held_line_is_the_one_line_of_a_held_deferral():
    assert (
        poll.held_line(rules.Deferred("orphan", 7, "held"))
        == "agentic: poll: orphan #7 waiting (held)"
    )


def test_the_cause_of_a_failed_read_comes_from_the_type_not_the_text():
    assert poll.holds_unread_cause(TrackerTimedOut("rate limit refused cut")) == "timed_out"
    assert poll.holds_unread_cause(HeldListMayBeCut("timed out")) == "cut"


def test_a_stacked_held_child_still_gets_its_stack_pause_note_when_the_hold_read_fails(tmp_path):
    ports = _world(CHILD)
    ports.tracker.states[CHILD] = IssueState("OPEN", "", (), None)
    ports.tracker.held_issues_error = TrackerTimedOut("gh timed out")
    lines, launcher = _stack_go(tmp_path, ports)
    assert launcher.spawned == []
    assert "agentic: poll: holds unread (timed_out)" in lines
    assert len(_notes(ports, CHILD)) == 1
