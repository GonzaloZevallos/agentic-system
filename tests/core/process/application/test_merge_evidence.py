"""GH-212: what a failed merge-checks run shows -- the `merge_checks` record, the merge
child's log, the poller line and the `Auto-merge refused:` comment -- through
`merge_task` and `gather_merges` on in-memory ports."""

from __future__ import annotations

import dataclasses

import pytest

from agentic.core.process.application import merge
from agentic.core.process.application.ports import CommandResult, GitError, Upstream
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _merges, _ports
from tests.fakes import MemoryCommands, MemoryStack

CHECKS = {"lint": "run lint", "tests": "run tests"}


def _world(tmp_path, commands=None, stack=None):
    ports = _ports(5, checks=False)
    return dataclasses.replace(
        ports, stack=stack or MemoryStack(checks=CHECKS), commands=commands or MemoryCommands()
    )


def _run(ports, tmp_path):
    logged: list[str] = []
    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        logged.append,
    )
    return result, logged


def _record(ports):
    return (ports.documents.load("GH-5") or {})["merge_checks"]


def test_one_failing_check_is_named_with_its_tail_in_the_record_the_log_and_the_comment(tmp_path):
    ports = _world(tmp_path, MemoryCommands(**{"run tests": [CommandResult(1, "ran\nFAILED x\n")]}))

    result, logged = _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "checks_failed")
    record = _record(ports)
    assert record["passed"] is False and record["failed_step"] == "checks"
    assert record["checks"] == [
        {"name": "lint", "passed": True},
        {"name": "tests", "passed": False, "tail": ["ran", "FAILED x"]},
    ]
    assert any("Failed: tests" in line for line in logged)
    assert "    FAILED x" in logged
    [(_, comment)] = ports.tracker.comments
    assert comment.splitlines()[0] == f"Auto-merge refused: {HEAD} checks_failed"
    assert "Failed: tests" in comment and "```text\nran\nFAILED x\n```" in comment
    assert comment.endswith("Left for the human to merge.")


def test_two_runs_with_different_failing_checks_comment_twice_and_other_tails_once(tmp_path):
    ports = _world(tmp_path, MemoryCommands(**{"run tests": [CommandResult(1, "one")]}))
    _run(ports, tmp_path)
    ports.documents.record_closure("GH-5", {"merge_attempts": {"head": HEAD, "count": 0}})
    ports.documents.record_closure("GH-5", {"merge_checks": {"head": "x", "passed": True}})
    ports.commands.queues = {"run tests": [CommandResult(1, "another tail")]}
    _run(ports, tmp_path)
    assert len(ports.tracker.comments) == 1  # same failing checks, other tail: silent
    ports.documents.record_closure("GH-5", {"merge_attempts": {"head": HEAD, "count": 0}})
    ports.documents.record_closure("GH-5", {"merge_checks": {"head": "x", "passed": True}})
    ports.commands.queues = {
        "run lint": [CommandResult(1, "lint says")],
        "run tests": [CommandResult(1, "t")],
    }
    _run(ports, tmp_path)

    summaries = [text.splitlines()[2] for _, text in ports.tracker.comments]
    assert summaries == ["Failed: tests", "Failed: lint, tests"]


def test_a_conflict_a_setup_step_and_a_worktree_error_each_record_where_it_stopped(
    tmp_path, monkeypatch
):
    conflict = _world(tmp_path)
    conflict.workspace.conflicts.add(MASTER)
    _run(conflict, tmp_path)
    assert _record(conflict)["failed_step"] == "conflict"

    setup = _world(
        tmp_path,
        MemoryCommands(**{"npm i": [CommandResult(1, "\x1b[31mE404\x1b[0m")]}),
        MemoryStack(checks=CHECKS, setup={"install": "npm i", "later": "never"}),
    )
    _, logged = _run(setup, tmp_path)
    record = _record(setup)
    assert record["failed_step"] == "setup" and record["passed"] is False
    assert record["checks"] == [{"name": "install", "passed": False, "tail": ["E404"]}]
    assert [command for _, command in setup.commands.ran] == ["npm i"]  # stops at the first
    assert any("Failed at setup: install" in line for line in logged)

    broken = _world(tmp_path)

    def raising(name, sha):
        raise GitError("no space")

    monkeypatch.setattr(broken.workspace, "detached_checkout", raising)
    _run(broken, tmp_path)
    assert _record(broken)["failed_step"] == "worktree"


def test_a_runner_that_raises_oserror_writes_no_merge_checks(tmp_path, monkeypatch):
    ports = _world(tmp_path)

    def raising(cwd, command, timeout_s):
        raise OSError("boom")

    monkeypatch.setattr(ports.commands, "run", raising)
    with pytest.raises(OSError):
        _run(ports, tmp_path)

    assert "merge_checks" not in (ports.documents.load("GH-5") or {})
    assert ports.workspace.removed_checkouts  # the worktree went anyway


def _failed_record(**more):
    return {
        "head": HEAD,
        "master": MASTER,
        "passed": False,
        "duration_s": 1.0,
        "failed_step": "checks",
        "checks": [{"name": "tests", "passed": False, "tail": ["boom"]}],
        **more,
    }


def test_the_poller_line_names_the_failing_checks_only_for_this_head_and_new_documents():
    ports = _ports(5, checks=False)
    ports.documents.record_closure("GH-5", {"merge_checks": _failed_record()})
    assert _merges(ports)[1] == [
        "agentic: poll: auto-merge #5 -> left to the human: checks_failed (Failed: tests)"
    ]
    other = _ports(5, checks=False)
    other.documents.record_closure("GH-5", {"merge_checks": _failed_record(head="c" * 40)})
    legacy = _ports(5, checks=False)
    legacy.documents.record_closure(
        "GH-5", {"merge_checks": {"head": HEAD, "master": MASTER, "passed": False}}
    )
    old = "agentic: poll: auto-merge #5 -> left to the human: checks_failed"
    assert _merges(other)[1] == [old]
    assert _merges(legacy)[1] == [old]
