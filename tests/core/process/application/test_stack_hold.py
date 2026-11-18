"""GH-899: the hold of a stacked child. `compute`, `free_writer` and `write_episodes` run over
counting doubles; each test says what was asked of the tracker and what was written."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast, get_args

import pytest

from agentic.core.process.application import stack_hold
from agentic.core.process.application.ports import (
    IssueComment,
    IssueState,
    LockRecord,
    RunRegistration,
    Upstream,
)
from agentic.core.process.application.stack_facts import StackMemo
from agentic.core.process.domain import stacking
from agentic.core.process.domain.stacking import NotedCause
from agentic.core.process.domain.task import StackParent, TrackerFailure
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryDocuments, MemoryStack, MemoryTracker

TIP = "e" * 40
CFG = Configuration(product_dir=Path("/p"))
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
STARTED = "2026-11-16T10:00:00+00:00"


def _head(n: int) -> str:
    return f"{n:040x}"


def _record(n: int) -> StackParent:
    return StackParent(issue=n, task_id=f"GH-{n}", head=_head(n), started_at=STARTED)


class _Workspace:
    def __init__(self) -> None:
        self.ancestors: set[tuple[str, str]] = set()
        self.upstream_calls = 0

    def fetch_commit(self, sha: str) -> bool:
        return True

    def is_ancestor(self, path: Path, sha: str, ref: str = "HEAD") -> bool:
        return (sha, ref) in self.ancestors

    def upstream(self) -> Upstream:
        self.upstream_calls += 1
        return Upstream("origin/main", TIP, None)


class _Tracker(MemoryTracker):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.unreadable = False
        self.fail_note = False
        self.noted: list[tuple[str, str]] = []

    def timestamped_comments(self, task_id: str) -> list[IssueComment]:
        self.reads += 1
        if self.unreadable:
            raise ValueError("gh issue view failed")
        return super().timestamped_comments(task_id)

    def note(self, task_id: str, text: str) -> TrackerFailure | None:
        self.noted.append((task_id, text))
        if self.fail_note:
            return TrackerFailure(target="comment", cls="transient", at="2026-11-17T12:00:00Z")
        return None


class _Inbox:
    def task_id(self, number: int) -> str:
        return f"GH-{number}"


class _Lock:
    def __init__(self) -> None:
        self.alive: set[str] = set()
        self.records: dict[str, LockRecord] = {}

    def read(self, task_id: str) -> LockRecord | None:
        return self.records.get(task_id)

    def refusal(self, task_id: str) -> str | None:
        return "held" if task_id in self.alive else None


@dataclass
class _World:
    workspace: _Workspace = field(default_factory=_Workspace)
    tracker: _Tracker = field(default_factory=_Tracker)
    documents: MemoryDocuments = field(default_factory=MemoryDocuments)
    inbox: _Inbox = field(default_factory=_Inbox)
    stack: MemoryStack = field(default_factory=MemoryStack)
    lock: _Lock = field(default_factory=_Lock)
    memo: StackMemo = field(default_factory=StackMemo)
    live: list[RunRegistration] = field(default_factory=list)

    @property
    def ports(self) -> Any:
        return self

    def parent(self, n: int, **doc: object) -> StackParent:
        self.tracker.states[n] = IssueState("OPEN", "", (), None, ())
        self.documents.save(cast(Any, {"id": f"GH-{n}", **doc}))
        return _record(n)

    def child(self, n: int, parent: StackParent | None, **doc: object) -> None:
        extra = {"stack_parent": dict(parent)} if parent is not None else {}
        self.documents.save(cast(Any, {"id": f"GH-{n}", **extra, **doc}))

    def queued(self, *numbers: int) -> dict[int, Any]:
        return {n: self.documents.load(f"GH-{n}") for n in numbers}

    def compute(self, queued: dict[int, Any]) -> stack_hold.StackHold:
        return stack_hold.compute(
            self.ports,
            CFG,
            self.lock,
            queued,
            self.workspace.upstream,
            self.memo,
            self.live,
        )

    def episodes(self, queued: dict[int, Any], now: datetime = NOW, **flags: bool) -> list[str]:
        self.memo = StackMemo()
        hold = self.compute(queued)
        return stack_hold.write_episodes(
            self.ports,
            self.lock,
            hold,
            queued,
            now,
            dry_run=flags.get("dry_run", False),
            space_held=flags.get("space_held", False),
        )

    def rows(self, task_id: str) -> list[dict[str, Any]]:
        stored = (self.documents.load(task_id) or {}).get("stack_pauses")
        return list(stored) if isinstance(stored, list) else []


def _escalated_world() -> tuple[_World, StackParent]:
    world = _World()
    parent = world.parent(7, escalated="CI red")
    world.child(20, parent)
    return world, parent


def _text(parent: int, cause: NotedCause, k: int) -> str:
    return (
        stacking.pause_note(parent, cause)
        + "\n"
        + f"<!-- agentic-stack-pause: {parent} {cause} {k} -->"
    )


# --- compute ------------------------------------------------------------------------------------


def test_three_children_of_one_parent_read_the_parent_once_and_are_all_held():
    world = _World()
    parent = world.parent(7, escalated="CI red")
    for n in (20, 21, 22):
        world.child(n, parent)
    hold = world.compute(world.queued(20, 21, 22))
    assert hold.held == dict.fromkeys((20, 21, 22), "parent_escalated")
    assert world.tracker.issue_state_calls[7] == 1
    assert world.workspace.upstream_calls == 1


def test_a_round_with_no_stacked_document_reads_nothing():
    world = _World()
    world.child(20, None, status="ready_for_review")
    queued = {**world.queued(20), 22: None}
    hold = world.compute(queued)
    assert hold == stack_hold.StackHold()
    assert (world.workspace.upstream_calls, world.tracker.issue_state_calls) == (0, {})


def test_an_unreadable_parent_says_one_line_and_two_rounds_read_no_comment():
    world = _World()
    parent = world.parent(7)
    world.child(20, parent)
    del world.tracker.states[7]  # the parent issue cannot be read: unreadable
    for _ in range(2):
        assert world.episodes(world.queued(20)) == []
    hold = world.compute(world.queued(20))
    assert hold.held == {20: "unreadable"}
    assert hold.lines == ["agentic: poll: #20 held (stacked, parent unreadable)"]
    assert (world.tracker.reads, world.tracker.noted, world.documents.closures) == (0, [], {})


def test_a_parent_in_the_upstream_holds_nothing():
    world = _World()
    parent = world.parent(7, escalated="x")
    world.workspace.ancestors.add((parent["head"], TIP))
    world.child(20, parent)
    hold = world.compute(world.queued(20))
    assert (hold.held, hold.causes) == ({}, {20: "none"})


# --- free_writer --------------------------------------------------------------------------------


def test_free_writer_reads_a_dead_lock_as_free_and_a_live_one_as_not():
    lock = _Lock()
    assert stack_hold.free_writer("GH-20", frozenset(), lock) is True
    lock.alive.add("GH-20")
    assert stack_hold.free_writer("GH-20", frozenset(), lock) is False
    assert stack_hold.free_writer("GH-21", frozenset({"GH-21"}), _Lock()) is False


def test_free_writer_reads_a_lock_that_cannot_be_read_as_not_free():
    class _Unreadable(_Lock):
        def refusal(self, task_id: str) -> str | None:
            raise OSError("lock file unreadable")

    assert stack_hold.free_writer("GH-20", frozenset(), _Unreadable()) is False


# --- write_episodes: the table, one test per row ------------------------------------------------


def test_an_escalated_parent_leaves_one_note_with_the_exact_text_and_one_open_row():
    world, _ = _escalated_world()
    assert world.episodes(world.queued(20)) == []
    assert world.tracker.noted == [("GH-20", _text(7, "parent_escalated", 1))]
    assert world.rows("GH-20") == [
        {"parent": 7, "cause": "parent_escalated", "at": NOW.isoformat(), "resumed_at": None}
    ]


def test_a_second_round_writes_no_second_note():
    world, _ = _escalated_world()
    world.episodes(world.queued(20))
    reads = world.tracker.reads
    world.episodes(world.queued(20), LATER)
    assert len(world.tracker.noted) == 1
    assert world.tracker.reads == reads
    assert len(world.rows("GH-20")) == 1


def test_a_marker_already_on_the_issue_writes_the_row_and_no_note():
    world, _ = _escalated_world()
    world.tracker.timestamped["GH-20"] = [
        IssueComment(_text(7, "parent_escalated", 1), "2026-11-17T11:00:00Z")
    ]
    world.episodes(world.queued(20))
    assert world.tracker.noted == []
    assert [r["cause"] for r in world.rows("GH-20")] == ["parent_escalated"]


def test_an_unread_comment_list_writes_no_note_and_no_row_and_says_so():
    world, _ = _escalated_world()
    world.tracker.unreadable = True
    lines = world.episodes(world.queued(20))
    assert lines == ["agentic: poll: #20 stack pause note unread"]
    assert (world.tracker.noted, world.documents.closures) == ([], {})


def test_a_failed_note_goes_to_tracker_failures_and_the_row_is_recorded_anyway():
    world, _ = _escalated_world()
    world.tracker.fail_note = True
    world.episodes(world.queued(20))
    closure = world.documents.closures["GH-20"]
    assert closure["tracker_failures"] == [
        {"target": "comment", "cls": "transient", "at": "2026-11-17T12:00:00Z"}
    ]
    assert len(world.rows("GH-20")) == 1
    world.episodes(world.queued(20), LATER)
    assert len(world.tracker.noted) == 1  # a failed note is not retried


def test_a_child_with_a_live_run_or_lock_gets_no_note_no_read_and_a_line():
    world, _ = _escalated_world()
    world.lock.alive.add("GH-20")
    assert world.episodes(world.queued(20)) == ["agentic: poll: #20 held (stacked, at work)"]
    world.lock.alive.clear()
    world.live.append(RunRegistration(1, "h", "product", "GH-20"))
    assert world.episodes(world.queued(20)) == ["agentic: poll: #20 held (stacked, at work)"]
    assert (world.tracker.reads, world.tracker.noted, world.documents.closures) == (0, [], {})


def test_a_run_of_another_product_does_not_make_the_child_busy():
    world, _ = _escalated_world()
    world.live.append(RunRegistration(1, "h", "elsewhere", "GH-20"))
    world.episodes(world.queued(20))
    assert len(world.tracker.noted) == 1


@pytest.mark.parametrize("flag", ["dry_run", "space_held"])
def test_a_dry_or_space_held_round_reads_no_comment_and_writes_nothing(flag: str):
    world, _ = _escalated_world()
    assert world.episodes(world.queued(20), **{flag: True}) == []
    assert (world.tracker.reads, world.tracker.noted, world.documents.closures) == (0, [], {})


# --- episodes over time -------------------------------------------------------------------------


def test_the_cause_clearing_stamps_resumed_at_and_keeps_the_original_at():
    world, parent = _escalated_world()
    world.episodes(world.queued(20))
    world.documents.save(cast(Any, {"id": "GH-7"}))  # the owner answered: not escalated any more
    world.workspace.ancestors.add((parent["head"], TIP))
    world.episodes(world.queued(20), LATER)
    assert world.rows("GH-20") == [
        {
            "parent": 7,
            "cause": "parent_escalated",
            "at": NOW.isoformat(),
            "resumed_at": LATER.isoformat(),
        }
    ]
    world.episodes(world.queued(20), LATER + timedelta(minutes=5))
    assert len(world.rows("GH-20")) == 1  # sealing is not repeated into a duplicate


def test_a_dead_lock_child_is_sealed_but_a_live_one_is_not():
    world, parent = _escalated_world()
    world.episodes(world.queued(20))
    world.workspace.ancestors.add((parent["head"], TIP))
    world.lock.alive.add("GH-20")
    world.episodes(world.queued(20), LATER)
    assert world.rows("GH-20")[0]["resumed_at"] is None
    world.lock.alive.clear()
    world.episodes(world.queued(20), LATER)
    assert world.rows("GH-20")[0]["resumed_at"] == LATER.isoformat()


def test_a_to_b_to_a_numbers_the_episodes_one_then_two_and_seals_the_old_row():
    world, _ = _escalated_world()
    world.episodes(world.queued(20))
    world.lock.records["GH-7"] = LockRecord(1, "h", STARTED, "run", stopped_deliberately=True)
    world.documents.save(cast(Any, {"id": "GH-7"}))  # idle but stopped: parent_stopped
    world.episodes(world.queued(20), LATER)
    assert [r["cause"] for r in world.rows("GH-20")] == ["parent_escalated", "parent_stopped"]
    assert [r["resumed_at"] for r in world.rows("GH-20")] == [LATER.isoformat(), None]
    del world.lock.records["GH-7"]
    world.documents.save(cast(Any, {"id": "GH-7", "escalated": "CI red"}))
    world.episodes(world.queued(20), LATER + timedelta(minutes=5))
    assert [text for _, text in world.tracker.noted] == [
        _text(7, "parent_escalated", 1),
        _text(7, "parent_stopped", 1),
        _text(7, "parent_escalated", 2),
    ]
    assert len([r for r in world.rows("GH-20") if r["resumed_at"] is None]) == 1


def test_a_changed_parent_opens_a_new_row_and_a_new_note():
    world, _ = _escalated_world()
    world.episodes(world.queued(20))
    other = world.parent(8, escalated="x")
    world.child(20, other, stack_pauses=world.rows("GH-20"))
    world.episodes(world.queued(20), LATER)
    assert [(r["parent"], r["resumed_at"] is None) for r in world.rows("GH-20")] == [
        (7, False),
        (8, True),
    ]
    assert world.tracker.noted[-1] == ("GH-20", _text(8, "parent_escalated", 1))


# --- the notes of the poller are the ones the plan knows ----------------------------------------


@pytest.mark.parametrize("cause", get_args(NotedCause))
def test_every_noted_cause_posts_exactly_pause_note_and_the_marker(cause: NotedCause):
    world = _World()
    parent = world.parent(7)
    world.child(20, parent)
    hold = stack_hold.StackHold(
        held={20: cause}, causes={20: cause}, parents={20: 7}, busy=frozenset()
    )
    stack_hold.write_episodes(
        world.ports, world.lock, hold, world.queued(20), NOW, dry_run=False, space_held=False
    )
    assert world.tracker.noted == [("GH-20", _text(7, cause, 1))]


_APPLICATION = Path(__file__).resolve().parents[4] / "agentic/core/process/application"
_NOTE_CALLERS = {
    ("merge", "_note"),
    ("poll", "_announce_spent"),
    ("refusal_note", "note_refusal"),
    ("resume", "announce"),
    ("stack_hold", "_open_episode"),
}


def _note_callers(root: Path) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(root.glob("*.py")):
        for function in ast.walk(ast.parse(path.read_text())):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call in ast.walk(function):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "note"
                    and isinstance(call.func.value, ast.Attribute)
                    and call.func.value.attr == "tracker"
                ):
                    found.add((path.stem, function.name))
    return found


def test_the_tracker_note_callers_of_the_application_are_the_known_five():
    found = _note_callers(_APPLICATION)
    assert found == _NOTE_CALLERS, (
        f"new Tracker.note callers: {sorted(found - _NOTE_CALLERS)}; "
        f"missing: {sorted(_NOTE_CALLERS - found)}"
    )


def test_the_caller_check_rejects_a_new_note_site(tmp_path: Path):
    (tmp_path / "rogue.py").write_text("def speak(ports):\n    ports.tracker.note('GH-1', 'x')\n")
    assert _note_callers(tmp_path) == {("rogue", "speak")}


_NOTE_EXPRESSION = 'stacking.pause_note(parent, cause) + "\\n" + tag'
_NOTE_TEXT = ast.dump(ast.parse(_NOTE_EXPRESSION, mode="eval").body)
_TAG = ast.dump(ast.parse("marker(parent, cause, k)", mode="eval").body)
_NOTE_SITE = (
    "def f(ports, task_id, parent, cause, k):\n"
    "    tag = marker(parent, cause, k)\n"
    "    ports.tracker.note(task_id, {text})\n"
)


def _note_text_violations(source: str) -> list[str]:
    """What is wrong with the `.note` calls of a module: it must hold exactly one, whose text is
    `stacking.pause_note(parent, cause) + "\\n" + tag`, with `tag` assigned once from
    `marker(parent, cause, k)`."""
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "note"
    ]
    if len(calls) != 1:
        return [f"{len(calls)} note calls, expected exactly one"]
    problems = []
    args = calls[0].args
    if len(args) != 2 or calls[0].keywords or ast.dump(args[1]) != _NOTE_TEXT:
        problems.append(f"note text is not pause_note + newline + tag: {ast.unparse(calls[0])}")
    tags = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["tag"]
    ]
    if len(tags) != 1 or ast.dump(tags[0].value) != _TAG:
        problems.append("`tag` is not assigned once from marker(parent, cause, k)")
    return problems


def test_stack_hold_passes_only_pause_note_output_to_the_tracker():
    assert _note_text_violations((_APPLICATION / "stack_hold.py").read_text()) == []


def test_the_note_text_check_accepts_the_exact_shape():
    assert _note_text_violations(_NOTE_SITE.format(text=_NOTE_EXPRESSION)) == []


@pytest.mark.parametrize(
    "text",
    [
        '"literal"',
        'stacking.pause_note(parent, cause) + "x"',
        'stacking.pause_note(parent, cause) + "\\n" + tag + "x"',
        'stacking.pause_note(parent, cause) + "\\n"',
        'other.pause_note(parent, cause) + "\\n" + tag',
    ],
)
def test_the_note_text_check_rejects_a_text_that_is_not_pause_note_newline_marker(text: str):
    assert _note_text_violations(_NOTE_SITE.format(text=text)) != []


def test_the_note_text_check_rejects_a_second_note_call_and_a_foreign_tag():
    site = _NOTE_SITE.format(text=_NOTE_EXPRESSION)
    assert _note_text_violations(site + "    ports.tracker.note(task_id, 'again')\n") != []
    assert _note_text_violations(site.replace("marker(parent, cause, k)", "'free'")) != []


def _imports_stack_hold(root: Path) -> list[str]:
    offenders = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(a.name for a in node.names)]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(name.split(".")[-1] == "stack_hold" for name in names):
                offenders.append(path.name)
    return offenders


def test_no_module_of_agentic_but_poll_imports_stack_hold():
    offenders = [n for n in _imports_stack_hold(_APPLICATION.parents[2]) if n != "poll.py"]
    assert offenders == [], offenders


def test_the_import_check_rejects_a_rogue_importer(tmp_path: Path):
    (tmp_path / "rogue.py").write_text("from .application import stack_hold\n")
    assert _imports_stack_hold(tmp_path) == ["rogue.py"]


def test_a_stack_pauses_block_is_replaced_by_one_flow_line_and_read_back():
    from agentic.core.process.domain import document

    rows = [{"parent": 7, "cause": "parent_escalated", "at": "t", "resumed_at": None}]
    raw = (
        "---\nid: GH-20\nstack_pauses:\n- parent: 7\n  cause: x\n  at: t\n  resumed_at: null\n"
        "status: done\n---\nbody\n"
    )
    written = document.record_closure(raw, {"stack_pauses": rows})
    assert written.count("stack_pauses:") == 1
    assert "\nstatus: done\n" in written
    assert stack_hold._rows(document.parse(written)) == rows
