"""GH-621 part 2: the poller reads the head-budget facts, writes each turn through one writer
(`_record_turn`) and says each yield once. The writer is exercised on real documents."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import poll
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import landing_order
from tests.core.process.application.test_poll import _Locks
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    _ready,
)
from tests.fakes import MemoryTracker, memory_ports

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
STUCK = {"merge_checks": {"head": HEAD, "master": MASTER, "passed": False}}


def _ports(tmp_path, *numbers: int, stuck: tuple[int, ...] = ()):
    ports = memory_ports(documents=FilesystemTaskDocuments(tmp_path), tracker=MemoryTracker())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False
    for number in numbers:
        _ready(ports, number, title=f"t{number}", description="d")
        ports.documents.record_closure(
            f"GH-{number}",
            {"landing_queued_at": (T0 - timedelta(hours=10 - number)).isoformat()},
        )
        if number in stuck:
            ports.documents.record_closure(f"GH-{number}", {"merge_checks": STUCK["merge_checks"]})
    return ports


def _round(ports, minutes: float, **more):
    lines: list[str] = []
    found = poll.gather_merges(
        ports, frozenset(), MASTER, _Locks(), lines, now=T0 + timedelta(minutes=minutes), **more
    )
    return found, lines


def _doc(ports, number: int):
    return ports.documents.load(f"GH-{number}")


def test_a_new_head_is_recorded_once_and_the_next_round_inside_five_minutes_writes_nothing(
    tmp_path,
):
    ports = _ports(tmp_path, 5, 6)

    _round(ports, 0)
    first = _doc(ports, 5)
    assert first["head_seen_at"] == T0.isoformat()
    assert first["head_turn_at"] == T0.isoformat()
    assert first["head_since_head"] == HEAD
    assert "head_seen_at" not in _doc(ports, 6)

    before = (tmp_path / ".agentic/tasks/GH-5.md").read_text()
    _round(ports, 2)
    assert (tmp_path / ".agentic/tasks/GH-5.md").read_text() == before


def test_a_stuck_head_yields_after_one_budget_of_observed_rounds_and_the_second_leads(tmp_path):
    ports = _ports(tmp_path, 5, 6, stuck=(5,))
    said: list[str] = []
    found: list[int] = []
    for minute in range(0, 60, 5):
        found, lines = _round(ports, minute)
        said += [line for line in lines if "yielded" in line]

    assert said == ["agentic: poll: landing order #5 -> yielded (budget_time) to #6"]
    first = _doc(ports, 5)
    assert first["head_yielded_at"] is not None
    assert [(y["head"], y["reason"]) for y in first["head_yields"]] == [(HEAD, "budget_time")]
    assert "head_seen_at" not in first
    assert "head_turn_at" not in first
    assert found == [6]
    assert _doc(ports, 6)["head_seen_at"] is not None


def test_a_full_save_between_two_yields_does_not_break_the_next_record(tmp_path):
    ports = _ports(tmp_path, 5, 6, stuck=(5, 6))
    for minute in range(0, 50, 5):
        _round(ports, minute)
    state = _doc(ports, 5)
    assert len(state["head_yields"]) == 1
    ports.documents.save(state)  # a child's full save rewrites the document whole

    lines: list[str] = []
    for minute in range(50, 130, 5):
        lines += _round(ports, minute)[1]

    assert len([line for line in lines if "yielded" in line]) >= 1
    assert len(_doc(ports, 5)["head_yields"]) >= 2 or len(_doc(ports, 6)["head_yields"]) >= 1


class _Failing(FilesystemTaskDocuments):
    """Documents whose closure writes fail once armed (a full disk, a read-only file)."""

    armed = False

    def record_closure(self, task_id, values):
        if self.armed:
            raise OSError("disk full")
        return super().record_closure(task_id, values)


def test_a_failing_write_prints_no_yield_line(tmp_path):
    ports = _ports(tmp_path, 5, 6, stuck=(5,))
    documents = _Failing(tmp_path)
    ports = dataclasses.replace(ports, documents=documents)
    for minute in range(0, 40, 5):
        _round(ports, minute)
    documents.armed = True

    lines: list[str] = []
    for minute in range(40, 60, 5):
        lines += _round(ports, minute)[1]

    assert not [line for line in lines if "yielded" in line]
    assert "head_yielded_at" not in _doc(ports, 5)


def test_a_held_round_writes_no_head_key_and_still_says_its_order(tmp_path):
    ports = _ports(tmp_path, 5, 6)
    found, lines = _round(ports, 0, held=True)

    assert found == [5]
    assert lines == ["agentic: poll: auto-merge #6 -> waiting (clean) behind #5"]
    for number in (5, 6):
        assert not [k for k in _doc(ports, number) if k.startswith("head_")]


def test_member_of_a_busy_task_with_a_running_ci_does_not_wait_on_ci(tmp_path):
    ports = _ports(tmp_path, 5)
    raw = _doc(ports, 5)
    assessed = poll.assess_merge(ports, "GH-5", raw, 5, frozenset(), MASTER)
    assert not isinstance(assessed, poll.Abandoned)
    waiting = dataclasses.replace(
        assessed, decision=dataclasses.replace(assessed.decision, kind="wait")
    )
    running = dataclasses.replace(
        assessed.report if hasattr(assessed.report, "running_runs") else _report(),
        head_sha=assessed.facts.pr_head,
        running_runs=frozenset({"1"}),
    )
    waiting = dataclasses.replace(waiting, report=running)

    idle = poll._member(ports, None, _Locks(), "GH-5", 5, raw, waiting)
    busy = poll._member(ports, None, _Locks(), "GH-5", 5, raw, waiting, busy=True)

    assert idle.waits_on_ci is True
    assert busy.waits_on_ci is False
    assert busy.busy is True


def _report():
    from agentic.core.process.domain import ci

    return ci.CiReport("x", None, None, None, ())


def test_a_busy_head_left_unobserved_for_forty_minutes_keeps_the_head(tmp_path):
    ports = _ports(tmp_path, 5, 6)
    seen = (T0 - timedelta(minutes=40)).isoformat()
    ports.documents.record_closure(
        "GH-6", {"head_seen_at": seen, "head_turn_at": seen, "head_charged_s": 600.0}
    )
    round_ = poll.evaluate_landing_order(
        ports, frozenset(), MASTER, _Locks(), frozenset({"GH-6"}), None, T0
    )
    assert round_.head == 6
    turned = next(e.turned for e in round_.entries if e.number == 6)
    assert turned is not None and turned.charged_s == 600.0


def test_the_writer_is_the_only_caller_of_the_charge_functions():
    import pathlib

    root = pathlib.Path(poll.__file__).parents[1]
    for path in root.rglob("*.py"):
        if path.name == "landing_order.py":
            continue
        text = path.read_text()
        for name in (
            "landing_order.observed(",
            "landing_order.left_head(",
            "landing_order.yielded(",
        ):
            assert name not in text, f"{path.name} calls {name}"
    assert landing_order.YIELD_MARK == "head_yields"
