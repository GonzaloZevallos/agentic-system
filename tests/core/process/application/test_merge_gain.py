"""GH-353, ADR-0045 addendum: the merge verb reads what master gained since a recorded check
pass, and merges on that pass without a suite only when the gain is inert. Every case runs
`merge_task` over the fakes; the doubt cases each pin that one unreadable input runs the
checks, and that nothing is read before every refusal ahead of the checks has passed."""

from __future__ import annotations

import pytest

from agentic.core.process.application import merge
from agentic.core.process.application.ports import GitError, Upstream
from agentic.core.process.domain.task import PullRequest
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryCommands, MemoryStack, MemoryTracker, memory_ports

HEAD = "a" * 40
OLD = "b" * 40
NEW = "c" * 40
MID = "d" * 40
URL = "https://github.com/o/r/pull/7"
INERT = (".agentic/tasks/*.md",)
NOTE = ".agentic/tasks/GH-9.md"


def _row(master: str | None, name: str = "tests", passed: bool = True) -> dict:
    row: dict = {"name": name, "passed": passed}
    if master is not None:
        row["master"] = master
    return row


def _record(master: str, rows: list[dict] | None = None, passed: bool = True) -> dict:
    return {
        "head": HEAD,
        "master": master,
        "passed": passed,
        "duration_s": 1.0,
        "checks": [_row(master)] if rows is None else rows,
    }


class World:
    def __init__(self, record: dict | None, *, inert=INERT, touched=("src/app.py",), **doc):
        self.ports = memory_ports(
            tracker=MemoryTracker(),
            stack=MemoryStack(checks={"tests": "pytest"}, merge_inert=inert),
            commands=MemoryCommands(),
        )
        workspace = self.ports.workspace
        workspace.upstream_answer = Upstream("origin/master", NEW, None)
        workspace.existing_branches["agentic/GH-7"] = ("tip", "taskbase1")
        workspace.diff_paths = touched
        workspace.contains_answer = "yes"
        workspace.gains[(OLD, NEW)] = (NOTE,)
        self.ports.tracker.prs[URL] = PullRequest(
            state="OPEN",
            created_at="2026-10-05T00:00:00Z",
            merged_at=None,
            commits=(),
            head_oid=HEAD,
        )
        document = {
            "id": "GH-7",
            "status": "ready_for_review",
            "pull_request": URL,
            "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": HEAD}},
            **doc,
        }
        if record is not None:
            document["merge_checks"] = record
        self.ports.documents.save(document)
        self.log: list[str] = []

    def merge(self):
        cfg = Configuration(product_dir=self.ports.workspace.ROOT)
        upstream = Upstream("origin/master", NEW, None)
        return merge.merge_task("GH-7", 7, cfg, self.ports, upstream, frozenset(), self.log.append)

    def doc(self) -> dict:
        return self.ports.documents.load("GH-7") or {}


def _ran_checks(world: World) -> bool:
    return bool(world.ports.workspace.detached)


def test_an_inert_gain_merges_on_the_recorded_pass_with_no_suite():
    world = World(_record(OLD))
    result = world.merge()
    assert result.kind == "merged"
    assert world.ports.workspace.detached == []
    assert world.ports.commands.ran == []
    document = world.doc()
    assert "merge_attempts" not in document
    assert document["auto_merge_inert_from"] == [OLD]
    assert document["auto_merge_master"] == NEW
    assert world.ports.workspace.gain_reads == [(OLD, NEW)]
    assert (NEW, OLD) in world.ports.workspace.contains_calls


def test_a_gain_that_is_not_inert_runs_the_suite():
    world = World(_record(OLD))
    world.ports.workspace.gains[(OLD, NEW)] = (NOTE, "src/app.py")
    world.merge()
    assert _ran_checks(world)
    assert world.doc()["merge_attempts"]["count"] == 1
    assert world.doc()["merge_checks"]["master"] == NEW


@pytest.mark.parametrize("path", [".agentic/stack.yaml", ".github/workflows/ci.yml", "uv.lock"])
def test_a_never_inert_path_in_the_gain_runs_the_suite_whatever_is_declared(path):
    world = World(_record(OLD), inert=(".agentic/**", ".github/**", "uv.lock", "*/x"))
    world.ports.workspace.gains[(OLD, NEW)] = (path,)
    world.merge()
    assert _ran_checks(world)


def test_no_declared_merge_inert_runs_the_suite_and_never_reads_a_gain():
    world = World(_record(OLD), inert=())
    world.merge()
    assert _ran_checks(world)
    assert world.ports.workspace.gain_reads == []
    assert world.ports.workspace.contains_calls == []


def test_a_refused_pull_request_reads_no_gain():
    world = World(_record(OLD))
    document = world.doc()
    document["subtasks"]["only"]["status"] = "needs_changes"
    world.ports.documents.save(document)
    result = world.merge()
    assert result.kind == "refused"
    assert world.ports.workspace.gain_reads == []
    assert world.ports.workspace.contains_calls == []
    assert not _ran_checks(world)


def test_a_pass_on_another_head_reads_no_gain():
    record = {**_record(OLD), "head": "e" * 40}
    world = World(record)
    world.merge()
    assert world.ports.workspace.gain_reads == []
    assert _ran_checks(world)


def _no(world: World) -> None:
    world.ports.workspace.containment[(NEW, OLD)] = "no"


def _unknown(world: World) -> None:
    world.ports.workspace.containment[(NEW, OLD)] = "unknown"


def _unreadable(world: World) -> None:
    world.ports.workspace.gains[(OLD, NEW)] = GitError("git diff failed")


def _empty(world: World) -> None:
    world.ports.workspace.gains[(OLD, NEW)] = ()


def _stack_unreadable(world: World) -> None:
    world.ports.stack.declared_merge_inert = ()


def _touches_stack(world: World) -> None:
    world.ports.workspace.diff_paths = (".agentic/stack.yaml", "src/app.py")


def _touched_unknown(world: World) -> None:
    world.ports.workspace.diff_paths = ("src/app.py",)
    world.ports.stack.declared_setup = {"setup": "uv sync"}
    world.ports.workspace.gains[(OLD, NEW)] = ("uv.lock",)


def _broken_declaration(world: World) -> None:
    def broken(revision: str) -> tuple[str, ...]:
        raise ValueError("stack.yaml: '*' under `merge_inert` is refused")

    world.ports.stack.merge_inert = broken  # type: ignore[method-assign]


DOUBTS = [
    _no,
    _unknown,
    _unreadable,
    _empty,
    _stack_unreadable,
    _touches_stack,
    _touched_unknown,
    _broken_declaration,
]


@pytest.mark.parametrize("doubt", DOUBTS, ids=[d.__name__.strip("_") for d in DOUBTS])
def test_each_input_that_cannot_be_read_runs_the_suite(doubt):
    world = World(_record(OLD))
    doubt(world)
    world.merge()
    assert _ran_checks(world)
    assert "auto_merge_inert_from" not in world.doc()


def test_a_row_without_a_master_of_a_record_without_a_usable_one_runs_the_suite():
    world = World(_record(OLD, [_row(OLD), {"name": "e2e", "passed": True, "master": 7}]))
    world.merge()
    assert _ran_checks(world)
    assert world.ports.workspace.gain_reads == []


def test_an_old_record_takes_the_record_master_for_every_row():
    world = World(_record(OLD, [_row(None), _row(None, "e2e")]))
    world.merge()
    assert world.doc()["auto_merge_inert_from"] == [OLD]
    assert not _ran_checks(world)


def test_a_flaky_rerun_whose_kept_rows_ran_on_an_inert_older_master_merges():
    # The rerun ran on NEW and is stamped NEW; the kept row ran on OLD.
    record = _record(NEW, [_row(OLD, "tests"), _row(NEW, "e2e")])
    world = World(record)
    result = world.merge()
    assert result.kind == "merged"
    assert not _ran_checks(world)
    assert world.doc()["auto_merge_inert_from"] == [OLD]


def test_a_flaky_rerun_whose_kept_rows_ran_on_a_live_older_master_runs_one_full_suite():
    record = _record(NEW, [_row(OLD, "tests"), _row(NEW, "e2e")])
    world = World(record)
    world.ports.workspace.gains[(OLD, NEW)] = ("src/app.py",)
    world.merge()
    assert _ran_checks(world)
    assert world.doc()["merge_attempts"]["count"] == 1
    assert [row["master"] for row in world.doc()["merge_checks"]["checks"]] == [NEW]


def test_two_older_masters_are_each_read_once_and_every_gain_must_be_inert():
    record = _record(NEW, [_row(OLD, "tests"), _row(MID, "lint"), _row(NEW, "e2e")])
    world = World(record)
    world.ports.workspace.gains[(MID, NEW)] = (NOTE,)
    world.merge()
    assert sorted(world.ports.workspace.gain_reads) == [(OLD, NEW), (MID, NEW)]
    assert world.doc()["auto_merge_inert_from"] == [OLD, MID]
    assert not _ran_checks(world)


def test_the_gain_is_read_once_across_the_decision_before_and_after_the_suite():
    world = World(_record(OLD))
    world.ports.workspace.gains[(OLD, NEW)] = ("src/app.py",)
    world.merge()
    assert _ran_checks(world)
    assert world.ports.workspace.gain_reads == [(OLD, NEW)]


def test_the_last_look_at_master_still_refuses_a_covered_merge_when_master_moved_again():
    world = World(_record(OLD))
    world.ports.workspace.upstream_answer = Upstream("origin/master", MID, None)
    result = world.merge()
    assert (result.kind, result.reason) == ("refused", "master_moved")
    assert "auto_merge_head" not in world.doc()
