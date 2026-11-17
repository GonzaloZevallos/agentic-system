"""GH-876: the `merge` verb reads a stacked child's parent through `stack_facts` with one
`StackMemo` per `merge_task` call, and comments an orphaned child once per (head, reason). All
through `merge.merge_task` on in-memory ports, with counting wrappers on the three reads a
stacking fact costs (`issue_state`, `fetch_commit`, `is_ancestor`)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.application import merge
from agentic.core.process.application.merge import MergeResult
from agentic.core.process.application.ports import IssueState, TrackerCallFailed, Upstream
from agentic.core.process.application.refusal_note import STACK_EVIDENCE
from agentic.core.process.domain.task import StackParent
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _ports

PARENT = "c" * 40
CLOSING = "f" * 40
MOVED = "e" * 40
NEW_HEAD = "d" * 40
ISSUE = 9
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def _record() -> StackParent:
    return StackParent(
        issue=ISSUE, task_id=f"GH-{ISSUE}", head=PARENT, started_at="2026-11-16T10:00:00"
    )


class _Calls:
    """What each of the three stacking reads was asked, in order."""

    def __init__(self, ports) -> None:
        self.issue_state: list[int] = []
        self.fetch_commit: list[str] = []
        self.is_ancestor: list[tuple[str, str]] = []
        tracker, workspace = ports.tracker, ports.workspace
        issue_state, fetch, ancestor = (
            tracker.issue_state,
            workspace.fetch_commit,
            workspace.is_ancestor,
        )

        def counted_issue(number: int):
            self.issue_state.append(number)
            return issue_state(number)

        def counted_fetch(sha: str) -> bool:
            self.fetch_commit.append(sha)
            return fetch(sha)

        def counted_ancestor(path: Path, sha: str, ref: str = "HEAD") -> bool:
            self.is_ancestor.append((sha, ref))
            return ancestor(path, sha, ref)

        tracker.issue_state = counted_issue
        workspace.fetch_commit = counted_fetch
        workspace.is_ancestor = counted_ancestor


def _world(checks: bool = True, **more):
    """A ready, approved task (with green recorded checks on `HEAD` and `MASTER` unless `checks`
    is False); a stacked one names an open parent issue that has not reached master."""
    ports = _ports(5, checks=checks, **more)
    ports.tracker.states[ISSUE] = IssueState("OPEN", "", (), None)
    return ports


def _stacked(checks: bool = True):
    return _world(checks, stack_parent=_record())


def _call(ports, tmp_path, master: str = MASTER) -> MergeResult:
    return merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", master, None),
        frozenset(),
        lambda _line: None,
        lambda: NOW.isoformat(),
    )


def _refusals(ports) -> list[str]:
    return [text for _, text in ports.tracker.comments if text.startswith("Auto-merge refused")]


def _orphan(ports) -> None:
    """The parent closed without its head: its closing merge excludes the recorded head."""
    ports.tracker.states[ISSUE] = IssueState("CLOSED", "COMPLETED", (), CLOSING)
    ports.workspace.ancestors_by_ref[CLOSING] = set()


def _landed(ports) -> None:
    """The parent's head is in master and in the child's head."""
    ports.workspace.ancestors_by_ref[MASTER] = {PARENT}
    ports.workspace.ancestors_by_ref[HEAD] = {PARENT}


def test_a_document_without_the_record_makes_no_stacking_read(monkeypatch, tmp_path):
    def refuse(*_args, **_kwargs):
        raise AssertionError("stack_facts was called for a document with no stack_parent")

    ports = _world()
    calls = _Calls(ports)
    monkeypatch.setattr(merge, "stack_facts", refuse)

    result = _call(ports, tmp_path)

    assert result.kind == "merged"
    assert calls.issue_state == []
    assert calls.is_ancestor == []
    # The verb's own fetch of the pull request's head: nothing else asked for an object.
    assert calls.fetch_commit == [HEAD]


@pytest.mark.parametrize("failing", ["issue_state", "parent_missing", "git_error"])
def test_a_read_that_fails_never_merges(failing, tmp_path):
    ports = _stacked()
    if failing == "issue_state":

        def broken(number: int):
            raise TrackerCallFailed("gh issue view failed")

        ports.tracker.issue_state = broken
    elif failing == "parent_missing":
        ports.workspace.fetch_commit = lambda sha: sha != PARENT
    else:
        ports.workspace.ancestor_errors.add(PARENT)

    result = _call(ports, tmp_path)

    assert result == MergeResult("refused", "stack_parent_unmerged")
    assert ports.tracker.merge_calls == []
    assert _refusals(ports) == []  # not a refusal a human can act on: never commented


def test_an_unreadable_record_is_orphaned_with_no_read_and_one_note(tmp_path):
    ports = _world(stack_parent=None)
    calls = _Calls(ports)

    result = _call(ports, tmp_path)

    assert result == MergeResult("refused", "stack_base_orphaned")
    assert ports.tracker.merge_calls == []
    assert (calls.issue_state, calls.is_ancestor) == ([], [])
    assert _refusals(ports) == [
        f"Auto-merge refused: {HEAD} stack_base_orphaned\n\n"
        f"{STACK_EVIDENCE['stack_base_orphaned']}\n\nLeft for the human to merge."
    ]


def test_an_orphaned_child_is_commented_once_per_head(tmp_path):
    ports = _stacked()
    _orphan(ports)

    first = _call(ports, tmp_path)
    again = _call(ports, tmp_path, master=MOVED)  # master moved, the head did not

    assert first == again == MergeResult("refused", "stack_base_orphaned")
    assert ports.tracker.merge_calls == []
    assert len(_refusals(ports)) == 1
    assert STACK_EVIDENCE["stack_base_orphaned"] in _refusals(ports)[0]

    ports.tracker.head_oid = NEW_HEAD
    document = ports.documents.load("GH-5") or {}
    document["subtasks"]["only"]["reviewed_head"] = NEW_HEAD
    ports.documents.save(document)
    moved_head = _call(ports, tmp_path, master=MOVED)

    assert moved_head == MergeResult("refused", "stack_base_orphaned")
    assert [text.splitlines()[0] for text in _refusals(ports)] == [
        f"Auto-merge refused: {HEAD} stack_base_orphaned",
        f"Auto-merge refused: {NEW_HEAD} stack_base_orphaned",
    ]


def test_a_landed_parent_the_child_holds_merges(tmp_path):
    ports = _stacked()
    _landed(ports)

    assert _call(ports, tmp_path).kind == "merged"
    assert len(ports.tracker.merge_calls) == 1


def test_the_re_decision_after_the_checks_reads_nothing_again(monkeypatch, tmp_path):
    """A landed parent leaves the decision at `run_checks`, so `decide` runs twice in one call:
    the second run asks the same memo, and git is asked once per question."""
    ports = _stacked(checks=False)
    _landed(ports)
    calls = _Calls(ports)
    ran: list[str] = []

    def checked(task_id, raw, head, master, base, cfg, ports, attempt, log, only=None):
        ran.append(head)
        ports.documents.record_closure(
            task_id, {"merge_checks": {"head": head, "master": master, "passed": True}}
        )

    monkeypatch.setattr(merge, "run_checks", checked)

    result = _call(ports, tmp_path)

    assert ran == [HEAD]  # decide ran twice
    assert result.kind == "merged"
    assert calls.is_ancestor == [(PARENT, MASTER), (PARENT, HEAD)]
    assert calls.issue_state == []
    assert calls.fetch_commit == [HEAD, HEAD, PARENT]


def test_a_stacked_call_costs_a_constant_number_of_reads(tmp_path):
    baseline = _world()
    baseline_calls = _Calls(baseline)
    _call(baseline, tmp_path)

    ports = _stacked()
    _orphan(ports)
    calls = _Calls(ports)
    _call(ports, tmp_path)

    assert len(calls.issue_state) <= 1
    assert len(calls.fetch_commit) - len(baseline_calls.fetch_commit) <= 3
    assert len(calls.is_ancestor) <= 3
