"""GH-899: the poller's round holds a stacked child while its parent says so. In-memory ports and
the `Launcher` fake, through `poll.run_once` and the three gatherers that take `skip`: no `gh`, no
git."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueState, LockRecord
from agentic.core.process.application.stack_facts import StackMemo
from agentic.core.process.domain.landing import IssueRef
from agentic.core.process.domain.task import StackParent
from tests.core.process.application.test_poll import _cfg, _Locks, _no_sleep, _round, _seed_body
from tests.core.process.application.test_poll_arm import _declaration
from tests.core.process.application.test_poll_merge import MASTER, _ports, _ready
from tests.fakes import FakeExperimentFile, FakeLauncher, MemoryRunRegistry, memory_ports

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
PARENT = 7
CHILD = 20


def _record(issue: int = PARENT) -> StackParent:
    return StackParent(
        issue=issue, task_id=f"GH-{issue}", head="c" * 40, started_at="2026-11-16T10:00:00+00:00"
    )


def _world(*children: int, escalated: bool = True, **more: Any):
    """A queue of ready stacked children of one parent whose pause says `parent_escalated`
    (or, with `escalated` False, no pause: the parent's issue is open and not escalated)."""
    ports = _ports(*children, stack_parent=_record(), **more)
    ports.tracker._queue = list(children)
    ports.tracker.states[PARENT] = IssueState("OPEN", "", (), None, ())
    ports.documents.save({"id": f"GH-{PARENT}", **({"escalated": "CI red"} if escalated else {})})
    for number in children:
        ports.tracker.states[number] = IssueState("OPEN", "", (), None)
    return ports


def _go(tmp_path, ports, now: datetime = NOW, **kw: Any):
    launcher = FakeLauncher()
    lines = _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, now, **kw)
    return lines, launcher


def _notes(ports, number: int) -> list[str]:
    return [text for task, text in ports.tracker.comments if task == f"GH-{number}"]


def test_an_escalated_parent_leaves_the_child_with_no_action_and_one_note(tmp_path):
    ports = _world(CHILD)
    lines, launcher = _go(tmp_path, ports)
    assert launcher.spawned == []
    assert not any(f"#{CHILD}" in line and "auto-merge" in line for line in lines)
    assert len(_notes(ports, CHILD)) == 1


def _spy(ports) -> list[str]:
    """Wraps every public method of the tracker and the workspace; the list fills, in order, with
    `owner.name(args)` for each call the round itself makes (a double calling its own method is
    not one)."""
    calls: list[str] = []
    depth = [0]
    for label, owner in (("tracker", ports.tracker), ("workspace", ports.workspace)):
        for name in dir(owner):
            member = getattr(owner, name)
            if name.startswith("_") or not callable(member):
                continue

            def recorded(*args: Any, _m: Any = member, _l: str = label, _n: str = name, **kw: Any):
                if depth[0] == 0:
                    calls.append(f"{_l}.{_n}{args}")
                depth[0] += 1
                try:
                    return _m(*args, **kw)
                finally:
                    depth[0] -= 1

            setattr(owner, name, recorded)
    return calls


def test_a_second_round_writes_no_second_note(tmp_path):
    ports = _world(CHILD)
    _go(tmp_path, ports)
    _go(tmp_path, ports, NOW + timedelta(minutes=5))
    assert len(_notes(ports, CHILD)) == 1
    rows = ports.documents.load(f"GH-{CHILD}")["stack_pauses"]
    assert [(r["parent"], r["cause"], r["resumed_at"]) for r in rows] == [
        (PARENT, "parent_escalated", None)
    ]


def test_the_cause_clearing_resumes_the_child_and_stamps_resumed_at(tmp_path):
    ports = _world(CHILD)
    _go(tmp_path, ports)
    ports.documents.save({"id": f"GH-{PARENT}"})  # the parent is not escalated any more
    later = NOW + timedelta(minutes=5)
    lines, _ = _go(tmp_path, ports, later)
    (row,) = ports.documents.load(f"GH-{CHILD}")["stack_pauses"]
    assert row["at"] == NOW.isoformat()
    assert row["resumed_at"] == later.isoformat()
    # evaluated again: the pause is over, and the unmerged parent (GH-838) keeps the child out of
    # the Cabeza, so it waits with the stacking reason instead of being refused as head
    assert any(
        f"auto-merge #{CHILD} -> waiting (" in line and "stacked parent unmerged" in line
        for line in lines
    )
    assert len(_notes(ports, CHILD)) == 1


def test_a_held_child_gets_its_note_from_the_whole_queue_and_no_action(tmp_path):
    ports = _world(CHILD)
    ports.tracker.states[CHILD] = IssueState("OPEN", "", (), None)
    lines, launcher = _go(tmp_path, ports)
    assert launcher.spawned == []
    assert not any(f"#{CHILD}" in line for line in lines if "held" not in line)
    assert len(_notes(ports, CHILD)) == 1


def test_a_held_child_with_a_dead_lock_is_not_retaken_and_nothing_is_read_for_it(tmp_path):
    ports = _world(CHILD)
    task_id = f"GH-{CHILD}"
    record = LockRecord(777, "h", "2026-11-17T08:00:00Z", "agentic run", "boot-old")
    locks = _Locks({task_id: record})
    spent: dict = {}
    seen: dict = {}
    reported: set[int] = set()
    retakes, lines = poll.gather_retakes(
        ports,
        locks,
        "boot-now",
        now=NOW,
        spent=spent,
        seen=seen,
        reported=reported,
        skip=frozenset({CHILD}),
    )
    assert (retakes, lines, spent, seen, reported) == ([], [], {}, {}, set())
    assert f"GH-{CHILD}" not in locks.reads
    calls = _spy(ports)
    lines, launcher = _go(tmp_path, ports, lock=locks)
    assert launcher.spawned == []
    assert not any("retake" in line or "resume" in line for line in lines)
    assert ports.documents.load(task_id).get("blocker_reports") is None
    about_child = [
        c for c in calls if f"'{task_id}'" in c or f"({CHILD}," in c or f"({CHILD})" in c
    ]
    assert [c.split("(")[0] for c in about_child] == [
        "tracker.timestamped_comments",  # the pause marker's own read
        "tracker.note",  # the one note
    ]
    assert len(_notes(ports, CHILD)) == 1


def test_gather_resumes_skips_a_held_issue_before_reading_its_comments():
    ports = _world(CHILD)
    ports.documents.save({"id": f"GH-{CHILD}", "escalated": "x", "status": "developing"})
    reads: list[str] = []
    original = ports.tracker.timestamped_comments
    ports.tracker.timestamped_comments = lambda task_id: (reads.append(task_id), original(task_id))[
        1
    ]
    assert poll.gather_resumes(ports, NOW, skip=frozenset({CHILD})) == []
    assert reads == []


def test_gather_merges_skips_a_held_head_and_the_next_pull_request_is_the_head(tmp_path):
    ports = _world(CHILD)
    _ready(ports, 21, checks=True)  # a plain task behind it
    ports.documents.save(
        {**ports.documents.load(f"GH-{CHILD}"), "landing_queued_at": "2026-11-01T00:00:00+00:00"}
    )
    ports.documents.save(
        {**ports.documents.load("GH-21"), "landing_queued_at": "2026-11-10T00:00:00+00:00"}
    )
    before = ports.documents.load(f"GH-{CHILD}")
    lines: list[str] = []
    found = poll.gather_merges(
        ports,
        frozenset(),
        MASTER,
        _Locks(),
        lines,
        cfg=_cfg(tmp_path),
        dry_run=False,
        memo=StackMemo(),
        skip=frozenset({CHILD}),
    )
    assert found == [21]
    assert not any(f"#{CHILD}" in line for line in lines)
    assert ports.documents.load(f"GH-{CHILD}") == before  # no turn charged, nothing recorded


def test_a_dry_round_filters_the_held_child_and_reads_and_writes_nothing(tmp_path):
    ports = _world(CHILD)
    reads: list[str] = []
    original = ports.tracker.timestamped_comments
    ports.tracker.timestamped_comments = lambda task_id: (reads.append(task_id), original(task_id))[
        1
    ]
    lines, launcher = _go(tmp_path, ports, dry_run=True)
    assert launcher.spawned == []
    assert not any(f"issue {CHILD}" in line for line in lines)
    assert [r for r in reads if r == f"GH-{CHILD}"] == []
    assert _notes(ports, CHILD) == []
    assert "stack_pauses" not in ports.documents.load(f"GH-{CHILD}")


def _recorded_round(tmp_path, monkeypatch, *, without_hold: bool) -> list[str]:
    """Every call a round made to the tracker and the workspace, in order, as `name(args)`."""
    ports = _ports(5, 6)
    ports.tracker._queue = [5, 6]
    for n in (5, 6):
        ports.tracker.states[n] = IssueState("OPEN", "", (), None)
    calls = _spy(ports)
    if without_hold:
        monkeypatch.setattr(poll.stack_hold, "compute", lambda *a, **k: poll.stack_hold.StackHold())
        monkeypatch.setattr(poll.stack_hold, "write_episodes", lambda *a, **k: [])
    _go(tmp_path, ports)
    return calls


def test_a_round_with_no_stacked_document_makes_the_reads_it_made_without_the_hold(
    tmp_path, monkeypatch
):
    with_hold = _recorded_round(tmp_path, monkeypatch, without_hold=False)
    without = _recorded_round(tmp_path, monkeypatch, without_hold=True)
    assert with_hold == without
    assert with_hold  # a round that reads nothing would prove nothing


def _working_child(ports, **more: Any) -> LockRecord:
    """The child as a run left it: mid-development, in the tray, its lock from a boot gone."""
    ports.documents.save(
        {
            "id": f"GH-{CHILD}",
            "status": "developing",
            "stack_parent": dict(_record()),
            **more,
        }
    )
    return LockRecord(777, "h", "2026-11-16T08:00:00Z", "agentic run", "boot-old")


def test_a_dead_lock_child_written_30h_ago_is_retaken_the_round_its_hold_ends_without_a_notice(
    tmp_path,
):
    ports = _world()
    ports.tracker._queue = [CHILD]
    ports.tracker.states[CHILD] = IssueState("OPEN", "", (), None)
    locks = _Locks({f"GH-{CHILD}": _working_child(ports)})
    _, launcher = _go(tmp_path, ports, NOW - timedelta(hours=30), lock=locks)
    assert launcher.spawned == []  # held: no retake while the parent is escalated
    ports.documents.save({"id": f"GH-{PARENT}"})
    ports.documents.untouched = {f"GH-{CHILD}"}  # nothing wrote it since: it is 30 h old
    _, launcher = _go(tmp_path, ports, NOW, lock=locks)
    assert [argv[1] for argv in launcher.spawned] == ["resume"]
    assert not any(poll.RetakesSpent.__name__ in text for _, text in ports.tracker.comments)
    (row,) = ports.documents.load(f"GH-{CHILD}")["stack_pauses"]
    assert row["resumed_at"] == NOW.isoformat()


def test_a_child_with_no_lock_written_3h_ago_waits_its_quiet_once_the_parent_is_green(tmp_path):
    ports = _world()
    ports.tracker._queue = [CHILD]
    ports.tracker.states[CHILD] = IssueState("OPEN", "", (), None)
    _working_child(ports)
    _go(tmp_path, ports, NOW - timedelta(hours=3))
    ports.documents.save({"id": f"GH-{PARENT}"})
    _, launcher = _go(tmp_path, ports, NOW - timedelta(hours=3) + timedelta(minutes=5))
    assert launcher.spawned == []  # sealed that round, and the quiet is still to wait
    assert ports.documents.load(f"GH-{CHILD}")["stack_pauses"][0]["resumed_at"] is not None


def test_the_final_filter_drops_an_action_a_gatherer_still_planned_for_a_held_issue(
    tmp_path, monkeypatch
):
    ports = _world(CHILD)
    monkeypatch.setattr(poll, "gather_merges", lambda *a, **k: [CHILD])
    lines, launcher = _go(tmp_path, ports)
    assert launcher.spawned == []
    assert not any(f"merge issue {CHILD}" in line for line in lines)


# The poller's own functions, taken before any test wraps them.
_ARM_COUNTS = poll._arm_counts
_DEPENDENTS = poll.launch_order.dependents


def _launch_round(tmp_path, monkeypatch, *, without_hold: bool):
    """A round with two plain launches beside a held stacked child that already has its arm and
    whose open issue says `Blocked by: #5`; what `_arm_counts` and `launch_order.dependents`
    answered, and what was spawned."""
    ports = memory_ports(experiment=FakeExperimentFile(_declaration()))
    ports.tracker._queue = [5, 6, CHILD]
    for number in (5, 6, CHILD, PARENT):
        ports.tracker.states[number] = IssueState("OPEN", "", (), None, ())
    for number in (5, 6):
        _seed_body(ports, number)
    ports.documents.save({"id": f"GH-{PARENT}", "escalated": "CI red"})
    ports.documents.save(
        {
            "id": f"GH-{CHILD}",
            "status": "developing",
            "stack_parent": dict(_record()),
            "assignment": {"experiment": "light-vs-default", "switches": {}, "arm": "control"},
        }
    )
    ports.inbox.refs = [
        IssueRef(f"https://github.com/o/r/issues/{CHILD}", "child", "Blocked by: #5"),
    ]
    counts: list[dict[str, int]] = []
    dependents: list[dict[int, int]] = []
    arm_counts, order = _ARM_COUNTS, _DEPENDENTS

    def counted(*args: Any) -> dict[str, int]:
        counts.append(dict(arm_counts(*args)))
        return arm_counts(*args)

    def ordered(edges: Any) -> dict[int, int]:
        dependents.append(dict(order(edges)))
        return order(edges)

    monkeypatch.setattr(poll, "_arm_counts", counted)
    monkeypatch.setattr(poll.launch_order, "dependents", ordered)
    if without_hold:
        monkeypatch.setattr(poll.stack_hold, "compute", lambda *a, **k: poll.stack_hold.StackHold())
        monkeypatch.setattr(poll.stack_hold, "write_episodes", lambda *a, **k: [])
    launcher = FakeLauncher()
    _round(_cfg(tmp_path), ports, MemoryRunRegistry(), launcher, NOW, sleep=_no_sleep)
    return counts, dependents, launcher.spawned, ports


def test_arm_counts_and_launch_weights_with_a_held_child_equal_the_round_without_the_hold(
    tmp_path, monkeypatch
):
    held = _launch_round(tmp_path, monkeypatch, without_hold=False)
    free = _launch_round(tmp_path, monkeypatch, without_hold=True)
    assert held[:3] == free[:3]
    assert held[0] == [{"control": 1}]  # the held child's own arm is counted, once
    assert held[1] == [{}]  # its issue has a document: it adds no edge to #5
    # one slot: #5 launches, with the arm that is behind (control holds the child's one)
    assert [argv[3:6] for argv in held[2]] == [["5", "--arm", "light"]]
    assert len(_notes(held[3], CHILD)) == 1 and _notes(free[3], CHILD) == []
