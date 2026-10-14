"""GH-212: what a failed merge-checks run shows -- the `merge_checks` record, the merge
child's log, the poller line and the `Auto-merge refused:` comment -- through
`merge_task` and `gather_merges` on in-memory ports."""

from __future__ import annotations

import dataclasses

import pytest

from agentic.core.process.application import landing_plan, merge, resume
from agentic.core.process.application.ports import CommandResult, GitError, Upstream
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _merges, _ports
from tests.fakes import MemoryCommands, MemoryStack

CHECKS = {"lint": "run lint", "tests": "run tests"}


@pytest.fixture(autouse=True)
def _no_live_landing_step(monkeypatch):
    """These tests pin the merge verb's refusal evidence, not the landing: no step is live,
    so a refusal stays the single comment it was. A test that wants one patches it itself."""
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())


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
    # GH-239: a `rerun` is the system's next step, so the human is not told it is theirs.
    assert "Left for the human to merge." not in comment


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
    assert _merges(other)[1] == []  # another head's record is no refusal: checks run again
    assert _merges(legacy)[1] == [old]


# -- the hand-off follows the landing step (GH-239, invariant 6) ---------------------

LEFT = "Left for the human to merge."


def _failing_world(tmp_path, landing=None):
    ports = _world(tmp_path, MemoryCommands(**{"run tests": [CommandResult(1, "FAILED x\n")]}))
    if landing is not None:
        ports.documents.record_closure("GH-5", {"landing": landing})
    return ports


def test_a_refusal_whose_step_is_none_is_left_to_the_human(tmp_path):
    spent = [{"head": HEAD, "step": "diagnose", "refusal": "merge_failed", "at": "t"}]
    ports = _failing_world(tmp_path, spent)
    _run(ports, tmp_path)
    [(_, comment)] = ports.tracker.comments
    assert comment.endswith(LEFT)


def test_a_refusal_with_a_step_is_never_left_to_the_human_and_names_the_step_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset({"rerun"}))
    ports = _failing_world(tmp_path)
    _run(ports, tmp_path)
    _run(ports, tmp_path)
    texts = [text for _, text in ports.tracker.comments]
    assert not any(LEFT in text for text in texts)
    assert [t for t in texts if t.startswith("Landing: ")] == [f"Landing: {HEAD} rerun"]


def test_a_refusal_with_an_unbuilt_step_posts_no_landing_comment_and_no_hand_off(tmp_path):
    ports = _failing_world(tmp_path)
    _run(ports, tmp_path)
    [(_, comment)] = ports.tracker.comments
    assert LEFT not in comment and not comment.startswith("Landing: ")


def test_landing_comments_read_as_the_systems_own_in_a_resume():
    assert "Landing: " in resume.SYSTEM_COMMENTS
    assert "Restarted by landing: " in resume.SYSTEM_COMMENTS


def test_a_merge_records_the_guarded_paths_it_landed_and_names_them_in_the_comment(tmp_path):
    ports = _world(tmp_path)
    ports.workspace.diff_paths = (".agentic/stack.yaml", ".agentic/tasks/GH-5.md", "src/a.py")
    result, _ = _run(ports, tmp_path)
    assert result.kind == "merged"
    assert (ports.documents.load("GH-5") or {})["auto_merge_guarded"] == [".agentic/stack.yaml"]
    [(_, comment)] = ports.tracker.comments
    assert comment.startswith("Auto-merged: ") and "- .agentic/stack.yaml" in comment
    assert "GH-5.md" not in comment


def test_a_merge_touching_nothing_guarded_records_no_guarded_key(tmp_path):
    ports = _world(tmp_path)
    ports.workspace.diff_paths = ("src/a.py",)
    result, _ = _run(ports, tmp_path)
    assert result.kind == "merged"
    assert "auto_merge_guarded" not in (ports.documents.load("GH-5") or {})


def test_the_landing_comment_is_not_posted_for_a_key_the_landing_list_already_holds(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset({"rerun"}))
    taken = [{"head": HEAD, "step": "rerun", "refusal": "checks_failed", "at": "t"}]
    ports = _failing_world(tmp_path, taken)
    _run(ports, tmp_path)
    assert not any(text.startswith("Landing: ") for _, text in ports.tracker.comments)
